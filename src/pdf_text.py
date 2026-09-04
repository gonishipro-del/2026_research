"""PDFからのテキスト抽出と、検索用の正規化を担当するモジュール。

抽出結果は text/ 配下にキャッシュするため、ワードを変更して再集計する際は
PDFの再解析が不要になる。
"""
from __future__ import annotations

import logging
import re
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pdfminer.high_level import extract_text

# pdfminer が出す大量の警告を抑制する
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# 日本語の文字と文字の間に入った改行・空白（レイアウト由来）を取り除くための正規表現
_CJK_GAP = re.compile(r"(?<=[^\x00-\x7F])[\s　]+(?=[^\x00-\x7F])")
_MULTI_SPACE = re.compile(r"[ \t　]+")


def extract_pdf_text(pdf_path: Path) -> str:
    """PDF本文のテキストを取り出す（失敗時は例外を送出）。"""
    return extract_text(str(pdf_path))


def normalize_for_search(text: str) -> str:
    """検索しやすいようにテキストを正規化する。

    - NFKC 正規化で全角英数（ＡＩ）を半角（AI）へ揃える
    - 日本語同士の間に挟まった改行・空白を除去し、行折り返しで
      分断された単語（例:「人工知\n能」）も拾えるようにする
    - 英数字の前後の空白は単語境界の判定に使うため残す
    """
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    # 「日本語 + 空白 + 日本語」は繰り返し適用しないと連鎖を消しきれない
    while True:
        replaced = _CJK_GAP.sub("", normalized)
        if replaced == normalized:
            break
        normalized = replaced
    normalized = _MULTI_SPACE.sub(" ", normalized)
    return normalized


def _extract_worker(task: tuple[str, str, str]) -> dict:
    """プロセス並列用のワーカー。(コード, PDFパス, 出力パス) を処理する。"""
    code, pdf_path, text_path = task
    try:
        text = extract_pdf_text(Path(pdf_path))
    except Exception as error:  # noqa: BLE001
        return {"code": code, "chars": 0, "error": f"テキスト抽出失敗: {error}"}

    Path(text_path).write_text(text, encoding="utf-8")
    return {"code": code, "chars": len(text), "error": ""}


def extract_all(
    tasks: list[tuple[str, Path, Path]],
    workers: int,
    refresh: bool = False,
) -> dict[str, dict]:
    """複数PDFのテキストを並列抽出し、コード別の結果を返す。

    tasks は (証券コード, PDFパス, テキスト出力パス) のリスト。
    既にキャッシュがある場合は再抽出しない。
    """
    results: dict[str, dict] = {}
    pending: list[tuple[str, str, str]] = []

    for code, pdf_path, text_path in tasks:
        if not refresh and text_path.exists():
            results[code] = {
                "code": code,
                "chars": len(text_path.read_text(encoding="utf-8")),
                "error": "",
            }
            continue
        pending.append((code, str(pdf_path), str(text_path)))

    if pending:
        print(f"  テキスト抽出対象: {len(pending)} 件（キャッシュ利用: {len(results)} 件）")
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for index, result in enumerate(
                executor.map(_extract_worker, pending, chunksize=4), start=1
            ):
                results[result["code"]] = result
                if index % 100 == 0 or index == len(pending):
                    print(f"  抽出 {index}/{len(pending)} 件完了")
    else:
        print(f"  すべてキャッシュを利用（{len(results)} 件）")

    return results
