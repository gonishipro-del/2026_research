"""決算短信PDFのページ別テキスト（キャッシュ付き）。

抽出は既存の psm_dataset.pdf_reader（pypdfium2、NFKC 正規化）を使う。
PDF の場所・サイズ・更新日時が同じならキャッシュを使う（元の PDF は変更しない）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import config
from psm_dataset.pdf_reader import extraction_failure_reason, read_pdf


@dataclass
class PageText:
    pages: list[list[str]]  # ページ → 行
    failure: str  # 画像PDF・抽出不能の理由。読めれば空文字


def _cache_path(path: Path) -> Path:
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    return config.TEXT_CACHE_DIR / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.json"


def page_text(path: Path) -> PageText:
    cache = _cache_path(path)
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return PageText(data["pages"], data["failure"])
    text = read_pdf(path)
    result = PageText(text.pages, extraction_failure_reason(text))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"file": str(path), "pages": result.pages, "failure": result.failure},
                                ensure_ascii=False), encoding="utf-8")
    return result
