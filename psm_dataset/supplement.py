"""段階2（画像PDFの目視読み取り）の結果 supplement.csv の読み込み。

列: code, unit, total_assets, net_assets, owner_equity, liabilities, ordinary_profit,
pretax_profit, net_income_parent, shares_issued, treasury_shares, bps_disclosed,
equity_ratio, scope, ai_flag, ai_count, note
金額は unit 列（百万円/千円/円。空なら百万円）の単位で書く。読み取れなかった項目は空欄にする。
"""
from __future__ import annotations

import csv
from pathlib import Path

from .assemble import SUPPLEMENT_AMOUNT_FIELDS, SUPPLEMENT_COUNT_FIELDS, SUPPLEMENT_RATE_FIELDS

SUPPLEMENT_COLUMNS = (
    ["code", "unit"] + SUPPLEMENT_AMOUNT_FIELDS + SUPPLEMENT_COUNT_FIELDS
    + SUPPLEMENT_RATE_FIELDS + ["scope", "ai_flag", "ai_count", "note"]
)
_NUMERIC = set(SUPPLEMENT_AMOUNT_FIELDS + SUPPLEMENT_COUNT_FIELDS + SUPPLEMENT_RATE_FIELDS + ["ai_flag", "ai_count"])


def _to_float(raw: str) -> float | None:
    cleaned = raw.strip().replace(",", "").replace("△", "-").replace("▲", "-")
    if cleaned in ("", "-", "―"):
        return None
    return float(cleaned)


def load_supplement(path: Path) -> dict[str, dict]:
    """証券コード → 補完データ。ファイルがなければ空辞書。"""
    if not path.exists():
        return {}
    rows: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = (row.get("code") or "").strip().upper()
            if not code:
                continue
            parsed = {key: (_to_float(value or "") if key in _NUMERIC else (value or "").strip())
                      for key, value in row.items() if key}
            rows[code] = parsed
    return rows
