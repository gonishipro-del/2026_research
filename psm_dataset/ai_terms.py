"""AI関連語の出現判定（処置変数）。"""
from __future__ import annotations

import re
import unicodedata

from . import config

_ASCII_ALNUM = re.compile(r"[A-Za-z0-9]")
_CJK_GAP = re.compile(r"(?<=[^\x00-\x7F])[\s　]+(?=[^\x00-\x7F])")
# URL・メールアドレス中の "ai" 等を拾わないよう、数え上げる前に除く
_URL_OR_MAIL = re.compile(r"https?://\S+|www\.\S+|\S+@\S+")


def _keyword_pattern(word: str) -> str:
    """英数字で始まる/終わる側にだけ単語境界を課す（RAIL, MAIN, AIG 等を除外）。"""
    prefix = r"(?<![A-Za-z0-9])" if _ASCII_ALNUM.fullmatch(word[0]) else ""
    suffix = r"(?![A-Za-z0-9])" if _ASCII_ALNUM.fullmatch(word[-1]) else ""
    return prefix + re.escape(unicodedata.normalize("NFKC", word)) + suffix


def build_pattern(keywords: list[str]) -> re.Pattern[str]:
    """全キーワードの選択パターン。長い語を先に試し、「生成AI」を「AI」と二重計上しない。"""
    ordered = sorted(keywords, key=len, reverse=True)
    return re.compile("|".join(_keyword_pattern(word) for word in ordered))


_PATTERN = build_pattern(config.AI_KEYWORDS)


def prepare_text(lines_by_page: list[list[str]]) -> str:
    """ページ別の行を連結し、改行で分断された日本語を結合して検索用テキストにする。"""
    text = "\n".join(line for page in lines_by_page for line in page)
    while True:
        replaced = _CJK_GAP.sub("", text)
        if replaced == text:
            break
        text = replaced
    return _URL_OR_MAIL.sub(" ", text)


def count_ai_terms(lines_by_page: list[list[str]]) -> int:
    return len(_PATTERN.findall(prepare_text(lines_by_page)))
