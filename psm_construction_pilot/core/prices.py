"""yfinance（{code}.T）からの株価取得（キャッシュ・リトライ・レート制限対策）。

1回の取得で、段階3の PBR（発表前営業日の終値）と段階6の日次リターン（推定期間を含む）の
両方をまかなえるよう、発表日の PRICE_LOOKBACK_DAYS 日前から取得日までを取る。

yfinance の Close（auto_adjust=False）は配当調整なし・分割調整ありで、過去分も現在の株数基準。
決算短信の株式数は発表時点の実数なので、PBR では株価日より後の分割を掛け戻して
発表時点の実株価に換算する（既存の psm_dataset.prices と同じ考え方）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

import config
from core import state

PRICE_LOOKBACK_DAYS = 420  # 推定期間 [-250,-21] 営業日を覆う暦日数（余裕込み）
MAX_RETRIES = 3
RETRY_SLEEP_SEC = 5.0  # 試行ごとに倍々
REQUEST_INTERVAL_SEC = 0.5


@dataclass
class PriceResult:
    price: float | None = None
    price_date: str = ""
    split_factor: float = 1.0  # 掛け戻した分割比率の累積（1.0 なら補正なし）
    note: str = ""
    failed: bool = False


def _cache_path(code: str):
    return config.PRICE_CACHE_DIR / f"{code}.json"


def _download(code: str, start: date) -> dict:
    import yfinance as yf

    last_error = ""
    for attempt in range(MAX_RETRIES):
        try:
            history = yf.Ticker(f"{code}.T").history(
                start=start.isoformat(), end=(date.today() + timedelta(days=1)).isoformat(),
                auto_adjust=False, actions=True,
            )
            if history is not None and not history.empty:
                return {
                    "closes": {index.strftime("%Y-%m-%d"): float(row["Close"]) for index, row in history.iterrows()},
                    "adj_closes": {index.strftime("%Y-%m-%d"): float(row["Adj Close"])
                                   for index, row in history.iterrows() if "Adj Close" in row},
                    "splits": {index.strftime("%Y-%m-%d"): float(row["Stock Splits"])
                               for index, row in history.iterrows()
                               if row.get("Stock Splits", 0) and row["Stock Splits"] > 0},
                    "error": "",
                }
            last_error = "データ0件（上場廃止・コード変更の可能性）"
        except Exception as error:  # noqa: BLE001  レート制限などは種類が多いのでまとめて扱う
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(RETRY_SLEEP_SEC * (2 ** attempt))
    return {"closes": {}, "adj_closes": {}, "splits": {}, "error": last_error}


def load_series(code: str, announce_date: date, refresh: bool = False) -> dict:
    """銘柄の株価系列。キャッシュが必要な期間を覆っていれば再取得しない。失敗はキャッシュしない。"""
    path = _cache_path(code)
    start = announce_date - timedelta(days=PRICE_LOOKBACK_DAYS)
    if path.exists() and not refresh:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("start", "9999") <= start.isoformat() and not cached.get("error"):
            return cached
    data = _download(code, start)
    data.update(start=start.isoformat(), fetched_at=state.now(), ticker=f"{code}.T")
    if not data["error"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(REQUEST_INTERVAL_SEC)
    return data


def price_before_announce(series: dict, announce_date: date) -> PriceResult:
    """発表日の前営業日の終値を、発表時点の実株価に換算して返す。"""
    if series.get("error"):
        return PriceResult(note=f"株価取得失敗: {series['error']}", failed=True)
    candidates = [day for day in series["closes"] if day < announce_date.isoformat()]
    if not candidates:
        return PriceResult(note="発表日前の株価なし（上場直後等）", failed=True)
    price_day = max(candidates)
    price = series["closes"][price_day]
    factor, applied = 1.0, []
    for split_day, ratio in sorted(series["splits"].items()):
        if split_day > price_day:
            factor *= ratio
            applied.append(f"{split_day}:{ratio:g}")
    note = ""
    if applied:
        price *= factor
        note = f"株式分割補正（{', '.join(applied)}、累積{factor:g}倍）"
    return PriceResult(price=price, price_date=price_day, split_factor=factor, note=note)


def close_series(series: dict) -> pd.Series:
    """分割調整後の終値（日付インデックス）。段階6の日次リターンに使う。"""
    closes = pd.Series(series.get("closes", {}), dtype=float)
    closes.index = pd.to_datetime(closes.index)
    return closes.sort_index()
