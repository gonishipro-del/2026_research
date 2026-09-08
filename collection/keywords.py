"""検索ワードの定義読み込みとマッチングを担当するモジュール。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_ASCII_ALNUM = re.compile(r"[A-Za-z0-9]")


@dataclass(frozen=True)
class Keyword:
    """検索ワード1件と、そのマッチ用正規表現。"""

    word: str
    pattern: re.Pattern[str]

    def count(self, text: str) -> int:
        return len(self.pattern.findall(text))

    def first_context(self, text: str, width: int = 40) -> str:
        """最初の出現箇所の周辺文字列を返す（目視確認用）。"""
        matched = self.pattern.search(text)
        if matched is None:
            return ""
        start = max(0, matched.start() - width)
        end = min(len(text), matched.end() + width)
        snippet = text[start:end].replace("\n", " ")
        return re.sub(r"\s+", " ", snippet).strip()


def _build_pattern(word: str) -> re.Pattern[str]:
    """ワードから正規表現を作る。

    英数字で始まる/終わるワードは、その側に単語境界を課す。
    これにより「AI」が MAINTENANCE や CHAIN に誤マッチするのを防ぎつつ、
    「AI活用」「生成AI」のように日本語と隣接する場合は正しく拾える。
    """
    core = re.escape(word)
    prefix = r"(?<![A-Za-z0-9])" if _ASCII_ALNUM.fullmatch(word[0]) else ""
    suffix = r"(?![A-Za-z0-9])" if _ASCII_ALNUM.fullmatch(word[-1]) else ""
    return re.compile(prefix + core + suffix, re.IGNORECASE)


def load_keywords(path: Path) -> list[Keyword]:
    """ワード定義ファイルを読み込む（# 始まりの行と空行は無視）。"""
    keywords: list[Keyword] = []
    seen: set[str] = set()

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        word = unicodedata.normalize("NFKC", line)
        if word in seen:
            continue
        seen.add(word)
        keywords.append(Keyword(word=word, pattern=_build_pattern(word)))

    if not keywords:
        raise ValueError(f"検索ワードが1件も定義されていません: {path}")
    return keywords
