"""ドル円レート（1ドルあたりの円）の取得と、円建てリターンのドル建てへの換算。

Kenneth French の Japan 5 Factors は、日本株をドル建てに直して作られている。株価のリターンも
同じようにドル建てに換算して、ファクターと単位を揃える（方式A）。RF は CSV の米国金利をそのまま使う。

レートは yfinance の JPY=X（日次終値）をプログラムで取得し、data/cache/fx/ にキャッシュする。
CAR を計算した run の出力にも系列を保存し、後から同じ換算を再現できるようにする。
Yahoo の JPY=X の日付ラベルはロンドン時間の0時区切りで、東京の株価の日付と1日ずれる。Kenneth French の
市場ファクターと最も合うのは「日付ラベルが1日後のレート」を東京のその日のレートとして使う合わせ方で、
日経平均をドル換算して市場ファクターとの相関を見ると 2020〜2026年のどの期間でも 0.93〜0.95（同じ日の
ラベルでは 0.64〜0.84）だった。そのため取得したレートを1つ前の日付にずらしてから使う（align_to_tokyo）。
確認は python -m core.fx --check で再現できる。

単体実行: python -m core.fx   （キャッシュを作り直して範囲を表示）
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import pandas as pd

import config
from core import state

TICKER = "JPY=X"
SOURCE = "yfinance（Yahoo Finance の JPY=X 日次終値）"
CACHE = config.CACHE_DIR / "fx" / "USDJPY.json"
MAX_RETRIES = 3
RETRY_SLEEP_SEC = 5.0
PLAUSIBLE_RANGE = (50.0, 300.0)  # 1ドルあたりの円として明らかにおかしい値を弾く
YAHOO_LABEL_OFFSET = 1  # 東京の株価の日付 t には、Yahoo の日付ラベルが t の1つ後のレートを使う
ALIGNMENT = "Yahoo の日付ラベルが1つ後のレートを、東京の株価のその日のレートとして使う"
MAX_GAP_DAYS = 7


class FxError(RuntimeError):
    """レートを取得できない、または値がおかしい。"""


def _download(start: date, end: date) -> pd.Series:
    import yfinance as yf

    last_error = ""
    for attempt in range(MAX_RETRIES):
        try:
            history = yf.Ticker(TICKER).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                                auto_adjust=False)
            if history is not None and not history.empty:
                rates = history["Close"].astype(float)
                rates.index = pd.DatetimeIndex(rates.index).tz_localize(None).normalize()
                return rates[~rates.index.duplicated(keep="last")].sort_index()
            last_error = "データ0件"
        except Exception as error:  # noqa: BLE001  通信エラーなどはまとめて再試行する
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(RETRY_SLEEP_SEC * (2 ** attempt))
    raise FxError(f"ドル円レートを取得できません（{TICKER}）: {last_error}")


def validate(rates: pd.Series, start: date, end: date) -> list[str]:
    """問題があれば理由の一覧を返す（空なら問題なし）。"""
    problems = []
    if rates.empty:
        return ["レートが0件"]
    low, high = PLAUSIBLE_RANGE
    outside = rates[(rates < low) | (rates > high) | rates.isna()]
    if len(outside):
        problems.append(f"範囲外・欠損の値が {len(outside)} 件（例: {outside.index[0].date()}）")
    if rates.index.min() > pd.Timestamp(start) + pd.Timedelta(days=MAX_GAP_DAYS):
        problems.append(f"開始日 {start} を覆っていない（最初の日 {rates.index.min().date()}）")
    if rates.index.max() < pd.Timestamp(end) - pd.Timedelta(days=MAX_GAP_DAYS):
        problems.append(f"終了日 {end} を覆っていない（最後の日 {rates.index.max().date()}）")
    gaps = rates.index.to_series().diff().dt.days.dropna()
    if (gaps > MAX_GAP_DAYS).any():
        problems.append(f"{MAX_GAP_DAYS}日を超える空白がある（最大 {int(gaps.max())} 日）")
    return problems


def align_to_tokyo(rates: pd.Series) -> pd.Series:
    """Yahoo の日付ラベルのレートを、東京の株価の日付に合わせる（1つ後のラベルの値を使う）。"""
    return rates.shift(-YAHOO_LABEL_OFFSET).dropna()


def load_usdjpy(start: date, end: date, refresh: bool = False) -> tuple[pd.Series, dict]:
    """start〜end を覆うドル円レート（1ドルあたりの円、東京の株価の日付に合わせたもの）と、取得の記録を返す。

    キャッシュには Yahoo の日付ラベルのままの値を保存し、返すときに align_to_tokyo で合わせる。
    """
    if CACHE.exists() and not refresh:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if cached["start"] <= start.isoformat() and cached["covered_until"] >= end.isoformat():
            rates = pd.Series(cached["rates"], dtype=float)
            rates.index = pd.to_datetime(rates.index)
            info = {key: value for key, value in cached.items() if key != "rates"}
            return align_to_tokyo(rates.sort_index()), {**info, "alignment": ALIGNMENT}

    rates = _download(start - timedelta(days=10), end + timedelta(days=10))
    problems = validate(rates, start, end)
    if problems:
        raise FxError("ドル円レートに問題があります: " + "; ".join(problems))
    info = {"source": SOURCE, "ticker": TICKER, "fetched_at": state.now(), "start": start.isoformat(),
            "covered_until": end.isoformat(), "first_date": str(rates.index.min().date()),
            "last_date": str(rates.index.max().date()), "rows": int(len(rates)),
            "unit": "1ドルあたりの円（終値）"}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({**info, "rates": {d.date().isoformat(): v for d, v in rates.items()}},
                                ensure_ascii=False), encoding="utf-8")
    return align_to_tokyo(rates), {**info, "alignment": ALIGNMENT}


def to_usd_return(jpy_return: float, rate_previous: float, rate_now: float) -> float:
    """円建てのリターンをドル建てに換算する。rate は1ドルあたりの円。

    1 + R_USD = (1 + R_JPY) × (S_前日 ÷ S_当日)。円安（S が上がる）だとドル建てのリターンは下がる。
    """
    return (1 + jpy_return) * (rate_previous / rate_now) - 1


def alignment_check(start: str = "2020-01-01", end: str | None = None) -> pd.DataFrame:
    """日経平均をドル換算したリターンと、ファクターの市場リターンの相関を、レートのずらし方ごとに比べる。"""
    import yfinance as yf

    from core import factors, inputs

    table = factors.load(inputs.current_file("factors")).dropna()
    market = (table["Mkt-RF"] + table["RF"]).loc[start:end]
    first, last = market.index.min() - timedelta(days=10), market.index.max() + timedelta(days=10)
    raw = _download(first.date(), last.date())
    nikkei = yf.Ticker("^N225").history(start=first.date().isoformat(), end=last.date().isoformat(),
                                        auto_adjust=False)["Close"]
    nikkei.index = pd.DatetimeIndex(nikkei.index).tz_localize(None).normalize()
    returns = nikkei.pct_change().dropna()
    rows = []
    for offset in (-1, 0, 1, 2):
        shifted = raw.shift(-offset)
        converted = (1 + returns) * (shifted.shift(1).reindex(returns.index) / shifted.reindex(returns.index)) - 1
        joined = pd.concat([converted.rename("n225_usd"), market.rename("mkt")], axis=1).dropna()
        rows.append({"Yahooの日付ラベルのずらし": offset, "相関": round(joined["n225_usd"].corr(joined["mkt"]), 3),
                     "日数": len(joined)})
    return pd.DataFrame(rows)


def main() -> int:
    import sys

    if "--check" in sys.argv:
        print(alignment_check().to_string(index=False))
        print(f"採用しているずらし: {YAHOO_LABEL_OFFSET}（{ALIGNMENT}）")
        return 0
    rates, info = load_usdjpy(date(2024, 1, 1), date.today() - timedelta(days=1), refresh=True)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
