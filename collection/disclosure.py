"""決算プロの企業ページ HTML から適時開示一覧を解析するモジュール。

ページには過去1年分の適時開示が新しい順に並んでおり、各行は
「分類 / 表題 / 情報開示日」と PDF への直リンクを持つ。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup

_DATE_PATTERN = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")

# 決算短信本体ではない資料を弾くための語
_EXCLUDE_WORDS = (
    "訂正",
    "補足",
    "説明会",
    "プレゼンテーション",
    "説明資料",
    "参考資料",
    "概要",
    "データ集",
    "ハイライト",
)


@dataclass(frozen=True)
class Disclosure:
    """適時開示1件分。"""

    category: str
    title: str
    url: str
    disclosed_on: date | None


def parse_disclosures(html: str) -> list[Disclosure]:
    """企業ページの HTML から適時開示の一覧を抽出する。"""
    soup = BeautifulSoup(html, "html.parser")
    disclosures: list[Disclosure] = []

    for row in soup.find_all("tr"):
        header = row.find("th")
        if header is None:
            continue

        pdf_url = ""
        for anchor in row.find_all("a", href=True):
            href = anchor["href"].strip()
            if href.lower().endswith(".pdf"):
                pdf_url = href
                break
        if not pdf_url:
            continue

        cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
        disclosed_on = None
        title = ""
        for text in cells:
            matched = _DATE_PATTERN.search(text)
            if matched and len(text) <= 12:
                year, month, day = (int(x) for x in matched.groups())
                disclosed_on = date(year, month, day)
            elif len(text) > len(title):
                title = text

        disclosures.append(
            Disclosure(
                category=header.get_text(strip=True),
                title=title,
                url=pdf_url,
                disclosed_on=disclosed_on,
            )
        )

    return disclosures


def _is_excluded(title: str) -> bool:
    return any(word in title for word in _EXCLUDE_WORDS)


def select_annual_tanshin(
    disclosures: list[Disclosure],
) -> tuple[Disclosure | None, str]:
    """通期（本決算）の決算短信を1件選ぶ。

    戻り値は (開示, 採用ルール)。該当がなければ (None, "")。
    分類が「短信」の行のうち、四半期や訂正・説明会資料などを除いた
    最新のものを採用する。表題は一覧上で末尾が省略されることがあるため、
    厳密な条件で見つからない場合は緩い条件でもう一度探す。
    """
    candidates = [
        d
        for d in disclosures
        if d.category == "短信"
        and "四半期" not in d.title
        and "中間" not in d.title
        and not _is_excluded(d.title)
    ]

    strict = [d for d in candidates if "決算短信" in d.title]
    if strict:
        return _latest(strict), "strict"

    loose = [d for d in candidates if "決算" in d.title]
    if loose:
        return _latest(loose), "loose"

    return None, ""


def _latest(disclosures: list[Disclosure]) -> Disclosure:
    """開示日が最も新しいものを返す（日付不明は最古扱い）。"""
    return max(disclosures, key=lambda d: d.disclosed_on or date.min)
