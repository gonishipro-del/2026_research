"""決算短信PDF1本から、PSM用の財務項目とAI関連語の出現数を抽出する。

第一ソースはサマリー頁の表、BS/PL本表はクロスチェックと補完に使う。
抽出結果は JSON にできる辞書で返し、値の取捨や備考の組み立ては行わない
（それは assemble.py の責務）。金額はすべて円単位。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config
from .ai_terms import count_ai_terms
from .pdf_reader import PdfText, extraction_failure_reason, read_pdf
from .statement_parser import StatementResult, parse_statements
from .summary_parser import SummaryResult, parse_summary

_STANDARD = re.compile(r"[〔\[](日本基準|IFRS|米国基準|国際財務報告基準|国際会計基準|US ?GAAP)[〕\]]", re.IGNORECASE)


def detect_title_info(first_page: list[str]) -> dict:
    """表題行から会計基準・通期/四半期・連結/単体を判定する。"""
    head = "".join(first_page[:8]).replace(" ", "")
    standard_match = _STANDARD.search(head)
    standard = standard_match.group(1).upper() if standard_match else ""
    if standard in ("国際財務報告基準", "国際会計基準"):
        standard = "IFRS"
    return {
        "title": " ".join(first_page[:2]),
        "standard": standard,
        "is_annual": "四半期" not in head and "中間" not in head,
        "title_non_consolidated": "非連結" in head or "(個別)" in head,
    }


def _summary_lines(text: PdfText) -> list[str]:
    lines: list[str] = []
    for page in text.pages[: config.SUMMARY_PAGES]:
        lines.extend(page)
    return lines


def _pick_current(values: list[float] | None, column: int) -> float | None:
    """行内の数値列（通常は前期・当期の並び）から当期の値を選ぶ。column は 0 か -1。"""
    if not values:
        return None
    return values[column] if len(values) >= 2 else values[0]


def _current_column(statement: StatementResult, summary_assets: float | None) -> int:
    """資産合計行の数値がサマリーの総資産と一致する列を当期とみなす（既定は末尾列=-1）。

    当期が左（0列目）に載る書式だけを検出すればよいので、戻り値は 0 か -1。
    """
    assets = statement.values.get("total_assets")
    if not assets or len(assets) < 2 or not summary_assets:
        return -1
    first_matches = abs(assets[0] - summary_assets) / summary_assets < 0.001
    last_matches = abs(assets[-1] - summary_assets) / summary_assets < 0.001
    return 0 if first_matches and not last_matches else -1


def _flatten(summary: SummaryResult, statement: StatementResult, column: int) -> dict:
    pl = summary.tables.get("pl")
    bs = summary.tables.get("bs")
    sm = {}
    if pl is not None:
        for key in ("ordinary_profit", "pretax_profit", "net_income_parent", "net_income"):
            sm[key] = pl.values.get(key)
    if bs is not None:
        for key in ("total_assets", "net_assets", "equity_ratio", "bps", "owner_equity"):
            sm[key] = bs.values.get(key)
    if sm.get("owner_equity") is None and summary.owner_equity_ref is not None:
        sm["owner_equity"] = summary.owner_equity_ref

    st = {key: _pick_current(values, column) for key, values in statement.values.items()}
    return {"summary": sm, "statement": st}


def extract_pdf(path: Path) -> dict:
    """PDFを読み、抽出結果の辞書を返す。画像PDF等で読めない場合は status="failed"。"""
    text = read_pdf(path)
    result: dict = {"filename": path.name, "page_count": text.page_count, "notes": []}

    reason = extraction_failure_reason(text)
    if reason:
        result.update(status="failed", fail_reason=reason)
        return result

    result.update(detect_title_info(text.pages[0]))
    summary = parse_summary(_summary_lines(text))
    statement = parse_statements(text.pages, start_page=1)

    bs = summary.tables.get("bs")
    summary_assets = bs.values.get("total_assets") if bs is not None else None
    column = _current_column(statement, summary_assets)

    result.update(_flatten(summary, statement, column))
    result["scope"] = (bs.scope if bs is not None else "") or ""
    result["shares_issued"] = summary.shares_issued
    result["treasury_shares"] = summary.treasury_shares
    result["statement_unit"] = statement.unit
    result["ai_count"] = count_ai_terms(text.pages)
    result["notes"].extend(summary.notes + statement.notes)
    if bs is None:
        result["notes"].append("サマリー頁の財政状態表を検出できず")
    if summary.tables.get("pl") is None:
        result["notes"].append("サマリー頁の経営成績表を検出できず")

    has_assets = result["summary"].get("total_assets") or result["statement"].get("total_assets")
    if has_assets:
        result["status"] = "ok"
    else:
        result.update(status="failed", fail_reason="テキストはあるが総資産を抽出できず（書式が想定外）")
    return result
