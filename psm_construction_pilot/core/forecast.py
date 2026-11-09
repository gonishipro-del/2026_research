"""業績予想の読み取り（利益サプライズの「予想」側）と、予想の資料の選び方。

予想は、本決算の発表より前で最も新しい資料から取る:
  - 業績予想の修正の開示（「今回発表予想(B)」「今回修正予想(B)」の行）
  - 四半期短信のサマリー頁の「業績予想」の表（「通期」の行）
同じ日に両方あれば修正の開示を先に試す。読めなければ（画像PDF・文字化け・表が無い）次の資料を試す。
四半期短信が「修正の開示を参照」とだけ書いて表を載せていないことがあるため、両方を集めておく。

表の列の対応づけ（売上高・営業利益・経常利益・税引前利益…）は、既存の psm_dataset.summary_parser を使う。
「通期」「今回発表予想(B)」の行を、期の行（例: 2026年3月期 …）に置き換えて同じ解析にかける。
予想に幅がある（230,000～250,000）場合は中央値にする。「未定」「－」は値なし。
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from core.pdf_text import page_text
from psm_dataset import summary_parser
from psm_dataset.numbers import squash, to_number

QUARTERLY, REVISION = "四半期短信", "業績予想の修正"
_RANGE = re.compile(r"(△?[\d,]+(?:\.\d+)?)\s*[～~〜]\s*(△?[\d,]+(?:\.\d+)?)")
_PERIOD = re.compile(r"(\d{4})年(\d{1,2})月期")
_QUARTERLY_HEADING = re.compile(r"\d{4}年\d{1,2}月期の(連結|個別)?業績予想")
_REVISION_ROW = re.compile(r"^(今回(?:発表|修正)?予想|今回発表予想|修正後予想)")
_EXCLUDE_TITLE = ("訂正", "補足", "説明", "プレゼンテーション", "資料", "質疑", "概要")
SEARCH_PAGES = 3


# --- 資料の選び方（収集で使う） -----------------------------------------------------

def is_quarterly_report(category: str, title: str) -> bool:
    return category == "四半期" and "決算短信" in title and not any(word in title for word in _EXCLUDE_TITLE)


def is_forecast_revision(category: str, title: str) -> bool:
    """業績予想を修正・公表する開示。配当予想だけの修正は除く。"""
    return category == "業績" and "業績予想" in title and "訂正" not in title


def select_documents(disclosures: list, annual_date: date | None) -> list:
    """本決算の発表日より前の、予想の資料（新しい順。同じ日は修正の開示を先に）。

    直前の四半期短信と、それ以降に出た業績予想の修正をすべて返す（最大でも数件）。
    """
    if annual_date is None:
        return []
    before = [d for d in disclosures if d.disclosed_on and d.disclosed_on < annual_date]
    quarterly = [d for d in before if is_quarterly_report(d.category, d.title)]
    latest_quarter = max((d.disclosed_on for d in quarterly), default=None)
    chosen = [d for d in quarterly if d.disclosed_on == latest_quarter]
    chosen += [d for d in before if is_forecast_revision(d.category, d.title)
               and (latest_quarter is None or d.disclosed_on >= latest_quarter)]
    return sorted(chosen, key=lambda d: (d.disclosed_on, d.category == "業績"), reverse=True)


# --- 表の読み取り ------------------------------------------------------------------

def _midpoints(text: str) -> str:
    def middle(match: re.Match) -> str:
        values = [float(v.replace(",", "").replace("△", "-")) for v in match.groups()]
        mid = sum(values) / 2
        return f"{mid:.2f}" if any("." in v for v in match.groups()) else f"{mid:.0f}"
    return _RANGE.sub(middle, text)


def _numeric_tail(line: str) -> str:
    """行の先頭の見出し（「通 期」「今回発表予想 (B)」など）を外し、数値の並びだけを返す。"""
    tokens = line.split()
    while tokens and to_number(tokens[0]) is None and tokens[0] not in ("-", "－", "―", "未定"):
        tokens.pop(0)
    return " ".join(tokens)


def _period_of(text: str) -> str:
    matched = _PERIOD.search(squash(text))
    return f"{matched.group(1)}年{int(matched.group(2))}月期" if matched else ""


def _table_from(lines: list[str], unit_index: int, row_values: str, period: str) -> dict | None:
    """unit_index の単位行と、その上の見出しを使って、row_values（期の行の数値部分）を項目に対応づける。"""
    header_start = unit_index
    while header_start > 0 and unit_index - header_start < 6:
        previous = lines[header_start - 1]
        if (summary_parser._is_unit_line(previous) or _REVISION_ROW.match(squash(previous))
                or "業績予想" in previous or previous.startswith(("(注)", "(%", "（%", "※"))
                or re.match(r"^[\d０-９]+[.．]", previous)):
            break
        header_start -= 1
    pseudo = lines[header_start:unit_index + 1] + [f"{period or '2000年1月期'} {row_values}"]
    table = summary_parser._build_table(pseudo, unit_index - header_start)
    if table is None or table.kind != "pl":
        return None
    return {key: value for key, value in table.values.items() if value is not None}


def parse_quarterly(pages: list[list[str]]) -> dict | None:
    """四半期短信のサマリー頁の「業績予想」の表から、通期の予想を取る（連結を優先）。"""
    lines = [_midpoints(line) for page in pages[:SEARCH_PAGES] for line in page]
    found = []
    for index, line in enumerate(lines):
        heading = squash(line)
        if not _QUARTERLY_HEADING.search(heading):
            continue
        period = _period_of(heading)
        for position in range(index + 1, min(index + 14, len(lines) - 1)):
            if summary_parser._is_unit_line(lines[position]):
                for row_index in range(position + 1, min(position + 4, len(lines))):
                    row = lines[row_index]
                    if squash(row).startswith("通期"):
                        table = _table_from(lines, position, _numeric_tail(row), period)
                        if table:
                            found.append({"period": period, "scope": "個別" if "個別" in heading else "連結",
                                          "values": table, "row": row})
                break
    if not found:
        found = _full_year_rows_anywhere(lines)
    if not found:
        return None
    return next((item for item in found if item["scope"] == "連結"), found[0])


def _full_year_rows_anywhere(lines: list[str]) -> list[dict]:
    """見出しの下に表がない場合（PDFの文字の並び順で、表が見出しより前に出てくることがある）、
    単位行のすぐ下にある「通期」の行を探す。期は、業績予想の見出しから取る。"""
    period = next((_period_of(squash(line)) for line in lines if _QUARTERLY_HEADING.search(squash(line))), "")
    if not period:
        return []
    found = []
    for index, line in enumerate(lines):
        if not squash(line).startswith("通期"):
            continue
        unit_index = next((i for i in range(index - 1, max(index - 4, -1), -1)
                           if summary_parser._is_unit_line(lines[i])), None)
        if unit_index is None:
            continue
        table = _table_from(lines, unit_index, _numeric_tail(line), period)
        if table:
            found.append({"period": period, "scope": "連結", "values": table, "row": line})
    return found


def parse_revision(pages: list[list[str]]) -> dict | None:
    """業績予想の修正の開示から、通期の「今回発表予想(B)」を取る（連結を優先）。"""
    lines = [_midpoints(line) for page in pages[:SEARCH_PAGES] for line in page]
    found = []
    context = ""
    for index, line in enumerate(lines):
        compact = squash(line)
        if "業績予想" in compact or "期" in compact and ("通期" in compact or "累計" in compact):
            context = compact
        if not _REVISION_ROW.match(compact):
            continue
        if "累計" in context or "第2四半期" in context or "中間" in context:
            continue  # 通期ではない予想の修正
        unit_index = next((i for i in range(index - 1, max(index - 6, -1), -1)
                           if summary_parser._is_unit_line(lines[i])), None)
        if unit_index is None:
            continue
        table = _table_from(lines, unit_index, _numeric_tail(line), _period_of(context))
        if table:
            found.append({"period": _period_of(context), "scope": "個別" if "個別" in context else "連結",
                          "values": table, "row": line})
    if not found:
        found = _vertical_revision(lines)
    if not found:
        return None
    return next((item for item in found if item["scope"] == "連結"), found[0])


_ROW_LABEL = re.compile(r"^(前回|今回|増減|修正後|\(ご参考\)|（ご参考）|ご参考|\(参考\)|前期実績)")
_UNIT_WORDS = {"百万円", "千円", "億円", "円", "銭", "%", "％"}
_BLANK_WORDS = {"-", "－", "―", "‐", "未定"}


def _vertical_revision(lines: list[str]) -> list[dict]:
    """表の各セルが1行ずつ縦に並ぶ書き方の修正の開示を読む。

    見出し（売上高 営業利益 経常利益 …）の項目の順に、「今回発表予想(B)」の行から次の行見出しまでの
    数値を対応づける。「－」「未定」は空欄として位置を保つ。
    """
    found = []
    context = ""
    for index, line in enumerate(lines):
        compact = squash(line)
        if "業績予想" in compact and ("通期" in compact or "期" in compact):
            context = compact
        if not (compact.startswith("今回") and ("予想" in compact or "(B)" in compact)):
            continue
        if "累計" in context or "第2四半期" in context or "中間" in context:
            continue
        # 見出し：この行より上で、前回の行見出しより前にある項目名の並び
        first_row = next((i for i in range(index - 1, max(index - 25, -1), -1)
                          if squash(lines[i]).startswith("前回")), index)
        header_start = first_row
        while header_start > 0 and first_row - header_start < 10 and "業績予想" not in lines[header_start - 1]:
            header_start -= 1
        header = squash(" ".join(lines[header_start:first_row]))
        labels = summary_parser._labels_in(header, summary_parser._PL_LABELS)
        if not labels:
            continue
        cells: list[float | None] = []
        unit = ""
        rest = re.sub(r"^\S*?予想\s*\(?\s*B\s*\)?", "", line).split()
        position = index
        tokens = rest
        while True:
            for token in tokens:
                token = token.strip("()（）")
                if token in _UNIT_WORDS:
                    unit = unit or (token if token in ("百万円", "千円", "億円") else "")
                elif token in _BLANK_WORDS:
                    cells.append(None)
                elif to_number(token) is not None:
                    cells.append(to_number(token))
            position += 1
            if (position >= len(lines) or _ROW_LABEL.match(squash(lines[position]))
                    or lines[position].startswith(("(注", "（注", "2.", "２．"))):
                break
            tokens = lines[position].split()
        if not unit:
            unit = next((t for row in lines[first_row:index] for t in row.split() if t in ("百万円", "千円", "億円")), "百万円")
        factor = {"百万円": 1e6, "千円": 1e3, "億円": 1e8}[unit]
        values = {}
        for label, value in zip(labels, cells):
            if value is not None and label not in values:
                values[label] = value * factor
        if any(key in values for key in ("ordinary_profit", "pretax_profit", "operating_profit")):
            found.append({"period": _period_of(context), "scope": "個別" if "個別" in context else "連結",
                          "values": values, "row": " ".join(lines[index:position])})
    return found


def read_forecast(document: dict) -> tuple[dict | None, str]:
    """予想の資料1件を読む。(予想, 読めなかった理由)。"""
    path = Path(document["path"])
    if not path.exists():
        return None, "ファイルがない"
    text = page_text(path)
    if text.failure:
        return None, f"文字を取り出せない（{text.failure}）"
    parser = parse_revision if document["kind"] == REVISION else parse_quarterly
    result = parser(text.pages)
    if result is None:
        return None, "予想の表が見つからない（参照のみ・書式が想定外の可能性）"
    return {**result, "kind": document["kind"], "title": document.get("title", ""),
            "date": document.get("date", ""), "path": str(path)}, ""


def latest_forecast(documents: list[dict], period: str) -> tuple[dict | None, list[str]]:
    """新しい資料から順に読み、対象の期（例: 2026年3月期）の予想が読めた最初のものを返す。"""
    notes = []
    for document in documents:
        forecast, reason = read_forecast(document)
        if forecast is None:
            notes.append(f"{document.get('date', '')} {document['kind']}: {reason}")
            continue
        if period and forecast["period"] and forecast["period"] != period:
            notes.append(f"{document.get('date', '')} {document['kind']}: 期が違う（{forecast['period']}）")
            continue
        return forecast, notes
    return None, notes
