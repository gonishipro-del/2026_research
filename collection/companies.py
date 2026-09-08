"""上場銘柄一覧（data_j.xlsx）の読み込みを担当するモジュール。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import openpyxl


@dataclass(frozen=True)
class Company:
    """対象企業1社分の基本情報。"""

    code: str
    name: str
    market: str
    industry33: str
    scale: str


# data_j.xlsx の列順（0始まり）
_COL_CODE = 1
_COL_NAME = 2
_COL_MARKET = 3
_COL_INDUSTRY33 = 5
_COL_SCALE = 9


def load_companies(xlsx_path: Path, market: str) -> list[Company]:
    """data_j.xlsx を読み、指定した市場区分の企業を証券コード順で返す。"""
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    sheet = workbook.active

    companies: list[Company] = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if not row or row[_COL_MARKET] != market:
            continue
        companies.append(
            Company(
                code=str(row[_COL_CODE]).strip(),
                name=str(row[_COL_NAME]).strip(),
                market=str(row[_COL_MARKET]).strip(),
                industry33=str(row[_COL_INDUSTRY33]).strip(),
                scale=str(row[_COL_SCALE]).strip(),
            )
        )
    workbook.close()

    companies.sort(key=lambda c: c.code)
    return companies
