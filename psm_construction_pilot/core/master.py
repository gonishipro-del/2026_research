"""JPX 上場銘柄一覧（data_j.xlsx）の読み込みと検証。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

EXPECTED_COLUMNS = ["日付", "コード", "銘柄名", "市場・商品区分", "33業種コード", "33業種区分",
                    "17業種コード", "17業種区分", "規模コード", "規模区分"]
PRIME_DOMESTIC = "プライム（内国株式）"


def _normalize_industry_code(raw: object) -> str:
    """Excel 上で数値化され先頭の0が落ちた業種コード（50 → 0050）を4桁に戻す。"""
    text = str(raw).strip()
    return text.zfill(4) if text.isdigit() else text


def load(path: Path) -> pd.DataFrame:
    frame = pd.read_excel(path, dtype=str).fillna("")
    missing = [column for column in EXPECTED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"上場銘柄一覧に列がありません: {missing}（実際の列: {list(frame.columns)}）")
    for column in frame.columns:
        frame[column] = frame[column].astype(str).str.strip()
    frame["コード"] = frame["コード"].str.upper()
    frame["33業種コード"] = frame["33業種コード"].map(_normalize_industry_code)
    return frame


def industries(frame: pd.DataFrame, market: str = PRIME_DOMESTIC) -> list[str]:
    """33業種区分の一覧（指定市場に属するもの）。設定ページの選択肢に使う。"""
    names = frame.loc[frame["市場・商品区分"] == market, "33業種区分"]
    return sorted(name for name in names.unique() if name and name != "-")


def validate(path: Path, target_industries: list[str] | None = None) -> dict:
    report: dict = {"ok": False, "errors": [], "warnings": [], "file": path.name}
    try:
        raw = pd.read_excel(path, dtype=str)
    except Exception as error:  # noqa: BLE001  壊れたファイルは検証結果として返す
        report["errors"].append(f"Excel を読めません: {error}")
        return report

    report["columns"] = list(raw.columns)
    missing = [column for column in EXPECTED_COLUMNS if column not in raw.columns]
    extra = [column for column in raw.columns if column not in EXPECTED_COLUMNS]
    if missing:
        report["errors"].append(f"列がありません: {missing}")
        return report
    if extra:
        report["warnings"].append(f"想定外の列があります: {extra}")

    frame = load(path)
    report["as_of_dates"] = sorted(frame["日付"].unique().tolist())
    if len(report["as_of_dates"]) != 1:
        report["warnings"].append("「日付」列に複数の値があります")
    report["rows"] = int(len(frame))
    prime = frame[frame["市場・商品区分"] == PRIME_DOMESTIC]
    report["prime_count"] = int(len(prime))
    if prime.empty:
        report["errors"].append(f"「{PRIME_DOMESTIC}」の銘柄がありません")

    report["industries"] = {}
    for name in target_industries or ["建設業"]:
        rows = prime[prime["33業種区分"] == name]
        report["industries"][name] = {
            "prime_count": int(len(rows)),
            "industry33_codes": sorted(rows["33業種コード"].unique().tolist()),
            "industry17": sorted((rows["17業種コード"] + " " + rows["17業種区分"]).unique().tolist()),
        }
        if rows.empty:
            report["errors"].append(f"プライムに「{name}」の銘柄がありません")
    report["ok"] = not report["errors"]
    return report
