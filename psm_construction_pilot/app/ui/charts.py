"""欠損ヒートマップ（段階3・4で共通）。

色だけで区別しないよう、セルには状態の略字を必ず重ねる（凡例にも略字を出す）。
"""
from __future__ import annotations

import altair as alt
import pandas as pd

from core import covariates

# (表示カテゴリ, 色, 略字)。状態の種類は固定なので色も固定で割り当てる
CATEGORIES = [
    ("取得済み", "#e1e0d9", ""),
    ("代替値", "#2a78d6", "代"),
    ("手動補完", "#1baf7a", "手"),
    ("要確認", "#fab219", "確"),
    (f"欠損: {covariates.R_IMAGE}", "#eb6834", "画"),
    (f"欠損: {covariates.R_NO_ITEM}", "#e87ba4", "項"),
    (f"欠損: {covariates.R_PARSE}", "#e34948", "抽"),
    (f"欠損: {covariates.R_NO_PRIOR}", "#6250d6", "前"),
    (f"欠損: {covariates.R_PRICE}", "#008300", "株"),
]
_DARK_TEXT = {"取得済み", "要確認"}
VARIABLE_ORDER = ["ai_flag", *covariates.VARIABLES]


def category(status: str, reason: str) -> str:
    return f"欠損: {reason}" if status == covariates.MISSING else status


def with_categories(cells: pd.DataFrame) -> pd.DataFrame:
    table = cells.copy()
    table["category"] = [category(s, r) for s, r in zip(table["status"], table["reason_code"])]
    abbreviations = {name: abbr for name, _, abbr in CATEGORIES}
    table["abbr"] = table["category"].map(abbreviations).fillna("?")
    table["text_color"] = ["#0b0b0b" if c in _DARK_TEXT else "#ffffff" for c in table["category"]]
    table["company"] = table["code"] + " " + table["name"]
    return table


def _color_scale() -> alt.Scale:
    return alt.Scale(domain=[name for name, _, _ in CATEGORIES], range=[color for _, color, _ in CATEGORIES])


def legend_markdown() -> str:
    return "　".join(f"<span style='display:inline-block;width:12px;height:12px;background:{color};"
                     f"border-radius:2px;vertical-align:middle'></span> {abbr or '空欄'}＝{name}"
                     for name, color, abbr in CATEGORIES)


def heatmap(cells: pd.DataFrame, order: list[str]) -> alt.LayerChart:
    """企業（行）× 変数（列）。order は行（企業ラベル）の並び。"""
    table = with_categories(cells)
    tooltip = [alt.Tooltip("company:N", title="企業"), alt.Tooltip("variable:N", title="変数"),
               alt.Tooltip("value:N", title="値"), alt.Tooltip("status:N", title="状態"),
               alt.Tooltip("reason_code:N", title="理由コード"), alt.Tooltip("detail:N", title="詳細")]
    base = alt.Chart(table).encode(
        x=alt.X("variable:N", sort=VARIABLE_ORDER, title=None, axis=alt.Axis(orient="top", labelAngle=0)),
        y=alt.Y("company:N", sort=order, title=None, axis=alt.Axis(labelOverlap=False, labelFontSize=12)),
    )
    rect = base.mark_rect(stroke="#ffffff", strokeWidth=2, cornerRadius=2).encode(
        color=alt.Color("category:N", scale=_color_scale(), title="状態", legend=alt.Legend(orient="bottom", columns=3, labelLimit=320)),
        tooltip=tooltip,
    )
    text = base.mark_text(fontSize=11).encode(text="abbr:N", color=alt.Color("text_color:N", scale=None),
                                              tooltip=tooltip)
    return (rect + text).properties(height=max(22 * len(order), 120))

