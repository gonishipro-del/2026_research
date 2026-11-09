"""段階5: バランス診断と、マッチングの凍結。

標準化平均差（SMD）をマッチング前後で出す。SMD の分母は、マッチング前の処置群と対照群の
プールした標準偏差。復元ありの対照は、使われた回数を重みにする。
段階5が完了すると「マッチングを確定（凍結）」できる。凍結するまで段階6は実行できない。

出力（output/run_*/stage5_freeze/）:
  balance.csv        共変量ごとの SMD（マッチング前後、主分析の設定）
  balance_grid.csv   設定ごとの SMD の最大値
  overlap.csv        傾向スコアの分布（群ごとの分位点）
  love.png           Love plot（SMD、目安 0.1）
  frozen.json        凍結の記録（凍結時刻、設定、マッチ結果のハッシュ）

単体実行: python -m core.balance [--run RUN_ID]          （診断）
          python -m core.balance --freeze [--run RUN_ID]  （凍結）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import config
from core import matcher, propensity, runs, stage_runner, stages, state
from core.progress import Progress

SMD_TARGET = 0.1


def smd(treated: pd.Series, control: pd.Series, control_weight: pd.Series | None = None,
        pooled_sd: float | None = None) -> float:
    """標準化平均差。control_weight があれば対照の平均を重み付きにする。"""
    w = np.ones(len(control)) if control_weight is None else control_weight.to_numpy(dtype=float)
    mean_c = np.average(control.to_numpy(dtype=float), weights=w)
    if pooled_sd is None:
        var_t, var_c = treated.var(ddof=1), control.var(ddof=1)
        pooled_sd = np.sqrt((var_t + var_c) / 2)
    if not pooled_sd or np.isnan(pooled_sd):
        return float("nan")
    return float((treated.mean() - mean_c) / pooled_sd)


def matched_weights(pairs: pd.DataFrame, frame: pd.DataFrame) -> tuple[pd.Index, pd.Series, pd.Series]:
    """(マッチした処置群の証券コード, 対照群の重み, 処置群の重み)。"""
    treated_codes = pd.Index(pairs["treated"].unique())
    control_weight = pairs["control"].value_counts().reindex(frame.index[frame["ai_flag"] == 0]).fillna(0)
    return treated_codes, control_weight, pd.Series(1.0, index=treated_codes)


def smd_table(frame: pd.DataFrame, pairs: pd.DataFrame, covariates: list[str]) -> pd.DataFrame:
    treated_all = frame[frame["ai_flag"] == 1]
    control_all = frame[frame["ai_flag"] == 0]
    treated_codes, control_weight, _ = matched_weights(pairs, frame)
    rows = []
    for column in covariates:
        pooled = np.sqrt((treated_all[column].var(ddof=1) + control_all[column].var(ddof=1)) / 2)
        before = smd(treated_all[column], control_all[column], pooled_sd=pooled)
        after = smd(frame.loc[treated_codes, column], control_all[column],
                    control_weight=control_weight.reindex(control_all.index), pooled_sd=pooled)
        rows.append({"covariate": column, "smd_before": before, "smd_after": after,
                     "mean_treated_after": frame.loc[treated_codes, column].mean(),
                     "mean_control_before": control_all[column].mean(),
                     "mean_control_after": np.average(control_all[column], weights=control_weight.reindex(control_all.index))})
    return pd.DataFrame(rows)


def max_abs_smd(frame: pd.DataFrame, pairs: pd.DataFrame, covariates: list[str]) -> float:
    if pairs.empty:
        return float("nan")
    table = smd_table(frame, pairs, covariates)
    return float(table["smd_after"].abs().max())


def _pairs_for(run_id: str, name: str) -> pd.DataFrame:
    return pd.read_csv(runs.stage_dir(run_id, stages.MATCHING) / "matches" / f"{name}.csv",
                       dtype={"treated": str, "control": str})


def run(run_id: str, progress: Progress) -> stage_runner.StageResult:
    settings = runs.run_config(run_id)
    covariates = settings["covariates"]
    frame = propensity.load_analysis(run_id, covariates)
    main = propensity.config_name(settings["replacement_main"], settings["caliper_sd"])
    pairs = _pairs_for(run_id, main)
    out = runs.stage_dir(run_id, stages.FREEZE)

    table = smd_table(frame, pairs, covariates)
    table.to_csv(out / "balance.csv", index=False, encoding="utf-8-sig")

    grid_rows = []
    for replacement, caliper_sd in propensity.matching_grid(settings):
        name = propensity.config_name(replacement, caliper_sd)
        other = _pairs_for(run_id, name)
        grid_rows.append({"config": name, "pairs": len(other),
                          "max_abs_smd_after": max_abs_smd(frame, other, covariates)})
    pd.DataFrame(grid_rows).to_csv(out / "balance_grid.csv", index=False, encoding="utf-8-sig")

    ps = pd.read_csv(runs.stage_dir(run_id, stages.MATCHING) / "propensity.csv", dtype={"code": str}, index_col=0)
    ps.index = ps.index.astype(str)
    treated_ps = ps.loc[ps["ai_flag"] == 1, "ps"]
    control_ps = ps.loc[ps["ai_flag"] == 0, "ps"]
    overlap = pd.DataFrame({"処置群": treated_ps.quantile([0, 0.25, 0.5, 0.75, 1]),
                            "対照群": control_ps.quantile([0, 0.25, 0.5, 0.75, 1])})
    overlap.to_csv(out / "overlap.csv", encoding="utf-8-sig")
    _love_plot(table, out / "love.png")

    dropped = pd.read_csv(runs.stage_dir(run_id, stages.MATCHING) / "dropped_treated.csv", dtype=str)
    worst = table.loc[table["smd_after"].abs().idxmax()]
    message = (f"SMD最大（マッチ後）{worst['smd_after']:.3f}（{worst['covariate']}）。"
               f"脱落した処置群{len(dropped)}社")
    if abs(worst["smd_after"]) > SMD_TARGET:
        # 目安を超えても診断は完了とする（ユーザー指示で要確認を通過して推定を進める）。超過はメッセージに残す
        message += f"。目安 {SMD_TARGET} を超える変数があります（結果に注記）"
    return stage_runner.StageResult(stages.DONE, message)


def _love_plot(table: pd.DataFrame, path: Path) -> None:
    import altair as alt

    long = table.melt(id_vars="covariate", value_vars=["smd_before", "smd_after"], var_name="when", value_name="smd")
    long["when"] = long["when"].map({"smd_before": "マッチング前", "smd_after": "マッチング後"})
    points = alt.Chart(long).mark_point(size=90, filled=True).encode(
        x=alt.X("smd:Q", title="標準化平均差（SMD）", scale=alt.Scale(domain=[-1, 1])),
        y=alt.Y("covariate:N", sort=None, title=None),
        color=alt.Color("when:N", scale=alt.Scale(range=["#898781", "#2a78d6"]), title=None),
        tooltip=["covariate", "when", alt.Tooltip("smd:Q", format=".3f")],
    )
    guides = alt.Chart(pd.DataFrame({"x": [-SMD_TARGET, 0, SMD_TARGET]})).mark_rule(
        strokeDash=[4, 4], color="#c3c2b7").encode(x="x:Q")
    chart = (guides + points).properties(width=460, height=200,
                                         title="Love plot（目安 ±0.1。試運転の結果であり、最終結果ではない）")
    _save_png(chart, path)


def _save_png(chart, path: Path) -> None:
    import vl_convert as vlc

    path.write_bytes(vlc.vegalite_to_png(chart.to_json(), scale=2))


def freeze(run_id: str) -> dict:
    """マッチングを凍結する。段階5が完了していることが必要。"""
    settings = runs.run_config(run_id)
    if runs.stage_state(run_id, stages.FREEZE)["status"] not in (stages.DONE, stages.REVIEW):
        raise RuntimeError("段階5（バランス診断）を先に実行してください")
    main = propensity.config_name(settings["replacement_main"], settings["caliper_sd"])
    matches_path = runs.stage_dir(run_id, stages.MATCHING) / "matched_pairs.csv"
    digest = hashlib.sha256(matches_path.read_bytes()).hexdigest()
    record = {
        "at": state.now(), "config": main, "replacement": settings["replacement_main"],
        "caliper_sd": settings["caliper_sd"], "covariates": settings["covariates"], "seed": settings["seed"],
        "matched_pairs_sha256": digest, "settings_sha256": hashlib.sha256(
            json.dumps(settings, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    state.write_json_atomic(runs.stage_dir(run_id, stages.FREEZE) / "frozen.json", record)

    def mutate(current: dict) -> None:
        current["runs"][run_id]["frozen"] = record
    state.update(mutate)
    runs.log_event(run_id, "マッチングの凍結", stage=stages.FREEZE, detail=f"主分析={main} 復元={settings['replacement_main']}")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="段階5: バランス診断・凍結")
    parser.add_argument("--run")
    parser.add_argument("--freeze", action="store_true", help="マッチングを凍結する")
    args = parser.parse_args()
    run_id = args.run or runs.current_run()
    if args.freeze:
        record = freeze(run_id)
        print(f"凍結しました: {record['config']} ({record['at']})")
        return 0
    return stage_runner.cli(stages.FREEZE, ["--run", run_id])


if __name__ == "__main__":
    sys.exit(main())
