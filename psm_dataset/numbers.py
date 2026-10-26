"""数値トークンと単位の解釈。"""
from __future__ import annotations

import re

_NUMBER = re.compile(r"([△▲\-]?)(\d[\d,]*(?:\.\d+)?)")

UNIT_FACTORS = {"百万円": 1_000_000.0, "千円": 1_000.0, "億円": 100_000_000.0, "円": 1.0}
CURRENCY_UNITS = set(UNIT_FACTORS)


def to_number(token: str) -> float | None:
    """'△1,234' → -1234.0。数値でないトークン（'―' など）は None。"""
    matched = _NUMBER.fullmatch(token.strip())
    if matched is None:
        return None
    value = float(matched.group(2).replace(",", ""))
    return -value if matched.group(1) else value


def numbers_in(tokens: list[str]) -> list[float]:
    """トークン列から数値だけを順に取り出す。"""
    values = []
    for token in tokens:
        number = to_number(token)
        if number is not None:
            values.append(number)
    return values


def squash(text: str) -> str:
    """空白をすべて除く（文字間に空白が入ったラベルの照合用）。"""
    return re.sub(r"\s+", "", text)


def spaced_pattern(label: str) -> str:
    """ラベルの各文字の間に任意の空白を許す正規表現を返す。"""
    return r"\s*".join(re.escape(char) for char in label)
