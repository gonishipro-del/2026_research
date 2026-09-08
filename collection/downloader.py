"""決算短信PDFの収集を担当するモジュール。

企業ページの取得 → 本決算短信の特定 → PDF ダウンロードまでを行い、
結果を metadata.jsonl に1社1行で追記する（再実行時はここから再開する）。
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .companies import Company
from .disclosure import parse_disclosures, select_annual_tanshin
from .kabupro_client import KabuproClient

_INVALID_FILENAME_CHARS = re.compile(r'[\/:*?"<>|\s]+')


def _safe_filename(text: str) -> str:
    return _INVALID_FILENAME_CHARS.sub("_", text).strip("_")


def load_metadata(path: Path) -> dict[str, dict]:
    """metadata.jsonl を読み込み、証券コードをキーにした辞書で返す。"""
    if not path.exists():
        return {}
    records: dict[str, dict] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            records[record["code"]] = record
    return records


class TanshinDownloader:
    """プライム上場企業の本決算短信PDFを一括ダウンロードする。"""

    def __init__(
        self,
        client: KabuproClient,
        pdf_dir: Path,
        metadata_path: Path,
        max_workers: int,
    ) -> None:
        self.client = client
        self.pdf_dir = pdf_dir
        self.metadata_path = metadata_path
        self.max_workers = max_workers
        self._write_lock = threading.Lock()

    # -- 1社分の処理 --------------------------------------------------------
    def _process(self, company: Company) -> dict:
        record = {
            "code": company.code,
            "name": company.name,
            "industry33": company.industry33,
            "scale": company.scale,
            "status": "",
            "title": "",
            "disclosed_on": "",
            "pdf_url": "",
            "pdf_path": "",
            "match_rule": "",
            "error": "",
        }

        try:
            html = self.client.fetch_company_page(company.code)
        except FileNotFoundError:
            record["status"] = "page_not_found"
            record["error"] = "決算プロに該当企業ページが存在しない"
            return record
        except Exception as error:  # noqa: BLE001
            record["status"] = "error"
            record["error"] = f"企業ページ取得失敗: {error}"
            return record

        disclosure, rule = select_annual_tanshin(parse_disclosures(html))
        if disclosure is None:
            record["status"] = "tanshin_not_found"
            record["error"] = "過去1年分の開示に本決算短信が見つからない"
            return record

        record["title"] = disclosure.title
        record["disclosed_on"] = (
            disclosure.disclosed_on.isoformat() if disclosure.disclosed_on else ""
        )
        record["pdf_url"] = disclosure.url
        record["match_rule"] = rule

        filename = (
            f"{company.code}_{_safe_filename(company.name)}"
            f"_{record['disclosed_on'].replace('-', '')}.pdf"
        )
        pdf_path = self.pdf_dir / filename
        record["pdf_path"] = str(pdf_path.relative_to(self.pdf_dir.parent.parent))

        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            record["status"] = "ok"
            return record

        try:
            content = self.client.fetch_binary(disclosure.url)
        except Exception as error:  # noqa: BLE001
            record["status"] = "error"
            record["error"] = f"PDF取得失敗: {error}"
            return record

        if not content:
            # 決算プロ側にファイルが存在せず0バイトで返るケース。再試行しても回復しない。
            record["status"] = "pdf_empty"
            record["error"] = "決算プロ上のPDFが0バイト（サイト側の欠損）"
            return record

        if not content.startswith(b"%PDF"):
            record["status"] = "error"
            record["error"] = "PDF形式ではない応答が返された"
            return record

        pdf_path.write_bytes(content)
        record["status"] = "ok"
        return record

    def _append_metadata(self, record: dict) -> None:
        with self._write_lock:
            with self.metadata_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # -- 一括処理 -----------------------------------------------------------
    def run(self, companies: list[Company], refresh: bool = False) -> list[dict]:
        """企業リストを処理し、全社分のメタデータを返す。"""
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)

        done = {} if refresh else load_metadata(self.metadata_path)
        targets = [c for c in companies if _needs_fetch(done.get(c.code))]
        skipped = len(companies) - len(targets)
        if skipped:
            print(f"  取得済みのためスキップ: {skipped} 社")

        results: list[dict] = []
        if targets:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(self._process, c): c for c in targets}
                for index, future in enumerate(as_completed(futures), start=1):
                    company = futures[future]
                    try:
                        record = future.result()
                    except Exception as error:  # noqa: BLE001
                        record = {
                            "code": company.code,
                            "name": company.name,
                            "industry33": company.industry33,
                            "scale": company.scale,
                            "status": "error",
                            "title": "",
                            "disclosed_on": "",
                            "pdf_url": "",
                            "pdf_path": "",
                            "match_rule": "",
                            "error": f"想定外のエラー: {error}",
                        }
                    self._append_metadata(record)
                    results.append(record)
                    done[company.code] = record
                    if index % 50 == 0 or index == len(targets):
                        print(f"  取得 {index}/{len(targets)} 社完了")

        return [done[c.code] for c in companies if c.code in done]


def _needs_fetch(record: dict | None) -> bool:
    """未処理、または一時的な失敗のみ再取得の対象にする。"""
    if record is None:
        return True
    return record.get("status") == "error"
