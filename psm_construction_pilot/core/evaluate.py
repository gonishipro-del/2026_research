"""段階8: 評価（頑健性・感度）。

以下の代替推定を、主分析（段階7）と同じ軸に並べて示す。
- 窓の違い（1日・3日・30日・60日・90日）
- 復元あり・なし、キャリパー（config.caliper_grid）の感度分析。マッチ数と SMD の最大値を併記
- プラセボ検定（発表前の無関係な窓 placebo_window の ATT は 0 に近いはず）
- Rosenbaum bounds（復元なし・1対1のペアの窓の CAR 差）。仮定は README に明記
- 期待収益率モデルの比較：市場モデル（株価・指数とも円建てのまま。FF5 のドル換算との比較になる）。ベンチマークは TOPIX が理想だが、J-Quants の APIキーが
  環境変数に無いため、日経平均（^N225）で代替する。ファクターが米ドル建てであることの影響を確認する検証

出力（output/run_*/stage8_evaluate/）:
  sensitivity.csv      代替推定の一覧（ATT、信頼区間、件数）
  grid.csv             マッチングの設定ごとの ATT、マッチ数、SMD の最大値
  rosenbaum.csv        Γ ごとの p 値の上限・下限
  market_model.json    市場モデル（日経平均）の ATT
  forest.png           フォレストプロット
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

import config
from core import att, car, factors, inputs, prices, propensity, runs, stage_runner, stages
from core.car import window_label
from core.progress import Progress

MARKET_TICKER = "^N225"
MARKET_LABEL = "日経平均（^N225）"
MARKET_CACHE = config.PRICE_CACHE_DIR / "_N225.json"


def market_closes(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """日経平均の終値（キャッシュ付き）。"""
    if MARKET_CACHE.exists():
        cached = json.loads(MARKET_CACHE.read_text(encoding="utf-8"))
        if cached["start"] <= start.date().isoformat():
            return pd.Series(cached["closes"], dtype=float).set_axis(pd.to_datetime(list(cached["closes"]))).sort_index()
    history = yf.Ticker(MARKET_TICKER).history(start=(start - pd.Timedelta(days=5)).date().isoformat(),
                                                end=(end + pd.Timedelta(days=1)).date().isoformat(), auto_adjust=False)
    closes = {index.strftime("%Y-%m-%d"): float(value) for index, value in history["Close"].items()}
    MARKET_CACHE.parent.mkdir(parents=True, exist_ok=True)
    MARKET_CACHE.write_text(json.dumps({"start": (start - pd.Timedelta(days=5)).date().isoformat(), "closes": closes}),
                            encoding="utf-8")
    return pd.Series(closes, dtype=float).set_axis(pd.to_datetime(list(closes))).sort_index()


def rosenbaum_bounds(diffs: np.ndarray, gammas: list[float]) -> list[dict]:
    """Wilcoxon 符号順位検定の Rosenbaum bounds（正規近似）。

    ATT の符号の方向に検定する。Γ の下で、各ペアの「効果の方向の符号」が出る確率は
    [1/(1+Γ), Γ/(1+Γ)] の範囲に入る。上限の p 値は Γ/(1+Γ)、下限の p 値は 1/(1+Γ) で計算する。
    """
    sign = 1.0 if diffs.mean() >= 0 else -1.0
    x = diffs[diffs != 0] * sign
    ranks = stats.rankdata(np.abs(x))
    positive = x > 0
    t_obs = float(ranks[positive].sum())
    rows = []
    for gamma in gammas:
        row = {"gamma": gamma}
        for label, p in (("p_upper", gamma / (1 + gamma)), ("p_lower", 1 / (1 + gamma))):
            mean = p * ranks.sum()
            var = p * (1 - p) * (ranks ** 2).sum()
            row[label] = float(1 - stats.norm.cdf((t_obs - mean - 0.5) / np.sqrt(var)))
        rows.append(row)
    return rows


def market_model_att(run_id: str, codes: list[str], settings: dict, pairs: pd.DataFrame,
                     factor_table: pd.DataFrame, targets: pd.DataFrame, progress: Progress) -> dict:
    factor_dates = factor_table.index
    closes = market_closes(factor_dates[0], factor_dates[-1])
    market_calendar = factor_dates[factor_dates.isin(closes.index)]
    rf = factor_table["RF"]
    market_excess, _ = car.daily_excess_returns(closes, market_calendar, rf)
    market_X = pd.DataFrame({"MKT": market_excess})
    windows = {window_label(tuple(w)): tuple(w) for w in settings["event_windows"]}
    windows["placebo"] = tuple(settings["placebo_window"])
    main = window_label(tuple(settings["event_window_main"]))

    rows = []
    for code in codes:
        announce = pd.Timestamp(targets.loc[code, "announce_date"])
        close = prices.close_series(prices.load_series(code, announce.date()))
        common, _ = car.matched_calendar(close, market_calendar)
        excess, _ = car.daily_excess_returns(close, common, rf)
        result = car.abnormal_car(excess, common, announce, market_X, windows,
                                  include_alpha=settings["include_alpha"], min_obs=settings["min_estimation_obs"])
        rows.append({"code": code, **{name: result.get(name, np.nan) for name in windows}})
    table = pd.DataFrame(rows)
    table.to_csv(runs.stage_dir(run_id, stages.EVALUATE) / "market_car.csv", index=False, encoding="utf-8-sig")
    pair_table = att.pair_differences(pairs, table.assign(code=table["code"]), main)
    return {"benchmark": MARKET_LABEL, "n_market_days": int(len(market_calendar)),
            "stats": att.summarize(pair_table["diff"].to_numpy(), settings["bootstrap_reps"], settings["seed"] + 2)
            if len(pair_table) else {"n": 0, "warnings": ["CARが計算できない"]}}


def forest(rows: pd.DataFrame, path: Path) -> None:
    import altair as alt
    import vl_convert as vlc

    base = alt.Chart(rows).encode(
        y=alt.Y("label:N", sort=None, title=None),
        x=alt.X("ci_low:Q", title="ATT（CAR差）"),
        x2="ci_high:Q",
    )
    bars = base.mark_rule(strokeWidth=2, color="#2a78d6")
    points = alt.Chart(rows).mark_point(size=110, filled=True, color="#2a78d6").encode(
        y=alt.Y("label:N", sort=None), x="att:Q",
        tooltip=["label", alt.Tooltip("att:Q", format="+.4f"), alt.Tooltip("n:Q", title="ペア数")],
    )
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(strokeDash=[4, 4], color="#898781").encode(x="x:Q")
    chart = (zero + bars + points).properties(width=520, height=max(24 * len(rows), 160),
                                             title="フォレストプロット（試運転の結果であり、最終結果ではない）")
    path.write_bytes(vlc.vegalite_to_png(chart.to_json(), scale=2))


def run(run_id: str, progress: Progress) -> stage_runner.StageResult:
    settings = runs.run_config(run_id)
    frozen = runs.run_info(run_id).get("frozen")
    if not frozen:
        raise RuntimeError("マッチングが凍結されていません")
    main = frozen["config"]
    out = runs.stage_dir(run_id, stages.EVALUATE)
    car_table = pd.read_csv(runs.stage_dir(run_id, stages.CAR) / "car.csv", dtype={"code": str})
    targets = pd.read_csv(runs.stage_dir(run_id, stages.INGEST) / "targets.csv", dtype=str, encoding="utf-8-sig",
                          keep_default_na=False).set_index("code")
    main_window = window_label(tuple(settings["event_window_main"]))
    reps, seed = settings["bootstrap_reps"], settings["seed"]

    # 1. 窓の違い（主分析の設定）
    sensitivity = []
    for window in settings["event_windows"]:
        name = window_label(tuple(window))
        pairs = pd.read_csv(runs.stage_dir(run_id, stages.MATCHING) / "matches" / f"{main}.csv", dtype=str)
        table = att.pair_differences(pairs, car_table, f"car_{name}")
        sensitivity.append(_row(f"窓 {car.window_title(tuple(window))}", table, reps, seed, "window"))

    # 2. マッチングの設定（復元の有無・キャリパー）。主分析の窓で比べる
    grid_rows, grid_balance = [], pd.read_csv(runs.stage_dir(run_id, stages.FREEZE) / "balance_grid.csv", dtype={"config": str})
    for replacement, caliper_sd in propensity.matching_grid(settings):
        name = propensity.config_name(replacement, caliper_sd)
        pairs = pd.read_csv(runs.stage_dir(run_id, stages.MATCHING) / "matches" / f"{name}.csv", dtype=str)
        table = att.pair_differences(pairs, car_table, f"car_{main_window}")
        balance_row = grid_balance[grid_balance["config"] == name]
        smd = float(balance_row["max_abs_smd_after"].iloc[0]) if len(balance_row) else float("nan")
        row = _row(f"マッチ {name}", table, reps, seed, "match")
        grid_rows.append({**row, "config": name, "main": int(name == main), "max_abs_smd_after": smd})
        if name != main:
            sensitivity.append(row)
    grid = pd.DataFrame(grid_rows)
    grid.to_csv(out / "grid.csv", index=False, encoding="utf-8-sig")

    # 3. プラセボ（主分析のマッチ）
    main_pairs = pd.read_csv(runs.stage_dir(run_id, stages.MATCHING) / "matches" / f"{main}.csv", dtype=str)
    placebo_table = att.pair_differences(main_pairs, car_table, "car_placebo")
    sensitivity.append(_row("プラセボ窓（発表前）", placebo_table, reps, seed, "placebo"))

    # 4. Rosenbaum bounds（復元なし・主分析の窓）
    rosen = pd.DataFrame()
    if not frozen["replacement"]:
        table = att.pair_differences(main_pairs, car_table, f"car_{main_window}")
        rosen = pd.DataFrame(rosenbaum_bounds(table["diff"].to_numpy(), settings["rosenbaum_gammas"]))
    rosen.to_csv(out / "rosenbaum.csv", index=False, encoding="utf-8-sig")

    # 5. 市場モデル（日経平均）
    factor_table = factors.load(inputs.current_file("factors")).dropna()
    codes = sorted(set(main_pairs["treated"]) | set(main_pairs["control"]))
    progress.log(f"市場モデル（{MARKET_LABEL}）: {len(codes)}社")
    market = market_model_att(run_id, codes, settings, main_pairs, factor_table, targets, progress)
    (out / "market_model.json").write_text(json.dumps(market, ensure_ascii=False, indent=2), encoding="utf-8")
    m = market["stats"]
    if m.get("n"):
        sensitivity.append({"label": f"市場モデル（{MARKET_LABEL}）", "att": m["att"], "ci_low": m["ci95"][0],
                            "ci_high": m["ci95"][1], "n": m["n"], "kind": "market"})

    sens = pd.DataFrame(sensitivity)
    sens.to_csv(out / "sensitivity.csv", index=False, encoding="utf-8-sig")
    forest(sens.iloc[::-1].reset_index(drop=True), out / "forest.png")

    message = (f"窓・マッチ・市場モデル等 {len(sens)}件の代替推定を並べた。"
               f"市場モデル（{MARKET_LABEL}）の ATT {m.get('att', float('nan')):+.4f}。試運転の結果")
    return stage_runner.StageResult(stages.DONE, message)


def _row(label: str, table: pd.DataFrame, reps: int, seed: int, kind: str) -> dict:
    if table.empty:
        return {"label": label, "att": np.nan, "ci_low": np.nan, "ci_high": np.nan, "n": 0, "kind": kind}
    summary = att.summarize(table["diff"].to_numpy(), reps, seed)
    ci = summary.get("ci95", [np.nan, np.nan])
    return {"label": label, "att": summary.get("att", np.nan), "ci_low": ci[0], "ci_high": ci[1],
            "n": summary.get("n", 0), "kind": kind}


if __name__ == "__main__":
    raise SystemExit(stage_runner.cli(stages.EVALUATE))
