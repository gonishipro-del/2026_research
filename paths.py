"""データ収集とマッチングの双方が参照する共通パス定義。

収集側の出力がそのままマッチング側の入力になるため、両フォルダから
同じ場所を指せるようにここへ集約する。個々の処理の設定値は
`collection/config.py` と `matching/config.py` に置く。
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 東証が公開する上場銘柄一覧（data_j.xlsx）
LISTED_COMPANY_XLSX = BASE_DIR / "data_j.xlsx"

# --- データセット（決算短信PDFと抽出テキストの保存先）-----------------------
DATASET_DIR = BASE_DIR / "20260904dataset"
PDF_DIR = DATASET_DIR / "pdf"
TEXT_DIR = DATASET_DIR / "text"
METADATA_JSONL = DATASET_DIR / "metadata.jsonl"

# --- 出力 -------------------------------------------------------------------
OUTPUT_DIR = BASE_DIR / "output"

# 収集側の出力（= マッチング側の入力）
REPORT_XLSX = OUTPUT_DIR / "keyword_report.xlsx"
REPORT_CSV = OUTPUT_DIR / "keyword_report.csv"

# マッチング側の出力
MATCHING_DIR = OUTPUT_DIR / "matching"
MATCHED_PAIRS_CSV = MATCHING_DIR / "matched_pairs.csv"
BALANCE_CSV = MATCHING_DIR / "covariate_balance.csv"
