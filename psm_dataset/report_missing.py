"""最終CSVの欠損状況と備考カテゴリの集計表示。"""
from __future__ import annotations

import re
from collections import Counter

import pandas as pd

from . import config

REPORT_COLUMNS = [
    "industry_code", "total_assets", "roa_ordinary", "roa_netincome", "leverage",
    "bps", "price_pre_announce", "pbr", "ai_flag", "ai_count", "source_type",
]


def main() -> int:
    frame = pd.read_csv(config.DATASET_CSV, dtype={"code": str, "industry_code": str}, encoding="utf-8-sig")
    total = len(frame)
    print(f"=== 変数ごとの欠損（全{total}社）===")
    for column in REPORT_COLUMNS:
        missing = int(frame[column].isna().sum())
        print(f"{column:20s} {missing:5d}社  {missing / total:6.1%}")

    print("\n=== source_type ===")
    print(frame["source_type"].fillna("(取得なし)").value_counts().to_string())
    print(f"\n金融業(is_financial=1): {int(frame['is_financial'].sum())}社")

    counter: Counter[str] = Counter()
    for note in frame["備考"].dropna():
        counter.update(set(re.findall(r"【(.+?)】", note)))
    print("\n=== 備考カテゴリ別（社数）===")
    for category, count in counter.most_common():
        print(f"{count:5d}  {category}")

    needs_supplement = frame["備考"].fillna("").str.contains("画像PDF・要補完").sum()
    print(f"\n画像PDF・要補完（段階2が必要）: {int(needs_supplement)}社")
    return 0
