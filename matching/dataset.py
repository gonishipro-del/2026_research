"""マッチング用データセットの構築を担当するモジュール。

収集側の出力（`output/keyword_report.csv`）を読み、1社1件のレコードに
処置フラグと共変量を持たせた「分析用データ」を作る。必要なら財務指標などの
追加共変量を別CSVから証券コードで結合する。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Unit:
    """マッチング対象の企業1社分のレコード。"""

    code: str
    name: str
    treated: bool
    categorical: dict[str, str] = field(default_factory=dict)
    numeric: dict[str, float] = field(default_factory=dict)


def read_rows(path: Path) -> list[dict[str, str]]:
    """UTF-8(BOM付き) CSV を辞書のリストとして読む。"""
    if not path.exists():
        raise FileNotFoundError(
            f"入力ファイルが見つかりません: {path}\n"
            "先に `python collect.py` を実行してデータを収集してください。"
        )
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _to_float(value: str) -> float | None:
    text = (value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def merge_covariates(
    rows: list[dict[str, str]], path: Path, key_column: str
) -> list[dict[str, str]]:
    """追加共変量のCSVを証券コードで左結合する。"""
    extra = {row[key_column]: row for row in read_rows(path)}
    merged: list[dict[str, str]] = []
    for row in rows:
        addition = extra.get(row[key_column], {})
        merged.append({**row, **{k: v for k, v in addition.items() if k != key_column}})
    return merged


def build_units(
    rows: list[dict[str, str]],
    treated_mark: str,
    control_mark: str,
    categorical: list[str],
    numeric: list[str],
) -> tuple[list[Unit], list[dict[str, str]]]:
    """分析用データを作る。

    処置群・対照群のいずれにも分類できない行（判定不可）と、共変量が欠損して
    いる行は母集団から除外し、除外された行を第2要素で返す。
    """
    units: list[Unit] = []
    dropped: list[dict[str, str]] = []

    for row in rows:
        mark = row.get("該当有無", "")
        if mark not in (treated_mark, control_mark):
            dropped.append({**row, "除外理由": "処置状態が判定不可"})
            continue

        missing = [c for c in categorical if not row.get(c, "").strip()]
        values = {c: _to_float(row.get(c, "")) for c in numeric}
        missing += [c for c, v in values.items() if v is None]
        if missing:
            dropped.append({**row, "除外理由": f"共変量が欠損: {', '.join(missing)}"})
            continue

        units.append(
            Unit(
                code=row["コード"],
                name=row["銘柄名"],
                treated=(mark == treated_mark),
                categorical={c: row[c].strip() for c in categorical},
                numeric={c: v for c, v in values.items() if v is not None},
            )
        )

    return units, dropped


def describe(units: list[Unit]) -> dict:
    """処置群・対照群の内訳を返す。"""
    treated = [u for u in units if u.treated]
    control = [u for u in units if not u.treated]
    return {
        "母集団": len(units),
        "処置群": len(treated),
        "対照群": len(control),
        "処置割合": f"{len(treated) / len(units):.1%}" if units else "-",
    }
