"""段階7: 処置群の平均処置効果（ATT）の推定。

ペアごとの CAR 差（処置群の CAR − 対照群の CAR）の平均を ATT とする。
復元ありでは、処置企業ごとに、マッチした対照の CAR の平均を使う（処置企業を1単位とする）。
検定は、対応のある t 検定、Wilcoxon 符号順位検定、処置企業単位のブートストラップ（seed 固定）による
百分位信頼区間。ブートストラップは最近傍マッチの標準誤差の近似であることに注意する。

出力（output/run_*/stage7_att/）:
  att_summary.json        窓ごとの ATT、標準誤差、95%信頼区間、p 値、件数
  pairs_{窓}.csv           窓ごとのペアの CAR 差
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy import stats

from core import propensity, runs, stage_runner, stages
from core.car import window_label
from core.progress import Progress

BOOTSTRAP_SEED_OFFSET = 1


def pair_differences(pairs: pd.DataFrame, car: pd.DataFrame, column: str) -> pd.DataFrame:
    """処置企業ごとの CAR 差。対照が複数ある場合は、対照の CAR の平均と比べる。"""
    values = car.set_index("code")[column].astype(float)
    pairs = pairs.assign(car_c=pairs["control"].map(values), car_t=pairs["treated"].map(values)).dropna(subset=["car_c", "car_t"])
    grouped = pairs.groupby("treated").agg(car_t=("car_t", "first"), car_c=("car_c", "mean"),
                                           n_controls=("control", "count"))
    grouped["diff"] = grouped["car_t"] - grouped["car_c"]
    return grouped.reset_index()


def bootstrap_ci(diffs: np.ndarray, reps: int, seed: int) -> tuple[float, float, float]:
    """処置企業単位の百分位ブートストラップ。(標準誤差, 2.5%点, 97.5%点)。"""
    rng = np.random.default_rng(seed)
    n = len(diffs)
    draws = diffs[rng.integers(0, n, size=(reps, n))].mean(axis=1)
    low, high = np.percentile(draws, [2.5, 97.5])
    return float(draws.std(ddof=1)), float(low), float(high)


def summarize(diffs: np.ndarray, reps: int, seed: int) -> dict:
    n = int(len(diffs))
    if n < 2:
        return {"n": n, "att": float(diffs.mean()) if n else float("nan"), "warnings": ["ペアが2組未満のため検定できない"]}
    att = float(diffs.mean())
    se_t = float(diffs.std(ddof=1) / np.sqrt(n))
    t_p = float(stats.ttest_1samp(diffs, 0.0).pvalue)
    try:
        wilcoxon_p = float(stats.wilcoxon(diffs).pvalue)
    except ValueError:
        wilcoxon_p = float("nan")
    se_boot, ci_low, ci_high = bootstrap_ci(diffs, reps, seed)
    warnings = []
    if n < 20:
        warnings.append(f"サンプルが小さい（{n}組）。信頼区間の幅が大きいことに注意")
    return {"n": n, "att": att, "se_t": se_t, "p_t": t_p, "p_wilcoxon": wilcoxon_p,
            "se_bootstrap": se_boot, "ci95": [ci_low, ci_high], "warnings": warnings}


def run(run_id: str, progress: Progress) -> stage_runner.StageResult:
    settings = runs.run_config(run_id)
    frozen = runs.run_info(run_id).get("frozen")
    if not frozen:
        raise RuntimeError("マッチングが凍結されていません")
    main = frozen["config"]
    pairs = pd.read_csv(runs.stage_dir(run_id, stages.MATCHING) / "matches" / f"{main}.csv", dtype=str)
    pairs["distance"] = pairs["distance"].astype(float)
    car = pd.read_csv(runs.stage_dir(run_id, stages.CAR) / "car.csv", dtype={"code": str})
    out = runs.stage_dir(run_id, stages.ATT)

    windows = {window_label(tuple(w)): tuple(w) for w in settings["event_windows"]}
    windows["placebo"] = tuple(settings["placebo_window"])
    summary = {"config": main, "bootstrap_reps": settings["bootstrap_reps"], "windows": {}}
    messages = []
    for name, window in windows.items():
        column = f"car_{name}"
        diffs_table = pair_differences(pairs, car, column)
        diffs_table.to_csv(out / f"pairs_{name}.csv", index=False, encoding="utf-8-sig")
        if diffs_table.empty:
            summary["windows"][name] = {"n": 0, "warnings": ["CAR が計算できたペアがない"]}
            continue
        stats_row = summarize(diffs_table["diff"].to_numpy(), settings["bootstrap_reps"],
                              settings["seed"] + BOOTSTRAP_SEED_OFFSET)
        summary["windows"][name] = {"window": list(window), **stats_row}
        if name == window_label(tuple(settings["event_window_main"])):
            messages.append(f"主分析{name}: ATT {stats_row['att']:+.4f} "
                            f"95%CI [{stats_row['ci95'][0]:+.4f}, {stats_row['ci95'][1]:+.4f}] "
                            f"n={stats_row['n']}")
    (out / "att_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    message = messages[0] if messages else "主分析の窓を計算できない"
    if not messages or summary["windows"].get(window_label(tuple(settings["event_window_main"])), {}).get("warnings"):
        return stage_runner.StageResult(stages.REVIEW, message + "。結果に注記が必要（att_summary.json）")
    return stage_runner.StageResult(stages.DONE, message)


if __name__ == "__main__":
    raise SystemExit(stage_runner.cli(stages.ATT))
