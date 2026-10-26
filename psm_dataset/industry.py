"""業種コード表の読み込みと金融業判定。"""
from __future__ import annotations

import pandas as pd

from . import config


def _normalize_industry_code(raw: object) -> str:
    """Excel上で数値化され先頭の0が落ちた33業種コード（50 → 0050）を4桁に戻す。"""
    text = str(raw).strip()
    return text.zfill(4) if text.isdigit() else text


def load_industry_table() -> dict[str, tuple[str, str]]:
    """証券コード → (33業種コード, 33業種名) の辞書を返す。"""
    frame = pd.read_excel(config.INDUSTRY_XLSX, dtype=str)
    needed = [
        config.INDUSTRY_COL_CODE,
        config.INDUSTRY_COL_INDUSTRY_CODE,
        config.INDUSTRY_COL_INDUSTRY_NAME,
    ]
    missing = [column for column in needed if column not in frame.columns]
    if missing:
        raise ValueError(f"業種コード表に列がありません: {missing} / 実際の列: {list(frame.columns)}")

    table: dict[str, tuple[str, str]] = {}
    for _, row in frame.iterrows():
        code = str(row[config.INDUSTRY_COL_CODE]).strip().upper()
        table[code] = (
            _normalize_industry_code(row[config.INDUSTRY_COL_INDUSTRY_CODE]),
            str(row[config.INDUSTRY_COL_INDUSTRY_NAME]).strip(),
        )
    return table


def is_financial(industry_name: str) -> int:
    return int(industry_name in config.FINANCIAL_INDUSTRIES)
