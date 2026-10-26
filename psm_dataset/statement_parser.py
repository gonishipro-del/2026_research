"""連結（なければ単体）の貸借対照表・損益計算書の本表から項目を取り出す。

サマリー頁の値のクロスチェックと、サマリーで取れない項目（負債合計・非支配株主持分など）
の補完に使う。本表は「項目名 前期 当期」の行形式で並ぶ。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .numbers import UNIT_FACTORS, numbers_in

# 行頭一致で判定する。「流動資産合計」「負債純資産合計」を拾わないよう行頭に固定する
_BS_PATTERNS = {
    "total_assets": r"(?:資産合計|資産の部合計|総資産|資産総額)",
    "liabilities": r"(?:負債合計|負債の部合計|負債総額)",
    "net_assets": r"(?:純資産合計|純資産の部合計|資本合計|資本総額)",
    "nci": r"(?:非支配株主持分|非支配持分)",
    "subscription_rights": r"新株予約権",
    "owner_equity": r"(?:親会社の所有者に帰属する持分合計|親会社の所有者に帰属する持分|親会社所有者帰属持分合計|親会社株主持分合計)",
}
_PL_PATTERNS = {
    "ordinary_profit": r"経常(?:利益|損益|損失)(?!率)",
    "pretax_profit": r"(?:税金等調整前当期純|税引前当期純|税引前当期|税引前)(?:利益|損益|損失)",
    "net_income_parent": r"(?:親会社株主に帰属する当期(?:純)?|親会社の所有者に帰属する当期(?:純)?|当社株主に帰属する当期純)(?:利益|損益|損失)",
    "net_income": r"当期純(?:利益|損益|損失)",
}
_UNIT = re.compile(r"単位\s*[:：]\s*(百万円|千円|億円|円)")


@dataclass
class StatementResult:
    values: dict[str, list[float]] = field(default_factory=dict)  # 項目 → 行内の数値列（円換算）
    unit: str = ""
    notes: list[str] = field(default_factory=list)


def _line_numbers(line: str, pattern: re.Pattern[str]) -> list[float] | None:
    matched = pattern.match(line)
    if matched is None:
        return None
    values = numbers_in(line[matched.end():].split())
    return values or None


def _compile(patterns: dict[str, str]) -> dict[str, re.Pattern[str]]:
    return {key: re.compile(r"^\s*(?:※\d+\s*)?" + regex) for key, regex in patterns.items()}


_BS_RE = _compile(_BS_PATTERNS)
_PL_RE = _compile(_PL_PATTERNS)


def parse_statements(pages: list[list[str]], start_page: int = 0) -> StatementResult:
    """貸借対照表を見つけ、続く頁から損益計算書の項目を取り出す。"""
    result = StatementResult()
    bs_page = None

    for page_index in range(start_page, len(pages)):
        lines = pages[page_index]
        unit_match = _UNIT.search(" ".join(lines))

        found = False
        for line in lines:
            numbers = _line_numbers(line, _BS_RE["total_assets"])
            if numbers is not None and len(numbers) >= 1:
                found = True
                break
        if not found:
            continue

        bs_page = page_index
        if unit_match:
            result.unit = unit_match.group(1)
        break

    if bs_page is None:
        result.notes.append("貸借対照表の資産合計行を検出できず")
        return result

    factor = UNIT_FACTORS.get(result.unit, 1.0)
    if not result.unit:
        result.notes.append("本表の単位を検出できず（円扱い）")

    # 貸借対照表は資産の部・負債の部・純資産の部で複数頁にまたがることがある
    for page_index in range(bs_page, len(pages)):
        for line in pages[page_index]:
            for key, pattern in _BS_RE.items():
                if key in result.values:
                    continue
                numbers = _line_numbers(line, pattern)
                if numbers is not None:
                    result.values[key] = [value * factor for value in numbers]
        if "net_assets" in result.values and "liabilities" in result.values:
            break

    # 損益計算書は貸借対照表より後ろ。「経常利益」行を最初に見つけた頁以降を調べる
    for page_index in range(bs_page, len(pages)):
        page_unit = _UNIT.search(" ".join(pages[page_index]))
        page_factor = UNIT_FACTORS[page_unit.group(1)] if page_unit else factor
        for line in pages[page_index]:
            for key, pattern in _PL_RE.items():
                if key in result.values:
                    continue
                numbers = _line_numbers(line, pattern)
                if numbers is not None and len(numbers) >= 2:
                    result.values[key] = [value * page_factor for value in numbers]
        if "net_income_parent" in result.values or ("net_income" in result.values and page_index > bs_page + 2):
            break
    return result
