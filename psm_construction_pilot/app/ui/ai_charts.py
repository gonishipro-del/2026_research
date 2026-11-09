"""段階2の図（言及あり・なしの内訳、企業ごとのヒット件数）。

色は段階4と同じく、言及あり＝青、言及なし＝橙、判定不能＝灰。
"""
from __future__ import annotations

import altair as alt
import pandas as pd

from core import ai_count

GROUP_COLORS = {ai_count.TREATED: "#2a78d6", ai_count.CONTROL: "#eb6834", ai_count.UNDETERMINED: "#c3c2b7"}
GROUP_LABELS = {ai_count.TREATED: "言及あり（処置群）", ai_count.CONTROL: "言及なし（対照群）",
                ai_count.UNDETERMINED: "判定不能"}
ROW_HEIGHT = 22  # 1社あたりの行の高さ（px）。企業名が重ならない高さ


def group_bar(flags: pd.DataFrame) -> alt.LayerChart:
    """全社の内訳（言及あり・なし・判定不能）を1本の横棒で。"""
    counts = flags["group"].value_counts().reindex(list(GROUP_COLORS), fill_value=0)
    counts = counts[counts > 0]
    table = pd.DataFrame({"label": [GROUP_LABELS[g] for g in counts.index], "count": counts.to_numpy()})
    table["end"] = table["count"].cumsum()
    table["start"] = table["end"] - table["count"]
    table["mid"] = (table["start"] + table["end"]) / 2
    table["text"] = table["label"] + " " + table["count"].astype(str) + "社"
    domain = [GROUP_LABELS[g] for g in GROUP_COLORS]
    base = alt.Chart(table)
    bar = base.mark_bar(height=34, stroke="#ffffff", strokeWidth=2).encode(
        x=alt.X("start:Q", axis=None), x2="end:Q",
        color=alt.Color("label:N", scale=alt.Scale(domain=domain, range=list(GROUP_COLORS.values())),
                        legend=alt.Legend(title=None, orient="bottom")),
        tooltip=[alt.Tooltip("label:N", title="区分"), alt.Tooltip("count:Q", title="社数")],
    )
    text = base.mark_text(color="#ffffff", fontWeight="bold").encode(x="mid:Q", text="text:N")
    return (bar + text).properties(height=60)


def hit_bars(hits: pd.DataFrame, flags: pd.DataFrame, threshold: int) -> alt.LayerChart:
    """ヒットがあった会社のヒット数と、言及ありの基準線。"""
    totals = flags.assign(company=flags["code"] + " " + flags["name"])[["code", "company"]]
    totals = totals.assign(total=totals["code"].map(hits.groupby("code").size()).fillna(0).astype(int))
    totals = totals[totals["total"] > 0].sort_values(["total", "code"], ascending=[False, True])
    totals["text"] = totals["total"].astype(str) + "件"
    order = totals["company"].tolist()
    # 名前が重なると自動で間引かれる（1行おきにしか出ない）ので、間引きを止めて行の高さを確保する
    y = alt.Y("company:N", sort=order, title=None,
              axis=alt.Axis(labelLimit=260, labelOverlap=False, labelFontSize=12))
    base = alt.Chart(totals)
    bars = base.mark_bar(size=14, color=GROUP_COLORS[ai_count.TREATED]).encode(
        x=alt.X("total:Q", title="ヒット数（件）", axis=alt.Axis(tickMinStep=1)), y=y,
        tooltip=[alt.Tooltip("company:N", title="企業"), alt.Tooltip("total:Q", title="件数")],
    )
    labels = base.mark_text(align="left", dx=4, fontSize=11, color="#52514e").encode(x="total:Q", y=y, text="text:N")
    # 線は基準の0.5件手前に引く（基準ちょうどの件数の棒も、線を越えて見えるように）
    rule = alt.Chart(pd.DataFrame({"x": [threshold - 0.5],
                                   "text": [f"この線を越えた会社が言及あり（{threshold}件以上）"]}))
    line = rule.mark_rule(strokeDash=[4, 4], color="#52514e").encode(x="x:Q")
    note = rule.mark_text(align="left", dx=4, dy=-6, color="#52514e", fontSize=11).encode(
        x="x:Q", y=alt.value(0), text="text:N")
    return (bars + labels + line + note).properties(height=max(ROW_HEIGHT * len(order), 120))
