"""決算短信サマリー頁（1頁目）の表を解析する。

サマリー頁は「見出し行 → 単位行 → 当期の数値行」という形に揃っているため、
単位行を手がかりに表を見つけ、見出しの項目順と数値の列を対応づける。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .numbers import CURRENCY_UNITS, UNIT_FACTORS, spaced_pattern, squash, to_number

_UNIT_TOKENS = CURRENCY_UNITS | {"銭", "%"}
_PERIOD_ROW = re.compile(r"^(\d{4})年(\d{1,2})月期?\s*(.*)$")

# 経営成績表の見出し項目。左から順に現れた項目を数値列へ対応づける。
# 「親会社株主に帰属する当期純利益」が「当期純利益」に吸われないよう長い語を先に置く
_PL_LABELS = [
    ("net_income_parent", r"親会社株主に帰属する当期(?:純)?(?:利益|損益|損失)|親会社の所有者に帰属する当期(?:純)?(?:利益|損益)|"
                          r"当社株主に帰属する当期純(?:利益|損益)|親会社の所有者に帰属する(?:純)?(?:利益|損益)|"
                          r"親会社株主に帰属する(?:純)?(?:利益|損益)"),
    ("pretax_profit", r"税金等調整前当期純(?:利益|損益|損失)|税引前当期純(?:利益|損益|損失)|税引前当期(?:利益|損益)|税引前(?:利益|損益)"),
    ("ordinary_profit", r"経常利益|経常損益"),
    ("operating_profit", r"営業利益|営業損益|事業利益"),
    ("net_income", r"当期純(?:利益|損益)|当期(?:利益|損益)"),
    ("comprehensive", r"包括利益"),
    ("sales", r"売上高|売上収益|営業収益|経常収益|営業総収入|営業収入|収益"),
]
# 財政状態表の見出し項目（1株当たり・比率を先に判定して「純資産」「自己資本」への誤吸収を防ぐ）
_BS_LABELS = [
    ("bps", r"1株当たり(?:親会社所有者帰属持分|親会社の所有者に帰属する持分|親会社株主持分|純資産|株主資本|自己資本|資本)"),
    ("equity_ratio", r"自己資本比率|親会社所有者帰属持分比率|親会社の所有者に帰属する持分比率|株主資本比率|親会社株主持分比率"),
    ("owner_equity", r"親会社所有者帰属持分|親会社の所有者に帰属する持分|親会社株主持分|自己資本|株主資本"),
    ("total_assets", r"総資産|資産合計|資産総額"),
    ("net_assets", r"純資産|資本合計|資本総額"),
]
_PL_KEYWORDS = ("営業利益", "経常利益", "税引前", "売上高", "売上収益", "営業収益", "経常収益", "収益")
_RATIO_KEYS = ("equity_ratio", "bps")


@dataclass
class SummaryTable:
    kind: str  # "pl" / "bs"
    scope: str  # "consolidated" / "non_consolidated" / ""（不明）
    period: str  # 例: 2026年3月期
    unit_index: int = 0
    values: dict[str, float | None] = field(default_factory=dict)  # 金額は円単位
    raw_header: str = ""
    raw_row: str = ""
    warning: str = ""


@dataclass
class SummaryResult:
    tables: dict[str, SummaryTable] = field(default_factory=dict)
    owner_equity_ref: float | None = None  # 「(参考)自己資本」(円)
    shares_issued: float | None = None
    treasury_shares: float | None = None
    notes: list[str] = field(default_factory=list)


def _merge_yen_sen(tokens: list[str]) -> list[str]:
    """「円 銭」の2トークンは1列（例: 6,511.31）なので1つにまとめる。"""
    merged, index = [], 0
    while index < len(tokens):
        if tokens[index] == "円" and index + 1 < len(tokens) and tokens[index + 1] == "銭":
            merged.append("円銭")
            index += 2
        else:
            merged.append(tokens[index])
            index += 1
    return merged


def _unit_tokens(line: str) -> list[str]:
    return [token.strip("()") for token in line.replace("(", " ").replace(")", " ").split()]


def _is_unit_line(line: str) -> bool:
    tokens = _unit_tokens(line)
    return bool(tokens) and all(token in _UNIT_TOKENS for token in tokens)


def _scope_of(lines: list[str], unit_index: int) -> str:
    """表の直前の見出し（（１）連結経営成績 など）から連結/単体を判定する。"""
    for line in reversed(lines[max(0, unit_index - 12):unit_index]):
        compact = squash(line)
        if "財政状態" in compact or "経営成績" in compact:
            if "非連結" in compact or "個別" in compact or "単体" in compact:
                return "non_consolidated"
            if "連結" in compact:
                return "consolidated"
            return ""
    return ""


def _header_lines(lines: list[str], unit_index: int) -> list[str]:
    """単位行の直前から、前の表の数値行・注記に当たるまでを見出しとして集める。"""
    collected = []
    for line in reversed(lines[max(0, unit_index - 9):unit_index]):
        if _PERIOD_ROW.match(line) or line.startswith(("(注)", "(参考)")):
            break
        collected.append(line)
    return list(reversed(collected))


def _labels_in(header: str, label_defs: list[tuple[str, str]]) -> list[str]:
    """見出し文字列に現れる項目名を左から順に返す。"""
    pattern = re.compile("|".join(f"(?P<{key}>{regex})" for key, regex in label_defs))
    return [match.lastgroup for match in pattern.finditer(header)]


def _build_table(lines: list[str], unit_index: int) -> SummaryTable | None:
    tokens = _merge_yen_sen(_unit_tokens(lines[unit_index]))
    currency = [token for token in tokens if token in CURRENCY_UNITS]
    if not currency:
        return None  # 比率だけの表（総資産経常利益率など）は対象外

    header = squash(" ".join(_header_lines(lines, unit_index)))
    row_match = _PERIOD_ROW.match(lines[unit_index + 1])
    row_tokens = row_match.group(3).split()

    is_bs = "総資産" in header or "資産合計" in header
    if is_bs and any(word in header for word in ("純資産", "資本", "持分")):
        kind, label_defs = "bs", _BS_LABELS
    elif not is_bs and any(word in header for word in _PL_KEYWORDS):
        kind, label_defs = "pl", _PL_LABELS
    else:
        return None

    table = SummaryTable(
        kind=kind,
        scope=_scope_of(lines, unit_index),
        period=f"{row_match.group(1)}年{int(row_match.group(2))}月期",
        unit_index=unit_index,
        raw_header=header,
        raw_row=lines[unit_index + 1],
    )

    if len(row_tokens) == len(tokens):
        if kind == "pl":  # 金額の列だけを拾う（増減率の列は読み飛ばす）
            columns = [j for j, token in enumerate(tokens) if token in CURRENCY_UNITS]
        else:
            columns = list(range(len(tokens)))
    else:
        # 列数が合わない場合は経営成績を「金額＋増減率」の対と仮定し、財政状態は先頭から順に読む
        table.warning = f"単位行{len(tokens)}列と数値行{len(row_tokens)}列が不一致"
        columns = list(range(0, len(row_tokens), 2)) if kind == "pl" else list(range(len(row_tokens)))

    factor = UNIT_FACTORS[currency[0]]
    for position, label in enumerate(_labels_in(header, label_defs)):
        number = None
        if position < len(columns) and columns[position] < len(row_tokens):
            number = to_number(row_tokens[columns[position]])
        if number is not None and label not in _RATIO_KEYS:
            number *= factor
        table.values.setdefault(label, number)
    return table


def _find_tables(lines: list[str]) -> list[SummaryTable]:
    tables = []
    for index, line in enumerate(lines[:-1]):
        if _is_unit_line(line) and _PERIOD_ROW.match(lines[index + 1]):
            table = _build_table(lines, index)
            if table is not None:
                tables.append(table)
    return tables


def _find_share_count(text: str, label: str) -> float | None:
    """「期末発行済株式数」「期末自己株式数」の直後に現れる当期の株式数を探す。"""
    pattern = re.compile(
        spaced_pattern(label) + r"[^\d]{0,60}?\d{4}年\d{1,2}月期\s*([\d,]+)\s*(千?)\s*株"
    )
    matched = pattern.search(text)
    if matched is None:
        return None
    value = float(matched.group(1).replace(",", ""))
    return value * 1000 if matched.group(2) else value


def parse_summary(summary_lines: list[str]) -> SummaryResult:
    """サマリー頁の行から、連結（なければ単体）の財務数値・株式数を取り出す。"""
    result = SummaryResult()
    tables = _find_tables(summary_lines)

    for kind in ("pl", "bs"):
        candidates = [table for table in tables if table.kind == kind]
        if not candidates:
            continue
        # 連結が先に載る。連結の見出しがなければ最初の表を使う
        chosen = next((t for t in candidates if t.scope == "consolidated"), candidates[0])
        result.tables[kind] = chosen
        if chosen.warning:
            result.notes.append(f"サマリー{kind}表: {chosen.warning}")

    bs = result.tables.get("bs")
    if bs is not None:
        window = " ".join(summary_lines[bs.unit_index + 2:bs.unit_index + 6])
        matched = re.search(
            r"自己資本\s*\d{4}年\d{1,2}月期\s*([△▲\-]?[\d,]+)\s*(百万円|千円|億円|円)", window
        )
        if matched:
            result.owner_equity_ref = (to_number(matched.group(1)) or 0) * UNIT_FACTORS[matched.group(2)]

    text = "\n".join(summary_lines)
    result.shares_issued = _find_share_count(text, "期末発行済株式数")
    result.treasury_shares = _find_share_count(text, "期末自己株式数")
    return result
