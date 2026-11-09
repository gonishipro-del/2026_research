"""段階4: 傾向スコアの推定とマッチング（1対1・最近傍・キャリパー）。

距離はロジットの線形予測子。キャリパーは線形予測子の標準偏差 × caliper_sd。
復元あり・なしの両方を、キャリパーの感度分析とあわせて計算する。処理順と同点の扱いは seed で固定する。
段階5で凍結した設定が主分析になる。

出力（output/run_*/stage4_matching/）:
  ps_model.json          係数・標準誤差・疑似R²・収束・警告
  propensity.csv         企業ごとの ai_flag・線形予測子・傾向スコア
  matches/{設定}.csv      設定ごとのペア
  dropped/{設定}.csv      キャリパー内に対照がなく脱落した処置群
  matching_grid.csv      設定ごとのペア数・脱落数・SMD の最大値
  matched_pairs.csv      主分析の設定のペア（段階5で凍結したもの）
  dropped_treated.csv    主分析の設定で脱落した処置群

単体実行: python -m core.matcher [--run RUN_ID]
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from core import balance, propensity, runs, stage_runner, stages
from core.progress import Progress


def match(lp: pd.Series, treated: pd.Series, *, replacement: bool, caliper: float, seed: int) -> tuple[pd.DataFrame, list[str]]:
    """最近傍マッチング。(ペアの表, 脱落した処置群の証券コード) を返す。

    処置群を乱数で並べ替えて順に対照を選ぶ（復元なしの場合は貪欲法）。同点は並べ替え後の先頭を採る。
    """
    rng = np.random.default_rng(seed)
    treated_codes = list(lp.index[treated.astype(bool)])
    control_codes = list(lp.index[~treated.astype(bool)])
    rng.shuffle(treated_codes)
    rng.shuffle(control_codes)

    used: set[str] = set()
    pairs, dropped = [], []
    for code in treated_codes:
        available = [c for c in control_codes if replacement or c not in used]
        if not available:
            dropped.append(code)
            continue
        distance = np.abs(lp[available].to_numpy() - lp[code])
        within = distance <= caliper
        if not within.any():
            dropped.append(code)
            continue
        nearest = int(np.argmin(np.where(within, distance, np.inf)))
        control = available[nearest]
        used.add(control)
        pairs.append({"treated": code, "control": control, "lp_treated": lp[code],
                      "lp_control": lp[control], "distance": distance[nearest]})
    return pd.DataFrame(pairs, columns=["treated", "control", "lp_treated", "lp_control", "distance"]), dropped


def run(run_id: str, progress: Progress) -> stage_runner.StageResult:
    settings = runs.run_config(run_id)
    covariates = settings["covariates"]
    frame = propensity.load_analysis(run_id, covariates)
    if frame["ai_flag"].nunique() < 2:
        return stage_runner.StageResult(stages.FAILED, "処置群と対照群の両方が必要です")

    model = propensity.fit(frame, covariates)
    progress.log(f"ロジット: n={model['n']} 処置={model['n_treated']} 対照={model['n_control']} "
                 f"疑似R²={model['pseudo_r2']:.3f} 収束={model['converged']}")
    for note in model["warnings"]:
        progress.log(f"[警告] {note}")

    out = runs.stage_dir(run_id, stages.MATCHING)
    (out / "matches").mkdir(exist_ok=True)
    (out / "dropped").mkdir(exist_ok=True)
    lp = model["lp"]
    sd = float(lp.std(ddof=1))
    state_json = {k: v for k, v in model.items() if k not in ("lp", "ps")}
    state_json.update(caliper_sd_of_lp=sd, covariates=covariates)
    (out / "ps_model.json").write_text(json.dumps(state_json, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame({"ai_flag": frame["ai_flag"], "lp": lp, "ps": model["ps"]}).to_csv(
        out / "propensity.csv", encoding="utf-8-sig")

    main = propensity.config_name(settings["replacement_main"], settings["caliper_sd"])
    grid_rows = []
    for replacement, caliper_sd in propensity.matching_grid(settings):
        name = propensity.config_name(replacement, caliper_sd)
        pairs, dropped = match(lp, frame["ai_flag"], replacement=replacement, caliper=caliper_sd * sd,
                               seed=settings["seed"])
        pairs.to_csv(out / "matches" / f"{name}.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame({"treated": dropped}).to_csv(out / "dropped" / f"{name}.csv", index=False, encoding="utf-8-sig")
        smd = balance.max_abs_smd(frame, pairs, covariates)
        grid_rows.append({"config": name, "replacement": int(replacement), "caliper_sd": caliper_sd,
                          "caliper_abs": caliper_sd * sd, "pairs": len(pairs), "treated_matched": pairs["treated"].nunique(),
                          "treated_dropped": len(dropped), "controls_used": pairs["control"].nunique(),
                          "max_abs_smd_after": smd, "main": int(name == main)})
        progress.log(f"{name}: ペア{len(pairs)} 脱落{len(dropped)} SMD最大{smd:.3f}")
    grid = pd.DataFrame(grid_rows)
    grid.to_csv(out / "matching_grid.csv", index=False, encoding="utf-8-sig")

    pd.read_csv(out / "matches" / f"{main}.csv").to_csv(out / "matched_pairs.csv", index=False, encoding="utf-8-sig")
    pd.read_csv(out / "dropped" / f"{main}.csv").to_csv(out / "dropped_treated.csv", index=False, encoding="utf-8-sig")

    main_row = grid[grid["config"] == main].iloc[0]
    message = (f"主分析 {main}: ペア{int(main_row['pairs'])} / 脱落{int(main_row['treated_dropped'])}社 / "
               f"SMD最大{main_row['max_abs_smd_after']:.3f}")
    if model["warnings"] or not model["converged"]:
        return stage_runner.StageResult(stages.REVIEW, message + "。ロジットに警告があります（ログ参照）")
    if main_row["pairs"] < settings["min_group_size"]:
        return stage_runner.StageResult(stages.REVIEW, message + f"。ペアが{settings['min_group_size']}組未満です")
    return stage_runner.StageResult(stages.DONE, message)


if __name__ == "__main__":
    raise SystemExit(stage_runner.cli(stages.MATCHING))
