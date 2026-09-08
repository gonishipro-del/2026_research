"""データ収集（決算短信の取得・テキスト抽出・ワード集計）の設定値。

パスは共通定義 `paths.py` を参照する。ここには収集の挙動に関する値だけを置く。
"""
from paths import (  # noqa: F401  各モジュールからは config 経由で参照する
    BASE_DIR,
    LISTED_COMPANY_XLSX,
    METADATA_JSONL,
    OUTPUT_DIR,
    PDF_DIR,
    REPORT_CSV,
    REPORT_XLSX,
    TEXT_DIR,
)

# 検索対象ワードの定義ファイル
KEYWORD_FILE = BASE_DIR / "keywords.txt"

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
