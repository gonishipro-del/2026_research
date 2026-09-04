"""プロジェクト全体の設定値。

パスや通信のふるまいなど、実行環境に依存する値はここに集約する。
"""
from pathlib import Path

# --- パス設定 ---------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

# 東証が公開する上場銘柄一覧（data_j.xlsx）
LISTED_COMPANY_XLSX = BASE_DIR / "data_j.xlsx"

# 検索対象ワードの定義ファイル
KEYWORD_FILE = BASE_DIR / "keywords.txt"

# データセット（決算短信PDFと抽出テキストの保存先）
DATASET_DIR = BASE_DIR / "20260904dataset"
PDF_DIR = DATASET_DIR / "pdf"
TEXT_DIR = DATASET_DIR / "text"
METADATA_JSONL = DATASET_DIR / "metadata.jsonl"

# 集計結果の出力先
OUTPUT_DIR = BASE_DIR / "output"
REPORT_XLSX = OUTPUT_DIR / "keyword_report.xlsx"
REPORT_CSV = OUTPUT_DIR / "keyword_report.csv"

# --- 対象企業 ---------------------------------------------------------------
# data_j.xlsx の「市場・商品区分」列がこの値の行を対象にする
TARGET_MARKET = "プライム（内国株式）"

# --- 決算プロ（株プロ）への通信設定 -----------------------------------------
KABUPRO_BASE_URL = "http://ke.kabupro.jp"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research-crawler/1.0 "
    "(academic use; contact via site owner)"
)
# サーバ負荷に配慮した設定。同時接続数とリクエスト間隔。
MAX_WORKERS = 3
REQUEST_INTERVAL_SEC = 0.5
REQUEST_TIMEOUT_SEC = 60
MAX_RETRIES = 3
RETRY_WAIT_SEC = 3.0

# --- PDFテキスト抽出 --------------------------------------------------------
# テキスト抽出はCPUバウンドなのでプロセス並列で処理する
EXTRACT_WORKERS = 4
# 抽出文字数がこれ未満なら画像PDF（テキスト非埋め込み）とみなす
MIN_TEXT_CHARS = 200
