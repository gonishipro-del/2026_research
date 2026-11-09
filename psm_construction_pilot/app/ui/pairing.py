"""マッチングの見える化（ペアの図、ペアの比較カード、名簿）。

色だけで区別しないよう、区分ごとに形も変える（●処置・マッチ ✖処置・脱落 ■対照・ペア ◻対照・未使用）。
"""
from __future__ import annotations

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from core import pairing
from core.pairing import CONTROL_MATCHED, CONTROL_UNUSED, ROLES, TREATED_DROPPED, TREATED_MATCHED

BLUE, ORANGE, GREY, INK = "#2a78d6", "#eb6834", "#c3c2b7", "#52514e"
ROLE_COLOR = {TREATED_MATCHED: BLUE, TREATED_DROPPED: BLUE, CONTROL_MATCHED: ORANGE, CONTROL_UNUSED: GREY}
ROLE_SHAPE = {TREATED_MATCHED: "circle", TREATED_DROPPED: "cross", CONTROL_MATCHED: "square", CONTROL_UNUSED: "square"}
ROLE_SYMBOL = {TREATED_MATCHED: "●", TREATED_DROPPED: "✖", CONTROL_MATCHED: "■", CONTROL_UNUSED: "□"}
ALL_PAIRS = "（すべて表示）"


def summary_metrics(nodes: pd.DataFrame, pairs: pd.DataFrame) -> None:
    counts = nodes["role"].value_counts()
    treated, control = int((nodes["ai_flag"] == 1).sum()), int((nodes["ai_flag"] == 0).sum())
    columns = st.columns(4)
    columns[0].metric("言及あり（処置）", f"{treated}社")
    columns[1].metric("→ ペアになった", f"{counts.get(TREATED_MATCHED, 0)}社",
                      help="キャリパー内に似た対照の会社が見つかった処置群")
    columns[2].metric("→ 脱落（相手なし）", f"{counts.get(TREATED_DROPPED, 0)}社",
                      help="似た対照の会社が許容幅の中にいなかったため、比較から外れた")
    columns[3].metric("言及なし（対照）", f"{control}社（うち{counts.get(CONTROL_MATCHED, 0)}社がペアになった）")


def dumbbell(nodes: pd.DataFrame, pairs: pd.DataFrame, caliper_abs: float, selected: str | None,
             zoom: bool = False) -> alt.LayerChart:
    """傾向スコア（ロジット）の軸上に、処置（上段）と対照（下段）を置き、ペアを線でつなぐ。"""
    nodes = nodes.reset_index().rename(columns={"index": "code"})
    chosen = pairs[pairs["label"] == selected].iloc[0] if selected else None
    if chosen is None:
        nodes["opacity"], line_opacity, line_width = 1.0, 0.55, 1.2
    else:
        keep = {chosen["treated"], chosen["control"]}
        nodes["opacity"] = np.where(nodes["code"].isin(keep), 1.0, 0.18)
        line_opacity, line_width = 0.12, 1.0

    nodes["hit_label"] = nodes["ai_count"].map(lambda v: "—" if pd.isna(v) else f"{v:.0f}件")
    domain = ROLES
    color = alt.Color("role:N", scale=alt.Scale(domain=domain, range=[ROLE_COLOR[r] for r in domain]),
                      legend=alt.Legend(title="区分", orient="bottom", columns=2, labelLimit=300, symbolSize=120))
    shape = alt.Shape("role:N", scale=alt.Scale(domain=domain, range=[ROLE_SHAPE[r] for r in domain]))
    tooltip = [alt.Tooltip("name:N", title="企業"), alt.Tooltip("code:N", title="コード"),
               alt.Tooltip("role:N", title="区分"), alt.Tooltip("hit_label:N", title="AI言及"),
               alt.Tooltip("ps_pct:Q", title="傾向スコア(%)", format=".1f"), alt.Tooltip("partner:N", title="ペアの相手")]
    y_axis = alt.Axis(values=[0, 1], title=None, grid=False, ticks=False, domain=False, labelFontSize=13,
                      labelExpr="datum.value == 1 ? '言及あり（処置）' : '言及なし（対照）'")
    y_scale = alt.Scale(domain=[-0.45, 1.45])
    if zoom:  # ペアになった企業の範囲に絞って拡大する（外れ値は枠の外に出る）
        paired = pd.concat([pairs["lp_treated"], pairs["lp_control"]])
        margin = (paired.max() - paired.min()) * 0.08
        x_scale = alt.Scale(domain=[float(paired.min() - margin), float(paired.max() + margin)])
    else:
        x_scale = alt.Scale(nice=True)
    x_axis = alt.X("lp:Q", title="傾向スコア（ロジット）→ 右ほど「言及しやすい会社」", scale=x_scale)

    layers = []
    if chosen is not None:
        band = pd.DataFrame({"x0": [chosen["lp_treated"] - caliper_abs], "x1": [chosen["lp_treated"] + caliper_abs],
                             "y0": [-0.4], "y1": [1.4]})
        layers.append(alt.Chart(band).mark_rect(color=BLUE, opacity=0.10, clip=True).encode(
            x="x0:Q", x2="x1:Q", y=alt.Y("y0:Q", scale=y_scale), y2="y1:Q"))
    layers.append(alt.Chart(pairs).mark_rule(color=INK, opacity=line_opacity, strokeWidth=line_width, clip=True).encode(
        x=alt.X("lp_treated:Q", scale=x_scale), x2="lp_control:Q",
        y=alt.Y("y_treated:Q", scale=y_scale), y2="y_control:Q"))
    if chosen is not None:
        layers.append(alt.Chart(pd.DataFrame([chosen])).mark_rule(color=BLUE, strokeWidth=3, clip=True).encode(
            x="lp_treated:Q", x2="lp_control:Q", y=alt.Y("y_treated:Q", scale=y_scale), y2="y_control:Q"))
    layers.append(alt.Chart(nodes).mark_point(filled=True, size=130, strokeWidth=2, clip=True).encode(
        x=x_axis, y=alt.Y("y:Q", scale=y_scale, axis=y_axis), color=color, shape=shape,
        opacity=alt.Opacity("opacity:Q", legend=None, scale=None), tooltip=tooltip))
    if chosen is not None:
        for code, dy in ((chosen["treated"], -18), (chosen["control"], 20)):
            layers.append(alt.Chart(nodes[nodes["code"] == code]).mark_text(
                fontSize=13, fontWeight="bold", color=INK, dy=dy).encode(
                x="lp:Q", y=alt.Y("y:Q", scale=y_scale), text="name:N"))
    return alt.layer(*layers).properties(height=260)


def roster(nodes: pd.DataFrame) -> pd.DataFrame:
    """全企業の名簿（区分の順に並べる）。"""
    table = nodes.reset_index().rename(columns={"index": "code"})
    table["order"] = table["role"].map({role: i for i, role in enumerate(ROLES)})
    table = table.sort_values(["order", "ps"], ascending=[True, False])
    return pd.DataFrame({
        "区分": table["role"].map(lambda r: f"{ROLE_SYMBOL[r]} {r}"),
        "コード": table["code"], "企業": table["name"],
        "AI言及（件）": table["ai_count"],
        "傾向スコア（%）": table["ps_pct"],
        "ペアの相手": table["partner"],
        "相手になった回数": table["times_used"].where(table["ai_flag"] == 0, np.nan),
    })


def pair_card(run_id: str, settings: dict, pair: pd.Series, nodes: pd.DataFrame, main_window: str) -> None:
    """選んだペアを、2社を並べて比べる。"""
    left, right = st.columns(2)
    for column, side, role, title in ((left, "treated", pair["treated"], "言及あり（処置）"),
                                      (right, "control", pair["control"], "言及なし（対照）")):
        node = nodes.loc[role]
        column.markdown(f"**{title}**")
        column.markdown(f"### {node['name']}")
        column.caption(f"コード {role}　傾向スコア {node['ps_pct']:.1f}%")
    st.dataframe(pairing.comparison(run_id, settings, pair["treated"], pair["control"], nodes),
                 hide_index=True, width="stretch")
    gap = abs(pair["lp_treated"] - pair["lp_control"])
    st.caption(f"傾向スコア（ロジット）の差は {gap:.3f}（許容幅以内）。2社は、上の項目（言及以外）をまとめた"
               "傾向スコアが近い会社として組まれている。項目ごとには差が残ることがあり、全体のバランスは段階5で確認する。")
    car = pairing.cars(run_id, pair["treated"], pair["control"], f"car_{main_window}")
    if car is not None:
        st.markdown(f"**発表前後の株価反応（CAR {settings['event_window_main']}）**　"
                    f"処置 {car[0]:+.1%}　対照 {car[1]:+.1%}　**差 {car[0] - car[1]:+.1%}**")


def hidden_by_zoom(nodes: pd.DataFrame, pairs: pd.DataFrame) -> list[str]:
    """拡大表示（zoom=True）で枠の外に出る企業の名前。"""
    paired = pd.concat([pairs["lp_treated"], pairs["lp_control"]])
    margin = (paired.max() - paired.min()) * 0.08
    outside = nodes[(nodes["lp"] < paired.min() - margin) | (nodes["lp"] > paired.max() + margin)]
    return [f"{name}（{'言及あり' if flag == 1 else '言及なし'}）" for name, flag in zip(outside["name"], outside["ai_flag"])]
