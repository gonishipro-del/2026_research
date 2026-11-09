"""データセット（収集した決算短信のまとまり）の管理。

1回の収集ごとに data/datasets/{日時}_{名前}/ を作り、そこに保存する。以前のデータセットは上書きしない。
分析の run は、設定の dataset に、どのデータセットを使ったかを記録する。

フォルダの中身:
  dataset.json      収集の条件・状態・件数
  master.xlsx       収集に使った上場銘柄一覧の写し
  metadata.jsonl    企業ごとの収集結果（本決算短信と、予想の資料）
  pdf/              本決算短信 {証券コード}_{企業名}_{YYYYMMDD}.pdf
  forecast/         予想の資料（直前の四半期短信、業績予想の修正）
  log.txt errors.csv progress.json   収集の進捗

アプリ以前に collect.py で集めた 20260904dataset/ は、読み取り専用のデータセットとして登録する（予想の資料はない）。
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import config
from core import state

NOT_COLLECTED, COLLECTING, COLLECTED, INTERRUPTED, FAILED = "未収集", "収集中", "収集済み", "中断", "失敗"
FINANCIAL_INDUSTRIES = ["銀行業", "保険業", "証券、商品先物取引業", "その他金融業"]
_SAFE = re.compile(r"[^0-9A-Za-z぀-ヿ一-鿿_-]+")


def _load() -> dict:
    return state.read_json(config.DATASETS_JSON, {})


def _save(registry: dict) -> None:
    state.write_json_atomic(config.DATASETS_JSON, registry)


def _lock():
    return state.file_lock(config.DATASETS_JSON.with_suffix(".lock"))


def ensure_legacy() -> None:
    """アプリ以前のデータセット（20260904dataset/）を登録する（まだなら）。"""
    with _lock():
        registry = _load()
        if config.LEGACY_DATASET_ID in registry or not config.LEGACY_DATASET_DIR.exists():
            return
        registry[config.LEGACY_DATASET_ID] = {
            "id": config.LEGACY_DATASET_ID, "label": "アプリ以前の収集（collect.py）",
            "path": str(config.LEGACY_DATASET_DIR), "created_at": "2026-09-04", "status": COLLECTED,
            "legacy": True, "has_forecast": False,
            "scope": {"market": "プライム（内国株式）", "excluded_industries": []},
            "note": "本決算短信のみ（予想の資料はない）。読み取り専用。",
        }
        _save(registry)


def all_datasets() -> list[dict]:
    ensure_legacy()
    return sorted(_load().values(), key=lambda entry: entry["created_at"], reverse=True)


def get(dataset_id: str) -> dict:
    ensure_legacy()
    registry = _load()
    if dataset_id not in registry:
        raise KeyError(f"データセット {dataset_id} が登録されていません")
    return registry[dataset_id]


def update(dataset_id: str, **fields: object) -> dict:
    with _lock():
        registry = _load()
        registry[dataset_id].update(fields, updated_at=state.now())
        _save(registry)
        entry = registry[dataset_id]
    if not entry.get("legacy"):
        state.write_json_atomic(path(dataset_id) / "dataset.json", entry)
    return entry


def create(label: str, *, market: str, excluded_industries: list[str], master_path: Path,
           limit: int | None = None) -> str:
    """新しいデータセットのフォルダを作って登録する。収集は core.collector が行う。"""
    clean = _SAFE.sub("_", label.strip()).strip("_") or "dataset"
    dataset_id = f"{datetime.now():%Y%m%d_%H%M%S}_{clean}"
    folder = config.DATASETS_DIR / dataset_id
    (folder / "pdf").mkdir(parents=True)
    (folder / "forecast").mkdir()
    shutil.copy2(master_path, folder / "master.xlsx")
    entry = {
        "id": dataset_id, "label": label.strip(), "path": str(folder), "created_at": state.now(),
        "status": NOT_COLLECTED, "legacy": False, "has_forecast": True,
        "scope": {"market": market, "excluded_industries": excluded_industries, "limit": limit},
        "master_file": master_path.name,
    }
    with _lock():
        registry = _load()
        registry[dataset_id] = entry
        _save(registry)
    state.write_json_atomic(folder / "dataset.json", entry)
    return dataset_id


def path(dataset_id: str) -> Path:
    return Path(get(dataset_id)["path"])


def pdf_dir(dataset_id: str) -> Path:
    return path(dataset_id) / "pdf"


def forecast_documents(dataset_id: str) -> dict[str, list[dict]]:
    """証券コード → 予想の資料（新しい順）。予想の資料がないデータセットは空。"""
    entry = get(dataset_id)
    metadata = path(dataset_id) / "metadata.jsonl"
    if not entry.get("has_forecast") or not metadata.exists():
        return {}
    documents: dict[str, list[dict]] = {}
    for record in read_metadata(dataset_id).values():
        documents[record["code"]] = [doc for doc in record.get("forecast_documents", []) if doc.get("path")]
    return documents


def read_metadata(dataset_id: str) -> dict[str, dict]:
    """metadata.jsonl（追記型）を読み、証券コードごとに最後の行を返す。"""
    metadata = path(dataset_id) / "metadata.jsonl"
    records: dict[str, dict] = {}
    if not metadata.exists():
        return records
    for line in metadata.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[record["code"]] = record
    return records


def collected_ids() -> list[str]:
    return [entry["id"] for entry in all_datasets() if entry["status"] == COLLECTED]
