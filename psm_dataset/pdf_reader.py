"""PDFのページ別テキスト抽出と、画像PDF・抽出不能の判定。

pdfminer は表の数値が連結されて分離できないため、位置情報から空白を補う
pypdfium2 の get_text_bounded を使う。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium

from . import config


@dataclass
class PdfText:
    pages: list[list[str]] = field(default_factory=list)  # ページ → 行（NFKC正規化済み）
    error: str = ""

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def char_count(self, first_pages: int | None = None) -> int:
        subset = self.pages if first_pages is None else self.pages[:first_pages]
        return sum(len(re.sub(r"\s+", "", line)) for page in subset for line in page)


def _normalize_lines(raw: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", raw).replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in normalized.split("\n"):
        line = re.sub(r"[ \t　]+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def read_pdf(path: Path) -> PdfText:
    """全ページのテキストを行単位で返す。開けない場合は error に理由を入れる。"""
    result = PdfText()
    try:
        document = pdfium.PdfDocument(str(path))
    except Exception as error:  # noqa: BLE001
        result.error = f"PDFを開けない: {error}"
        return result

    try:
        for index in range(len(document)):
            textpage = document[index].get_textpage()
            result.pages.append(_normalize_lines(textpage.get_text_bounded()))
    except Exception as error:  # noqa: BLE001
        result.error = f"テキスト抽出失敗: {error}"
    finally:
        document.close()
    return result


def extraction_failure_reason(text: PdfText) -> str:
    """画像PDF・抽出不能なら理由を、問題なければ空文字を返す。"""
    if text.error:
        return text.error
    chars = text.char_count(config.IMAGE_PDF_CHECK_PAGES)
    if chars < config.IMAGE_PDF_MIN_CHARS:
        return f"画像PDFまたは文字抽出不能（先頭{config.IMAGE_PDF_CHECK_PAGES}頁で{chars}文字）"
    return ""
