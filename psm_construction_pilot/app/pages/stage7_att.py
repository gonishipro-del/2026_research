"""段階7: ATT の推定。"""
from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st

from core import runs, stages
from core.car import window_label, window_title
from ui import common, stage_panel

NUMBER = stages.ATT
run_id = runs.current_run()
entry = stage_panel.header(run_id, NUMBER)
settings = runs.run_config(run_id)
st.markdown("ペアごとの CAR 差（処置 − 対照）の平均を ATT とする（復元ありは処置企業ごとに対照の平均）。"
            "検定は対応のある t 検定、Wilcoxon 符号順位検定、処置企業単位のブートストラップ（seed 固定）。")
st.caption("最近傍マッチングでのブートストラップは標準誤差の近似であり、厳密な推定ではない。")
stage_panel.run_controls(run_id, NUMBER)

out = runs.stage_dir(run_id, NUMBER)
if not (out / "att_summary.json").exists() or entry["status"] in (stages.NOT_RUN, stages.RUNNING):
    stage_panel.logs_and_errors(run_id, NUMBER)
    st.stop()

runs.mark_results_viewed(run_id, NUMBER)
summary = json.loads((out / "att_summary.json").read_text(encoding="utf-8"))
main = window_label(tuple(settings["event_window_main"]))
headline = summary["windows"][main]
st.subheader(f"主分析：窓 {window_title(tuple(settings['event_window_main']))}、マッチ `{summary['config']}`")
columns = st.columns(4)
columns[0].metric("ATT", f"{headline['att']:+.4f}")
columns[1].metric("95%信頼区間（ブートストラップ）", f"[{headline['ci95'][0]:+.4f}, {headline['ci95'][1]:+.4f}]")
columns[2].metric("p値（t / Wilcoxon）", f"{headline['p_t']:.3f} / {headline['p_wilcoxon']:.3f}")
columns[3].metric("ペア", headline["n"])
st.warning(f"サンプルが小さい（{headline['n']}組）。信頼区間の幅は {headline['ci95'][1] - headline['ci95'][0]:.4f}。"
           "試運転の結果であり、最終結果ではない。")

def title_of(name: str, values: dict) -> str:
    window = tuple(values.get("window") or (0, 0))
    return ("プラセボ " if name == "placebo" else "") + window_title(window)


rows = []
for name, values in summary["windows"].items():
    rows.append({"窓": title_of(name, values),
                 "ATT": values.get("att"), "標準誤差（t）": values.get("se_t"),
                 "標準誤差（ブートストラップ）": values.get("se_bootstrap"),
                 "95%CI下限": (values.get("ci95") or [None, None])[0], "95%CI上限": (values.get("ci95") or [None, None])[1],
                 "p（t）": values.get("p_t"), "p（Wilcoxon）": values.get("p_wilcoxon"), "ペア": values.get("n")})
st.subheader("窓ごとの ATT")
event_rows = pd.DataFrame([{"窓": title_of(name, values), "日数": values["window"][1] - values["window"][0] + 1,
                            "ATT": values.get("att"), "下限": (values.get("ci95") or [None, None])[0],
                            "上限": (values.get("ci95") or [None, None])[1], "ペア": values.get("n")}
                           for name, values in summary["windows"].items() if name != "placebo" and values.get("n")])
order = event_rows.sort_values("日数")["窓"].tolist()
base = alt.Chart(event_rows).encode(x=alt.X("窓:N", sort=order, title=None, axis=alt.Axis(labelAngle=0, labelFontSize=12)))
ci = base.mark_rule(strokeWidth=2, color="#2a78d6").encode(y=alt.Y("下限:Q", title="ATT（CAR差の平均）"), y2="上限:Q")
point = base.mark_point(size=110, filled=True, color="#2a78d6").encode(
    y="ATT:Q", tooltip=["窓", alt.Tooltip("ATT:Q", format="+.4f"), alt.Tooltip("下限:Q", format="+.4f", title="95%CI下限"),
                        alt.Tooltip("上限:Q", format="+.4f", title="95%CI上限"), "ペア"])
zero_line = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(strokeDash=[4, 4], color="#898781").encode(y="y:Q")
st.altair_chart((zero_line + ci + point).properties(height=280), width="stretch")
st.caption("点が ATT、縦線が95%信頼区間（ブートストラップ）。縦線が0（点線）をまたいでいれば、その窓では差がはっきりしない。"
           "長い窓は、ファクターの最終日を過ぎる会社のペアが計算から外れるため、ペア数が少ないことがある。")
st.dataframe(pd.DataFrame(rows).style.format({c: "{:+.4f}" for c in ["ATT", "95%CI下限", "95%CI上限"]}
                                             | {c: "{:.4f}" for c in ["標準誤差（t）", "標準誤差（ブートストラップ）"]}
                                             | {c: "{:.3f}" for c in ["p（t）", "p（Wilcoxon）"]}),
             hide_index=True, width="stretch")

st.subheader("ペアごとの CAR 差")
options = list(summary["windows"])
window = st.selectbox("窓", options, index=options.index(main),
                      format_func=lambda name: title_of(name, summary["windows"][name]))
pairs = pd.read_csv(out / f"pairs_{window}.csv", dtype={"treated": str})
names = common.read_csv(runs.stage_dir(run_id, stages.INGEST) / "targets.csv").set_index("code")["name"]
match_pairs = pd.read_csv(runs.stage_dir(run_id, stages.MATCHING) / "matches" / f"{summary['config']}.csv", dtype=str)
controls = match_pairs.groupby("treated")["control"].apply(lambda codes: "、".join(codes.map(names)))
pairs["企業"] = pairs["treated"].map(names) + " ↔ " + pairs["treated"].map(controls)
industry_of = common.read_csv(runs.stage_dir(run_id, stages.INGEST) / "targets.csv").set_index("code")["industry33"]
pair_industries = sorted(pairs["treated"].map(industry_of).dropna().unique())
if len(pair_industries) > 1:
    picked_industry = st.selectbox("業種で絞る", ["すべて"] + pair_industries, key="att_industry")
    if picked_industry != "すべて":
        pairs = pairs[pairs["treated"].map(industry_of) == picked_industry]
pairs["向き"] = pairs["diff"].map(lambda v: "処置のほうが高い" if v > 0 else "対照のほうが高い")
st.caption("1本の棒が1組。左が言及あり（処置）、右が言及なし（対照）の会社。棒が右 = 処置の会社のほうが株価反応が良かった。")
chart = alt.Chart(pairs).mark_bar(size=12, cornerRadiusEnd=4).encode(
    x=alt.X("diff:Q", title="CAR差（処置 − 対照）"),
    y=alt.Y("企業:N", sort="-x", title=None, axis=alt.Axis(labelLimit=420, labelOverlap=False, labelFontSize=12)),
    color=alt.Color("向き:N", scale=alt.Scale(domain=["処置のほうが高い", "対照のほうが高い"], range=["#2a78d6", "#eb6834"]),
                    legend=alt.Legend(title=None, orient="bottom")),
    tooltip=["企業", alt.Tooltip("car_t:Q", format="+.4f", title="処置のCAR"),
             alt.Tooltip("car_c:Q", format="+.4f", title="対照のCAR"), alt.Tooltip("diff:Q", format="+.4f", title="差")],
)
zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color="#898781").encode(x="x:Q")
st.altair_chart((zero + chart).properties(height=max(24 * len(pairs), 160)), width="stretch")
with st.expander("表で見る"):
    st.dataframe(pairs[["企業", "car_t", "car_c", "n_controls", "diff"]], hide_index=True, width="stretch")

stage_panel.logs_and_errors(run_id, NUMBER)
stage_panel.file_list("出力ファイル", [(out / "att_summary.json", "窓ごとの ATT・検定")]
                      + [(out / f"pairs_{name}.csv", f"窓 {title_of(name, summary['windows'][name])} のペアごとの CAR 差")
                         for name in options])
