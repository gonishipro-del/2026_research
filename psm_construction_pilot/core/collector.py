"""データセットの収集：決算プロ（ke.kabupro.jp）から、本決算短信と予想の資料をダウンロードする。

通信・開示一覧の解析・本決算短信の選び方は、既存の collection パッケージ（collect.py と同じもの）を使い、
アクセス間隔・同時接続数も同じ設定にする。企業ページには過去1年分の開示が載っている。

企業ごとに:
  1. 本決算短信（最新の通期）を pdf/ に保存する
  2. 本決算の発表日より前の、直前の四半期短信と、それ以降の業績予想の修正を forecast/ に保存する
  3. 結果を metadata.jsonl に1行追記する（途中で止まっても、次回は未処理・失敗の企業から再開する）

対象は、データセットに写した上場銘柄一覧（master.xlsx）のうち、指定の市場で、除外業種と種類株式を除いた企業。

単体実行: python -m core.collector --dataset DATASET_ID
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import config  # noqa: F401  （sys.path の設定）
from collection import config as collection_config
from collection.disclosure import parse_disclosures, select_annual_tanshin
from collection.kabupro_client import KabuproClient
from core import datasets, forecast, master, state
from core.progress import Progress

_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')
RETRY_STATUSES = {"error"}


def _safe(text: str) -> str:
    return _UNSAFE.sub("_", text).strip("_")


def target_companies(dataset_id: str) -> list[dict]:
    entry = datasets.get(dataset_id)
    scope = entry["scope"]
    frame = master.load(datasets.path(dataset_id) / "master.xlsx")
    frame = frame[(frame["市場・商品区分"] == scope["market"])
                  & ~frame["33業種区分"].isin(scope.get("excluded_industries") or [])]
    frame = frame[~frame["コード"].str.len().eq(5) & ~frame["銘柄名"].str.contains("種類株式")]
    frame = frame.sort_values("コード")
    if scope.get("limit"):
        frame = frame.head(int(scope["limit"]))
    return [{"code": row["コード"], "name": row["銘柄名"], "industry33": row["33業種区分"]}
            for _, row in frame.iterrows()]


def _client() -> KabuproClient:
    return KabuproClient(collection_config.KABUPRO_BASE_URL, collection_config.USER_AGENT,
                         collection_config.REQUEST_INTERVAL_SEC, collection_config.REQUEST_TIMEOUT_SEC,
                         collection_config.MAX_RETRIES, collection_config.RETRY_WAIT_SEC)


def _download(client: KabuproClient, url: str, path: Path) -> str:
    """保存できれば空文字、できなければ理由。すでにあれば取り直さない。"""
    if path.exists() and path.stat().st_size > 0:
        return ""
    content = client.fetch_binary(url)
    if not content.startswith(b"%PDF"):
        return "PDF形式ではない応答"
    path.write_bytes(content)
    return ""


def process(client: KabuproClient, company: dict, folder: Path) -> dict:
    record = {**company, "status": "", "error": "", "annual": None, "forecast_documents": [],
              "collected_at": state.now()}
    try:
        html = client.fetch_company_page(company["code"])
    except FileNotFoundError:
        return {**record, "status": "page_not_found", "error": "決算プロに企業ページがない"}
    disclosures = parse_disclosures(html)
    annual, rule = select_annual_tanshin(disclosures)
    if annual is None or annual.disclosed_on is None:
        return {**record, "status": "tanshin_not_found", "error": "過去1年分の開示に本決算短信がない"}

    annual_path = folder / "pdf" / f"{company['code']}_{_safe(company['name'])}_{annual.disclosed_on:%Y%m%d}.pdf"
    problem = _download(client, annual.url, annual_path)
    record["annual"] = {"title": annual.title, "date": annual.disclosed_on.isoformat(), "url": annual.url,
                        "path": "" if problem else str(annual_path), "match_rule": rule, "error": problem}
    if problem:
        return {**record, "status": "error", "error": f"本決算短信: {problem}"}

    for index, document in enumerate(forecast.select_documents(disclosures, annual.disclosed_on)):
        kind = forecast.REVISION if document.category == "業績" else forecast.QUARTERLY
        suffix = "rev" if kind == forecast.REVISION else "q"
        path = folder / "forecast" / f"{company['code']}_{document.disclosed_on:%Y%m%d}_{suffix}{index}.pdf"
        try:
            problem = _download(client, document.url, path)
        except Exception as error:  # noqa: BLE001  予想の資料が1件取れなくても本決算短信は使える
            problem = f"{type(error).__name__}: {error}"
        record["forecast_documents"].append({
            "kind": kind, "title": document.title, "date": document.disclosed_on.isoformat(), "url": document.url,
            "path": "" if problem else str(path), "error": problem})
    return {**record, "status": "ok"}


def _needs_fetch(record: dict | None) -> bool:
    return record is None or record.get("status") in RETRY_STATUSES


def run(dataset_id: str, *, echo: bool = False) -> dict:
    folder = datasets.path(dataset_id)
    progress = Progress.in_directory(folder, echo=echo)
    datasets.update(dataset_id, status=datasets.COLLECTING, pid=os.getpid(), started_at=state.now(), message="収集中")
    try:
        companies = target_companies(dataset_id)
        done = datasets.read_metadata(dataset_id)
        todo = [company for company in companies if _needs_fetch(done.get(company["code"]))]
        progress.log(f"対象 {len(companies)}社 / 取得済みのため飛ばす {len(companies) - len(todo)}社 / 今回取得 {len(todo)}社")
        client, lock = _client(), threading.Lock()
        metadata_path = folder / "metadata.jsonl"
        with ThreadPoolExecutor(max_workers=collection_config.MAX_WORKERS) as executor:
            futures = {executor.submit(process, client, company, folder): company for company in todo}
            for count, future in enumerate(as_completed(futures), start=1):
                company = futures[future]
                try:
                    record = future.result()
                except Exception as error:  # noqa: BLE001  1社の失敗で全体を止めない
                    record = {**company, "status": "error", "error": f"{type(error).__name__}: {error}",
                              "annual": None, "forecast_documents": [], "collected_at": state.now()}
                if record["status"] != "ok":
                    progress.error(company["code"], company["name"], record["status"], record["error"])
                with lock, metadata_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                progress.update(count, len(todo), f"{company['code']} {company['name']}")
        progress.update(len(todo), len(todo), "完了", force=True)
        counts = summarize(dataset_id, companies)
        entry = datasets.update(dataset_id, status=datasets.COLLECTED, pid=None, finished_at=state.now(),
                                counts=counts, message=_message(counts))
        progress.log("終了: " + entry["message"])
        return entry
    except Exception as error:  # noqa: BLE001  状態とログに残して返す
        progress.log("[失敗]\n" + traceback.format_exc())
        return datasets.update(dataset_id, status=datasets.FAILED, pid=None, message=f"{type(error).__name__}: {error}")


def summarize(dataset_id: str, companies: list[dict] | None = None) -> dict:
    records = datasets.read_metadata(dataset_id)
    codes = [c["code"] for c in companies] if companies else list(records)
    rows = [records[code] for code in codes if code in records]
    with_forecast = [r for r in rows if any(doc.get("path") for doc in r.get("forecast_documents", []))]
    return {
        "companies": len(codes), "processed": len(rows),
        "annual_ok": sum(r["status"] == "ok" for r in rows),
        "with_forecast_documents": len(with_forecast),
        "tanshin_not_found": sum(r["status"] == "tanshin_not_found" for r in rows),
        "page_not_found": sum(r["status"] == "page_not_found" for r in rows),
        "errors": sum(r["status"] == "error" for r in rows),
    }


def _message(counts: dict) -> str:
    return (f"対象{counts['companies']}社 / 本決算短信{counts['annual_ok']}社 / 予想の資料あり{counts['with_forecast_documents']}社 / "
            f"短信なし{counts['tanshin_not_found']}社 / ページなし{counts['page_not_found']}社 / エラー{counts['errors']}社")


def main() -> int:
    parser = argparse.ArgumentParser(description="データセットの収集")
    parser.add_argument("--dataset", required=True)
    args = parser.parse_args()
    entry = run(args.dataset, echo=True)
    print(entry["status"], entry.get("message", ""))
    return 0 if entry["status"] == datasets.COLLECTED else 1


if __name__ == "__main__":
    sys.exit(main())
