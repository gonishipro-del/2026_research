"""PDFのページ画像（pypdfium2 で描画、キャッシュ付き）。元のPDFは変更しない。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pypdfium2 as pdfium

import config

_SCALE = 1.6  # 72dpi × 1.6 ≒ 115dpi（数値が読める程度）


def render_page(pdf_path: Path, page: int) -> Path:
    """page は1始まり。PNG のパスを返す。"""
    stat = pdf_path.stat()
    key = f"{pdf_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{page}|{_SCALE}"
    out = config.PAGE_IMAGE_CACHE_DIR / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.png"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        if not 1 <= page <= len(document):
            raise IndexError(f"ページ {page} はありません（全{len(document)}頁）")
        document[page - 1].render(scale=_SCALE).to_pil().save(out)
    finally:
        document.close()
    return out


def page_count(pdf_path: Path) -> int:
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(document)
    finally:
        document.close()
