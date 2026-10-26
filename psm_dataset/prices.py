"""yfinance からの株価取得（キャッシュ・リトライ・株式分割補正）。

yfinance の Close は過去分も「現在の株数基準」に分割調整済みである。一方、決算短信の
株式数は発表時点の実数なので、基準日より後に分割があった銘柄は分割比率を掛け戻して
発表時点の実株価に換算する。配当調整は掛からないよう auto_adjust=False で取得する。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, timedelta

from . import config


@dataclass
class PriceResult:
    price: float | None = None
    price_date: str = ""
    split_factor: float = 1.0  # 掛け戻した分割比率の累積（1.0 なら補正なし）
    note: str = ""  # 取得できなかった/補正した理由（備考用）
    failed: bool = False  # 取得失敗（失敗銘柄リスト対象）


def _cache_path(code: str):
    return config.PRICE_CACHE_DIR / f"{code}.json"


def _download(code: str, start: date) -> dict:
    """yfinance から終値と分割を取得して辞書にする。取得0件は error を入れて返す。"""
    import yfinance as yf  # 遅延import: 抽出だけの実行では不要

    last_error = ""
    for attempt in range(config.PRICE_MAX_RETRIES):
        try:
            history = yf.Ticker(f"{code}.T").history(
                start=start.isoformat(),
                end=(date.today() + timedelta(days=1)).isoformat(),
                auto_adjust=False,
                actions=True,
            )
            if history is not None and not history.empty:
                closes = {index.strftime("%Y-%m-%d"): float(row["Close"]) for index, row in history.iterrows()}
                splits = {
                    index.strftime("%Y-%m-%d"): float(row["Stock Splits"])
                    for index, row in history.iterrows()
                    if row.get("Stock Splits", 0) and row["Stock Splits"] > 0
                }
                return {"closes": closes, "splits": splits, "error": ""}
            last_error = "データ0件（上場廃止・コード変更の可能性）"
        except Exception as error:  # noqa: BLE001  レート制限などは種類が多いのでまとめて扱う
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(config.PRICE_RETRY_SLEEP_SEC * (2 ** attempt))
    return {"closes": {}, "splits": {}, "error": last_error}


def load_price_series(code: str, announce_date: date, refresh: bool = False) -> dict:
    """銘柄の株価系列を返す。キャッシュがあれば再取得しない（失敗結果も再試行まで保持）。"""
    path = _cache_path(code)
    start = announce_date - timedelta(days=config.PRICE_LOOKBACK_DAYS)
    if path.exists() and not refresh:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("start") and cached["start"] <= start.isoformat():
            return cached

    data = _download(code, start)
    data["start"] = start.isoformat()
    config.PRICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(config.PRICE_REQUEST_INTERVAL_SEC)
    return data


def price_before_announce(code: str, announce_date: date, refresh: bool = False) -> PriceResult:
    """announce_date より前の直近営業日の終値を、発表時点の実株価に換算して返す。"""
    series = load_price_series(code, announce_date, refresh=refresh)
    if series.get("error"):
        return PriceResult(note=f"株価取得失敗: {series['error']}", failed=True)

    candidates = [day for day in series["closes"] if day < announce_date.isoformat()]
    if not candidates:
        return PriceResult(note="announce_date前の株価なし（上場直後等）", failed=True)
    price_day = max(candidates)
    price = series["closes"][price_day]

    # 株価日より後の分割はすべて掛け戻す（発表日以降の分割に加え、株価日〜発表日の間も含む）
    factor = 1.0
    applied = []
    for split_day, ratio in sorted(series["splits"].items()):
        if split_day > price_day:
            factor *= ratio
            applied.append(f"{split_day}:{ratio:g}")
    note = ""
    if applied:
        price *= factor
        note = f"株式分割補正（{', '.join(applied)}、累積{factor:g}倍）"
    return PriceResult(price=price, price_date=price_day, split_factor=factor, note=note)
