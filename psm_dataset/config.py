"""PSM用データセット作成の設定値。

パスは共通定義 `paths.py` を参照する。ここにはパス以外の閾値・キーワードなど
作成処理の挙動に関する値を置く。
"""
from paths import BASE_DIR, LISTED_COMPANY_XLSX, OUTPUT_DIR, PDF_DIR  # noqa: F401

# --- 入力 -------------------------------------------------------------------
# 決算短信PDF（{証券コード}_{企業名}_{YYYYMMDD}.pdf）。収集工程の出力先をそのまま使う
PDF_INPUT_DIR = PDF_DIR

# 業種コード表。列構成: 日付 / コード / 銘柄名 / 市場・商品区分 / 33業種コード /
# 33業種区分 / 17業種コード / 17業種区分 / 規模コード / 規模区分
INDUSTRY_XLSX = LISTED_COMPANY_XLSX
INDUSTRY_COL_CODE = "コード"
INDUSTRY_COL_INDUSTRY_CODE = "33業種コード"
INDUSTRY_COL_INDUSTRY_NAME = "33業種区分"

# 金融業（レバレッジが構造的に高い）。industry_name がこれに該当すれば is_financial=1
FINANCIAL_INDUSTRIES = {"銀行業", "保険業", "証券、商品先物取引業", "その他金融業"}

# --- 出力 -------------------------------------------------------------------
PSM_OUTPUT_DIR = OUTPUT_DIR / "psm"
DATASET_CSV = PSM_OUTPUT_DIR / "dataset_psm.csv"
FAILED_EXTRACTION_CSV = PSM_OUTPUT_DIR / "failed_extraction.csv"
FAILED_PRICES_CSV = PSM_OUTPUT_DIR / "failed_prices.csv"
EXTRACTION_LOG = PSM_OUTPUT_DIR / "extraction_log.txt"
SUPPLEMENT_CSV = PSM_OUTPUT_DIR / "supplement.csv"
# 段階2（画像PDFの目視読み取り）用にページ画像を書き出す先
RENDERED_PAGES_DIR = PSM_OUTPUT_DIR / "rendered_pages"

# --- キャッシュ（途中再開用）--------------------------------------------------
CACHE_DIR = BASE_DIR / "cache"
EXTRACT_CACHE_DIR = CACHE_DIR / "extract"  # PDFごとの抽出結果(JSON)
PRICE_CACHE_DIR = CACHE_DIR / "prices"  # 銘柄ごとの株価・分割(JSON)

# --- 処置変数 ---------------------------------------------------------------
# 本文にこれらが1回でも出現すれば ai_flag=1。英字は単語境界を課して部分一致を防ぐ
AI_KEYWORDS = [
    "AI",
    "人工知能",
    "機械学習",
    "深層学習",
    "ディープラーニング",
    "生成AI",
    "LLM",
    "大規模言語モデル",
]

# --- 画像PDF・抽出不能の判定 -------------------------------------------------
# 先頭の数頁で取れた文字数（空白除く）がこれ未満なら画像PDF等とみなす
IMAGE_PDF_MIN_CHARS = 200
IMAGE_PDF_CHECK_PAGES = 3
# サマリー頁として探索する先頭ページ数
SUMMARY_PAGES = 4

# --- 妥当性チェックの閾値 ---------------------------------------------------
LEVERAGE_RANGE = (0.0, 1.5)
EQUITY_RATIO_TOLERANCE_PT = 1.0  # サマリーの自己資本比率との許容差（%ポイント）
BPS_DEVIATION_TOLERANCE = 0.05  # bps自前計算と開示値の許容乖離率
CROSSCHECK_TOLERANCE = 0.005  # サマリーと本表の同一項目の許容乖離率

# --- yfinance ---------------------------------------------------------------
PRICE_LOOKBACK_DAYS = 20  # announce_date の何日前から株価を取るか
PRICE_MAX_RETRIES = 3
PRICE_RETRY_SLEEP_SEC = 5.0  # リトライ間隔（試行ごとに倍々）
PRICE_REQUEST_INTERVAL_SEC = 0.3

# --- 並列 -------------------------------------------------------------------
EXTRACT_WORKERS = 4
