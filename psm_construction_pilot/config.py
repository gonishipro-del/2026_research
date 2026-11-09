"""設定の集約。

フォルダ構成などの固定値はここに定義し、分析の設定値（対象業種・キーワード・閾値など）は
config.yaml から読む。run ごとに設定のスナップショット（output/run_*/config.yaml）を保存し、
各段階の処理は run のスナップショットを読む。設定を後から変えても過去の run は再現できる。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

PILOT_DIR = Path(__file__).resolve().parent
REPO_DIR = PILOT_DIR.parent
# 既存の psm_dataset（リポジトリ直下の paths.py に依存）を import できるようにする
for _path in (PILOT_DIR, REPO_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from core import stages  # noqa: E402

CONFIG_YAML = PILOT_DIR / "config.yaml"

# --- 入力 -------------------------------------------------------------------
DATA_DIR = PILOT_DIR / "data"
INPUTS_DIR = DATA_DIR / "inputs"
INPUT_DIRS = {
    "factors": INPUTS_DIR / "factors",  # Japan 5 Factors（日次）の CSV / zip
    "master": INPUTS_DIR / "master",  # JPX 上場銘柄一覧 data_j.xlsx
}
INPUT_ARCHIVE_DIRNAME = "archive"  # 差し替えで退避した旧版の置き場（各入力フォルダの下）
MANUAL_DIR = DATA_DIR / "manual"
MANUAL_PDF_DIR = MANUAL_DIR / "pdfs"  # アプリから手動アップロードした決算短信PDF

# --- キャッシュ ---------------------------------------------------------------
CACHE_DIR = DATA_DIR / "cache"
TEXT_CACHE_DIR = CACHE_DIR / "pdf_text"  # PDFごとのページ別テキスト(JSON)
PRICE_CACHE_DIR = CACHE_DIR / "prices"
PAGE_IMAGE_CACHE_DIR = CACHE_DIR / "page_images"

# --- 出力・状態 ---------------------------------------------------------------
OUTPUT_DIR = PILOT_DIR / "output"
STATE_DIR = PILOT_DIR / "state"
STATE_JSON = STATE_DIR / "state.json"
STATE_LOCK = STATE_DIR / ".state.lock"
MANUAL_OVERRIDES_CSV = STATE_DIR / "manual_overrides.csv"  # 手動入力ログ（追記専用）
RUN_HISTORY_CSV = STATE_DIR / "run_history.csv"
CONFIG_HISTORY_CSV = STATE_DIR / "config_history.csv"
INPUTS_JSON = STATE_DIR / "inputs.json"  # 現在の入力ファイルと検証結果
INPUT_HISTORY_CSV = STATE_DIR / "input_history.csv"

TRIAL_NOTICE = "試運転の結果。最終結果ではない"
CURRENCY_NOTICE = (
    "ファクター（Kenneth French Data Library, Japan 5 Factors）と RF は米ドル建て。"
    "既定では、株価の日次リターンをドル円レート（yfinance の JPY=X）でドル建てに換算して単位を揃える（段階0で変更可）。"
)

# --- 分析の設定値（config.yaml が無い・項目が欠けている場合の既定値）-----------
DEFAULTS: dict = {
    # 対象
    "market": "プライム（内国株式）",
    "target_industries": ["建設業"],
    "pdf_dir": "../20260904dataset/pdf",  # 決算短信PDFの既存フォルダ（コピーしない）
    # 処置
    "ai_keywords": ["AI", "人工知能", "機械学習", "深層学習", "ディープラーニング",
                    "生成AI", "LLM", "大規模言語モデル"],
    "treatment_threshold": 1,  # 有効ヒット数がこれ以上なら ai_flag=1
    "min_group_size": 10,  # 処置群・対照群がこれ未満なら警告して確認を求める
    "hit_context_chars": 30,  # ヒット前後に保存する文字数
    # マッチング
    "covariates": ["log_total_assets", "roa_ordinary", "leverage", "pbr", "profit_change"],
    "caliper_sd": 0.2,  # キャリパー = ロジット傾向スコアの標準偏差 × この値
    "replacement_main": False,  # 主分析を復元ありにするか（凍結時に確定する）
    # イベントスタディ
    # 1日・3日・30日・60日・90日（営業日、発表日=0）。窓ごとに CAR と ATT を出す
    "event_windows": [[0, 0], [-1, 1], [0, 29], [0, 59], [0, 89]],
    "event_window_main": [-1, 1],
    "placebo_window": [-15, -11],
    "estimation_window": [-250, -21],
    "min_estimation_obs": 120,
    "include_alpha": True,
    # 株価リターンの通貨。"usd"＝ドル円レートでドル建てに換算してファクター（米ドル建て）と揃える、"jpy"＝換算しない
    "stock_currency": "usd",
    # 評価
    "caliper_grid": [0.1, 0.2, 0.3],
    "bootstrap_reps": 2000,
    "rosenbaum_gammas": [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0],
    # 再現性
    "seed": 20261105,
}

# 設定項目ごとに、変更したとき最初に影響を受ける段階（そこから下流を「要再実行」にする）
SETTING_FIRST_STAGE = {
    "market": stages.INGEST,
    "target_industries": stages.INGEST,
    "pdf_dir": stages.INGEST,
    "ai_keywords": stages.AI_COUNT,
    "treatment_threshold": stages.AI_COUNT,
    "min_group_size": stages.AI_COUNT,
    "hit_context_chars": stages.AI_COUNT,
    "covariates": stages.MATCHING,
    "caliper_sd": stages.MATCHING,
    "replacement_main": stages.FREEZE,
    "seed": stages.MATCHING,
    "event_windows": stages.CAR,
    "event_window_main": stages.ATT,
    "placebo_window": stages.CAR,
    "estimation_window": stages.CAR,
    "min_estimation_obs": stages.CAR,
    "include_alpha": stages.CAR,
    "stock_currency": stages.CAR,
    "caliper_grid": stages.EVALUATE,
    "bootstrap_reps": stages.ATT,
    "rosenbaum_gammas": stages.EVALUATE,
}

_YAML_HEADER = "# 建設業 PSM 試運転アプリの設定。項目の意味は README.md の「設定の意味」を参照。\n"


def with_defaults(values: dict | None) -> dict:
    merged = copy.deepcopy(DEFAULTS)
    merged.update(values or {})
    return merged


# 後から追加した項目で、それ以前の設定ファイルに無い場合に使う値（追加前の動きを変えないため）
LEGACY_VALUES = {"stock_currency": "jpy"}


def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_YAML
    if not path.exists():
        return with_defaults({})
    with path.open(encoding="utf-8") as handle:
        stored = yaml.safe_load(handle) or {}
    return with_defaults({**{k: v for k, v in LEGACY_VALUES.items() if k not in stored}, **stored})


def save_config(values: dict, path: Path | None = None) -> None:
    path = path or CONFIG_YAML
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(values, allow_unicode=True, sort_keys=False)
    path.write_text(_YAML_HEADER + body, encoding="utf-8")


def resolve_pdf_dir(values: dict) -> Path:
    """pdf_dir は config.yaml からの相対パスでも絶対パスでもよい。"""
    raw = Path(values["pdf_dir"])
    return raw if raw.is_absolute() else (PILOT_DIR / raw).resolve()
