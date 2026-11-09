"""Fama-French Japan 5 Factors（日次）の読み込みと検証。

入力は data/inputs/factors/ のファイルのみ（Webからの自動取得はしない）。zip のままでも
解凍後の CSV でも読める。値は % 表記なので 100 で割って小数にする。

単体実行: python -m core.factors   （現在のファクターファイルを検証して結果を表示）
"""
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

FACTOR_COLUMNS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]
MISSING_CODES = (-99.99, -999.0)
_DATE_ROW = re.compile(r"^\s*(\d{8})\s*,")
# 他地域のファイル名・説明に現れる語。Japan を含んでも「ex Japan」は日本向けではない
_OTHER_REGIONS = re.compile(
    r"developed|north[_ ]?america|europe|asia[_ ]?pacific|emerging|global|ex[_ ]?japan",
    re.IGNORECASE,
)


class FactorFileError(ValueError):
    """日次でない・5ファクターでない・日本向けでないなど、分析に使えないファイル。"""


def read_text(path: Path) -> tuple[str, str]:
    """(本文, 中身のファイル名) を返す。zip なら中の CSV を1つだけ読む。"""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise FactorFileError(f"zip 内の CSV が1つではありません: {members}")
            raw = archive.read(members[0])
            inner = members[0]
    else:
        raw, inner = path.read_bytes(), path.name
    return raw.decode("utf-8-sig", errors="replace"), inner


def parse_text(text: str) -> tuple[pd.DataFrame, list[str], list[str]]:
    """(% 表記のままの表, 列名, 表より前の説明行) を返す。

    ヘッダの説明行を読み飛ばし、8桁の日付で始まる行から読み、日付でない行で止める
    （月次ファイルの後ろに続く年次の表などを読まないため）。
    """
    header: list[str] | None = None
    preamble: list[str] = []
    rows: list[list[str]] = []
    for line in io.StringIO(text):
        stripped = line.strip()
        if _DATE_ROW.match(stripped):
            rows.append([cell.strip() for cell in stripped.split(",")])
            continue
        if rows:
            break  # 表が終わった
        if stripped.startswith(",") and header is None:
            header = [cell.strip() for cell in stripped.split(",")[1:]]
        elif stripped:
            preamble.append(stripped)

    if header is None:
        raise FactorFileError("列名の行（,Mkt-RF,... の形）が見つかりません")
    if not rows:
        raise FactorFileError("8桁の日付（YYYYMMDD）で始まる行がありません。日次ファイルではない可能性があります")
    width = len(header) + 1
    bad = [row[0] for row in rows if len(row) != width]
    if bad:
        raise FactorFileError(f"列数が合わない行があります（先頭: {bad[:3]}）")

    frame = pd.DataFrame(rows, columns=["date", *header])
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d")
    for column in header:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame.set_index("date"), header, preamble


def to_decimal(percent: pd.DataFrame) -> pd.DataFrame:
    """欠損コードを NaN にし、% を小数にする。"""
    cleaned = percent.copy()
    for code in MISSING_CODES:
        cleaned = cleaned.mask(np.isclose(cleaned, code))
    return cleaned / 100.0


def load(path: Path) -> pd.DataFrame:
    """分析用のファクター（小数、日付インデックス）。検証に通らなければ例外。"""
    report = validate(path)
    if not report["ok"]:
        raise FactorFileError("; ".join(report["errors"]))
    text, _ = read_text(path)
    percent, _, _ = parse_text(text)
    return to_decimal(percent[FACTOR_COLUMNS])


def validate(path: Path) -> dict:
    """日次・5ファクター・日本向けかを確かめ、要約統計を返す。errors が空なら ok。"""
    report: dict = {"ok": False, "errors": [], "warnings": [], "file": path.name}
    try:
        text, inner = read_text(path)
        report["inner_file"] = inner
        percent, header, preamble = parse_text(text)
    except (FactorFileError, zipfile.BadZipFile, ValueError) as error:
        report["errors"].append(str(error))
        return report

    report["preamble"] = preamble
    report["columns"] = header
    if header != FACTOR_COLUMNS:
        report["errors"].append(f"列が {FACTOR_COLUMNS} ではありません（実際: {header}）。5ファクターのファイルではありません")

    names = f"{path.name} {inner}"
    region_text = names + " " + " ".join(preamble)
    if "japan" not in names.lower():
        report["errors"].append(f"ファイル名に Japan がありません（{names}）。日本向けのファイルか確認できません")
    if _OTHER_REGIONS.search(region_text):
        report["errors"].append(f"他地域のファイルの可能性があります（{_OTHER_REGIONS.search(region_text).group(0)}）")
    report["region_basis"] = "ファイル名（CSV本文に地域の記載がないため）"

    dates = percent.index
    gaps = pd.Series(dates).diff().dt.days.dropna()
    span_years = max((dates.max() - dates.min()).days, 1) / 365.25
    rows_per_year = len(dates) / span_years
    if len(gaps) == 0 or gaps.median() > 4 or rows_per_year < 150:
        report["errors"].append(f"日次ではありません（日付の間隔の中央値 {gaps.median() if len(gaps) else float('nan'):.0f} 日、"
                                f"1年あたり {rows_per_year:.0f} 行）")
    if "daily" not in names.lower():
        report["warnings"].append("ファイル名に Daily がありません（日付の間隔からは日次と判定）")
    if dates.has_duplicates:
        report["errors"].append("日付の重複があります")
    if not dates.is_monotonic_increasing:
        report["errors"].append("日付が昇順ではありません")

    present = [column for column in FACTOR_COLUMNS if column in percent.columns]
    report["missing_counts"] = {
        str(code): {column: int(np.isclose(percent[column], code).sum()) for column in present}
        for code in MISSING_CODES
    }
    decimal = to_decimal(percent[present])
    report["stats"] = {
        column: {"mean": float(decimal[column].mean()), "std": float(decimal[column].std()),
                 "min": float(decimal[column].min()), "max": float(decimal[column].max())}
        for column in present
    }
    report.update(
        rows=int(len(percent)), first_date=str(dates.min().date()), last_date=str(dates.max().date()),
        max_gap_days=int(gaps.max()) if len(gaps) else 0,
    )
    report["ok"] = not report["errors"]
    return report


def main() -> int:
    from core import inputs

    path = inputs.current_file("factors")
    report = validate(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    import config  # noqa: F401  （sys.path の設定）

    sys.exit(main())
