"""段階6: 累積異常収益率（CAR）。凍結後のみ実行できる。

期待収益率モデルは Fama-French 5 ファクター（入力フォルダの Japan 5 Factors 日次）。
  R_it − RF_t = α_i + β_i1·MKT_t + … + β_i5·CMA_t + ε_it     推定期間 [-250, -21]（営業日、発表日を0）
  AR_it = (R_it − RF_t) − (α̂_i + Σ β̂_ik·F_kt)                   α を含めるかは include_alpha
  CAR = Σ AR を窓の期間で合計。窓は event_windows（既定は1日・3日・5日・10日・30日）と placebo_window。

対象は、凍結したマッチの処置群と対照群のみ。感度分析（段階8）で使う他の設定のマッチに含まれる企業も
同じ計算に入れる（それ以外の全社分は計算しない）。
株価は yfinance の分割調整後の終値（Close、auto_adjust=False）から日次リターンを作る。
ファクターは米ドル建てなので、設定 stock_currency="usd"（既定）では、株価の日次リターンをドル円レートで
ドル建てに換算してから RF（CSV の米国金利）を引く（core.fx）。"jpy" は換算しない（以前の方法）。
株価とファクターの両方に存在する日だけを営業日として数える（内部結合）。合わない日は使わず、件数を記録する。

出力（output/run_*/stage6_car/）:
  car.csv          企業ごとの CAR（窓ごと）、推定の観測数、α、β、決定係数、警告
  ar_daily.csv     企業×日の異常収益率（事象窓と推定期間の外側のみ）
  warnings.csv     日次変化が ±50% を超えた日（未調整の株式分割の可能性）
  fx_usdjpy.csv    換算に使ったドル円レート（stock_currency="usd" のとき）
  currency.json    通貨の扱いとレートの取得元・取得日時

単体実行: python -m core.car [--run RUN_ID]
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from core import factors, fx, inputs, prices, propensity, runs, stage_runner, stages
from core.progress import Progress

FACTOR_COLUMNS = factors.FACTOR_COLUMNS
ESTIMATION = (-250, -21)
JUMP_LIMIT = 0.5


def window_label(window: tuple[int, int]) -> str:
    start, end = window
    return f"{start:+d}_{end:+d}".replace("+", "p").replace("-", "m")


def window_title(window: tuple[int, int]) -> str:
    """画面・図に出す窓の名前（例: 3日 [-1, +1]）。"""
    start, end = window
    def signed(day: int) -> str:
        return f"{day:+d}" if day else "0"
    return f"{end - start + 1}日 [{signed(start)}, {signed(end)}]"


def covered_codes(run_id: str) -> list[str]:
    """凍結したマッチの処置群と対照群、および感度分析の全マッチに含まれる企業。"""
    codes: set[str] = set()
    for path in (runs.stage_dir(run_id, stages.MATCHING) / "matches").glob("*.csv"):
        pairs = pd.read_csv(path, dtype=str)
        codes |= set(pairs["treated"]) | set(pairs["control"])
    return sorted(codes)


def matched_calendar(close: pd.Series, factor_dates: pd.DatetimeIndex) -> tuple[pd.DatetimeIndex, int]:
    """株価とファクターの両方に存在する日（内部結合）と、合わずに使わない日数を返す。"""
    span = factor_dates[(factor_dates >= close.index.min()) & (factor_dates <= close.index.max())]
    common = span[span.isin(close.index)]
    stock_only = close.index[(close.index >= span[0]) & (close.index <= span[-1]) & ~close.index.isin(span)]
    unmatched = int(len(span) - len(common)) + int(len(stock_only))
    return common, unmatched


def daily_excess_returns(close: pd.Series, calendar: pd.DatetimeIndex, rf: pd.Series,
                         usdjpy: pd.Series | None = None) -> tuple[pd.Series, list[str]]:
    """分割調整後の終値から日次リターンを作り、超過収益率（− RF）を返す。

    calendar（株価とファクターの共通日）の連続2日の間に株価の取引日が他にない場合だけリターンを作る。
    usdjpy（1ドルあたりの円）を渡すと、リターンをドル建てに換算してから RF を引く。
    その日以前で最も近い日のレートを使う（為替に値がない日は前の値で代用）。
    """
    notes: list[str] = []
    close = close.sort_index()
    returns = {}
    for position in range(1, len(calendar)):
        date, previous = calendar[position], calendar[position - 1]
        if date in close.index and previous in close.index:
            stock_between = close.index[(close.index > previous) & (close.index < date)]
            if len(stock_between) == 0:
                value = close[date] / close[previous] - 1
                if usdjpy is not None:
                    rate_now, rate_previous = usdjpy.asof(date), usdjpy.asof(previous)
                    if pd.isna(rate_now) or pd.isna(rate_previous):
                        continue  # レートがない日は使わない
                    value = fx.to_usd_return(value, rate_previous, rate_now)
                returns[date] = value
    raw = pd.Series(returns, dtype=float).sort_index()
    if (raw.abs() > JUMP_LIMIT).any():
        jumps = raw[raw.abs() > JUMP_LIMIT]
        notes.append("日次変化が±50%超（未調整の分割の可能性）: " + ", ".join(f"{d:%Y-%m-%d}({v:+.0%})" for d, v in jumps.items()))
    excess = raw - rf.reindex(raw.index)
    return excess.dropna(), notes


def abnormal_car(excess: pd.Series, calendar: pd.DatetimeIndex, announce: pd.Timestamp, X: pd.DataFrame,
                 windows: dict[str, tuple[int, int]], *, include_alpha: bool, min_obs: int) -> dict:
    """推定と異常収益率・CAR。X は超過収益率の説明変数（日付のインデックス）。"""
    result: dict = {"warnings": []}
    if announce not in calendar:
        later = calendar[calendar >= announce]
        announce = later[0] if len(later) else announce
        result["warnings"].append("発表日が営業日でないため次の営業日を日0とした")
    e0 = calendar.get_loc(announce)
    lo, hi = ESTIMATION
    if e0 + lo < 0:
        result["warnings"].append("推定期間の履歴が不足")
        return result

    est_dates = calendar[e0 + lo: e0 + hi + 1]
    est = [d for d in est_dates if d in excess.index and d in X.index]
    result["n_est"] = len(est)
    if len(est) < min_obs:
        result["warnings"].append(f"推定の有効観測数 {len(est)} が下限 {min_obs} 未満")
        return result
    design = X.loc[est].to_numpy(dtype=float)
    target = excess.loc[est].to_numpy(dtype=float)
    if include_alpha:
        design = np.column_stack([np.ones(len(est)), design])
    coef, *_ = np.linalg.lstsq(design, target, rcond=None)
    fitted = design @ coef
    ss_res = float(((target - fitted) ** 2).sum())
    ss_tot = float(((target - target.mean()) ** 2).sum())
    result["r2"] = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    if include_alpha:
        result["alpha"], betas = float(coef[0]), coef[1:]
    else:
        result["alpha"], betas = 0.0, coef
    result["betas"] = [float(b) for b in betas]

    def abnormal(date: pd.Timestamp) -> float | None:
        if date not in excess.index or date not in X.index:
            return None
        expected = result["alpha"] + float(X.loc[date].to_numpy(dtype=float) @ np.asarray(result["betas"]))
        return float(excess.loc[date]) - expected

    ar_rows = []
    for name, (start, end) in windows.items():
        if e0 + end >= len(calendar):
            result[name] = float("nan")
            result["warnings"].append(f"{name}: ファクターの最終日が窓の末尾より前")
            continue
        days = calendar[e0 + start: e0 + end + 1]
        values = []
        for date in days:
            ar = abnormal(date)
            ar_rows.append({"window": name, "date": date.date().isoformat(), "rel_day": int(calendar.get_loc(date) - e0), "ar": ar})
            values.append(ar)
        missing = sum(v is None for v in values)
        if missing:
            result[name] = float("nan")
            result["warnings"].append(f"{name}: 株価またはファクターの欠損 {missing} 日")
        else:
            result[name] = float(sum(values))
    result["ar_rows"] = ar_rows
    return result


def factor_frame(factor_table: pd.DataFrame) -> tuple[pd.DatetimeIndex, pd.Series, pd.DataFrame]:
    calendar = factor_table.index
    return calendar, factor_table["RF"], factor_table[FACTOR_COLUMNS[:-1]]


def run(run_id: str, progress: Progress) -> stage_runner.StageResult:
    settings = runs.run_config(run_id)
    if not runs.run_info(run_id).get("frozen"):
        raise RuntimeError("マッチングが凍結されていません（段階5で凍結してください）")
    ok, reason = inputs.is_valid("factors")
    if not ok:
        raise RuntimeError(reason)
    factor_table = factors.load(inputs.current_file("factors"))
    factor_table = factor_table.dropna()
    calendar, rf, X = factor_frame(factor_table)
    out = runs.stage_dir(run_id, stages.CAR)

    targets = pd.read_csv(runs.stage_dir(run_id, stages.INGEST) / "targets.csv", dtype=str, encoding="utf-8-sig",
                          keep_default_na=False).set_index("code")
    codes = covered_codes(run_id)
    windows = {window_label(tuple(w)): tuple(w) for w in settings["event_windows"]}
    placebo = {"placebo": tuple(settings["placebo_window"])}
    all_windows = {**windows, **placebo}
    progress.log(f"対象: {len(codes)}社 / ファクター {calendar[0]:%Y-%m-%d}〜{calendar[-1]:%Y-%m-%d}（{len(calendar)}日）")

    usdjpy, currency = None, {"stock_currency": settings["stock_currency"]}
    if settings["stock_currency"] == "usd":
        earliest = min(pd.Timestamp(targets.loc[c, "announce_date"]) for c in codes) - pd.Timedelta(
            days=prices.PRICE_LOOKBACK_DAYS)
        usdjpy, info = fx.load_usdjpy(earliest.date(), calendar[-1].date())
        currency.update(info, method="株価の日次リターンをドル建てに換算: 1+R_USD = (1+R_JPY)×(S_前日÷S_当日)")
        usdjpy.rename("usdjpy").to_csv(out / "fx_usdjpy.csv", index_label="date", encoding="utf-8-sig")
        progress.log(f"株価をドル建てに換算: {info['source']} {info['first_date']}〜{info['last_date']}"
                     f"（{info['rows']}日、取得 {info['fetched_at']}）")
    else:
        currency["method"] = "換算しない（円建てのリターンから米国のRFを引く）"
        progress.log("株価は円建てのまま（ファクターは米ドル建て）")
    (out / "currency.json").write_text(json.dumps(currency, ensure_ascii=False, indent=2), encoding="utf-8")

    rows, ar_all, warn_rows = [], [], []
    for index, code in enumerate(codes):
        progress.update(index, len(codes), f"CAR {code}")
        name = targets.loc[code, "name"] if code in targets.index else ""
        announce = pd.Timestamp(targets.loc[code, "announce_date"])
        try:
            series = prices.load_series(code, announce.date())
            close = prices.close_series(series)
            common, unmatched = matched_calendar(close, calendar)
            excess, notes = daily_excess_returns(close, common, rf, usdjpy)
            if unmatched:
                notes.append(f"株価とファクターの日付が合わない{unmatched}日は使わなかった")
            result = abnormal_car(excess, common, announce, X, all_windows,
                                  include_alpha=settings["include_alpha"], min_obs=settings["min_estimation_obs"])
        except Exception as error:  # noqa: BLE001  1社の失敗で全体を止めない
            progress.error(code, name, "CAR計算失敗", f"{type(error).__name__}: {error}")
            rows.append({"code": code, "name": name, "status": "失敗"})
            continue
        for note in notes + result["warnings"]:
            progress.error(code, name, "CARの警告", note)
            warn_rows.append({"code": code, "name": name, "message": note})
        row = {"code": code, "name": name, "announce_date": announce.date().isoformat(),
               "n_est": result.get("n_est", 0), "alpha": result.get("alpha", float("nan")),
               "r2": result.get("r2", float("nan")), "status": "ok" if not result["warnings"] else "要確認"}
        for label, value in zip(["beta_mkt", "beta_smb", "beta_hml", "beta_rmw", "beta_cma"], result.get("betas", [])):
            row[label] = value
        for label in all_windows:
            row[f"car_{label}"] = result.get(label, float("nan"))
        row["warnings"] = "；".join(result["warnings"] + notes)
        rows.append(row)
        ar_all += [{"code": code, **r} for r in result.get("ar_rows", [])]
    progress.update(len(codes), len(codes), "完了", force=True)

    pd.DataFrame(rows).to_csv(out / "car.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(ar_all).to_csv(out / "ar_daily.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(warn_rows, columns=["code", "name", "message"]).to_csv(out / "warnings.csv", index=False, encoding="utf-8-sig")

    frame = pd.DataFrame(rows)
    main_col = f"car_{window_label(tuple(settings['event_window_main']))}"
    computed = int(frame[main_col].notna().sum()) if main_col in frame else 0
    message = f"対象{len(codes)}社 / 主分析窓のCAR計算 {computed}社 / 警告{len(warn_rows)}件"
    if computed < len(codes):
        return stage_runner.StageResult(stages.REVIEW, message + "。計算できない企業があります（warnings.csv）")
    return stage_runner.StageResult(stages.DONE, message)


def uncomputed(run_id: str, window: str) -> pd.DataFrame:
    """窓 window の CAR を計算できなかった企業と理由（code, name, reason）。"""
    out = runs.stage_dir(run_id, stages.CAR)
    car = pd.read_csv(out / "car.csv", dtype={"code": str}, encoding="utf-8-sig", keep_default_na=False)
    column = f"car_{window}"
    values = pd.to_numeric(car[column], errors="coerce") if column in car else pd.Series(float("nan"), car.index)
    missing = car[values.isna()]
    reasons = missing.get("warnings", pd.Series("", missing.index)).replace("", "CAR計算失敗（errors.csv）")
    return pd.DataFrame({"code": missing["code"], "name": missing["name"], "reason": reasons}).reset_index(drop=True)


def confirm_uncomputed(run_id: str, note: str) -> None:
    """主分析窓で CAR を計算できない企業があることを確認して先へ進める（そのペアは段階7で外れる）。"""
    if not note.strip():
        raise ValueError("確認のメモは必須です")
    entry = runs.stage_state(run_id, stages.CAR)
    if entry["status"] != stages.REVIEW:
        raise ValueError("段階6は「要確認」ではありません")
    runs.set_status(run_id, stages.CAR, stages.DONE, entry["message"] + "（計算できない企業を確認済み）", confirmed=True)
    runs.log_event(run_id, "CARを計算できない企業の確認", stage=stages.CAR, detail=note.strip())


if __name__ == "__main__":
    raise SystemExit(stage_runner.cli(stages.CAR))
