"""段階4: 傾向スコアの推定とマッチング。"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from core import pairing, propensity, runs, stages
from core.car import window_label
from ui import pairing as ui_pairing
from ui import stage_panel

NUMBER = stages.MATCHING
run_id = runs.current_run()
entry = stage_panel.header(run_id, NUMBER)
settings = runs.run_config(run_id)
st.markdown(f"ロジット `ai_flag ~ {' + '.join(settings['covariates'])}`（対象は1業種なので業種は入れない）。"
            "距離はロジットの線形予測子、キャリパーはその標準偏差 × 倍数。1対1の最近傍で、復元あり・なしと"
            f"キャリパー {settings['caliper_grid']} をすべて計算する。処理順・同点は seed（{settings['seed']}）で固定。")
stage_panel.run_controls(run_id, NUMBER)

out = runs.stage_dir(run_id, NUMBER)
if not (out / "ps_model.json").exists() or entry["status"] in (stages.NOT_RUN, stages.RUNNING):
    stage_panel.logs_and_errors(run_id, NUMBER)
    st.stop()

model = json.loads((out / "ps_model.json").read_text(encoding="utf-8"))
grid = pd.read_csv(out / "matching_grid.csv")
main = propensity.config_name(settings["replacement_main"], settings["caliper_sd"])
main_row = grid[grid["config"] == main].iloc[0]

columns = st.columns(5)
columns[0].metric("推定に使った企業", f"{model['n']}（処置{model['n_treated']}・対照{model['n_control']}）")
columns[1].metric("疑似R²", f"{model['pseudo_r2']:.3f}")
columns[2].metric("主分析のペア", int(main_row["pairs"]))
columns[3].metric("脱落した処置群", int(main_row["treated_dropped"]))
columns[4].metric("SMD最大（マッチ後）", f"{main_row['max_abs_smd_after']:.3f}")
if not model["converged"]:
    st.error("ロジットが収束していません")
for note in model["warnings"]:
    st.warning(note)

st.subheader("ロジットの係数")
coef = pd.DataFrame({"係数": model["coef"], "標準誤差": model["se"]})
coef["z"] = coef["係数"] / coef["標準誤差"]
st.dataframe(coef.style.format("{:.4f}"), width="stretch")
st.caption(f"線形予測子の標準偏差 {model['caliper_sd_of_lp']:.4f}。キャリパー（絶対値）= これ × 倍数。")

st.subheader("マッチングの設定ごとの結果")
st.caption(f"主分析は `{main}`（設定の「主分析の復元」と「キャリパー」）。段階5で凍結して確定する。")
st.dataframe(grid.rename(columns={"config": "設定", "replacement": "復元", "caliper_sd": "キャリパー（倍数）",
                                  "caliper_abs": "キャリパー（絶対値）", "pairs": "ペア", "treated_matched": "マッチした処置群",
                                  "treated_dropped": "脱落した処置群", "controls_used": "使った対照",
                                  "max_abs_smd_after": "SMD最大（マッチ後）", "main": "主分析"}),
             hide_index=True, width="stretch")

st.subheader("誰と誰がペアになったか")
chosen = st.selectbox("表示するマッチングの設定", grid["config"].tolist(), index=grid["config"].tolist().index(main))
data = pairing.load(run_id, chosen)
nodes, pairs, caliper_abs = data["nodes"], data["pairs"], data["caliper_abs"]
ui_pairing.summary_metrics(nodes, pairs)

tab_chart, tab_pairs, tab_roster = st.tabs(["図で見る", "ペアを比べる", "全企業の名簿"])
with tab_chart:
    st.markdown(
        "横軸は**傾向スコア**（その会社が AI に言及しやすい度合い。右ほど言及しやすい）。"
        "上段が言及あり（処置）、下段が言及なし（対照）で、**線でつながった2社が1組のペア**。"
        "線がほぼ垂直なほど、傾向スコアが近い2社を組めている（個々の項目が似ているとは限らない）。")
    columns = st.columns([3, 1])
    picked = columns[0].selectbox("ペアを強調表示", [ui_pairing.ALL_PAIRS] + pairs["label"].tolist(), key="chart_pair")
    zoom = columns[1].checkbox("中心部を拡大", value=True, help="離れた外れ値を枠の外に出して、ペアが密な範囲を拡大する")
    st.altair_chart(ui_pairing.dumbbell(nodes, pairs, caliper_abs, None if picked == ui_pairing.ALL_PAIRS else picked,
                                         zoom=zoom), width="stretch")
    if zoom:
        hidden = ui_pairing.hidden_by_zoom(nodes, pairs)
        if hidden:
            st.caption(f"拡大表示のため枠の外にある{len(hidden)}社: {'、'.join(hidden)}（「中心部を拡大」を外すと表示される）")
    if picked != ui_pairing.ALL_PAIRS:
        st.caption(f"薄い青の帯は許容幅（キャリパー）。処置の会社の傾向スコアから ±{caliper_abs:.3f} の範囲にいる対照だけが、相手の候補になる。")
    st.caption("● 言及あり・ペアになった　✖ 言及あり・脱落（許容幅の中に相手がいない、または近くの対照が他のペアですでに使われた）　"
               "■ 言及なし・ペアになった　□ 言及なし・使われなかった。点にカーソルを合わせると企業名と相手が出る。")
with tab_pairs:
    label = st.selectbox("ペア", pairs["label"].tolist(), key="card_pair")
    pair = pairs[pairs["label"] == label].iloc[0]
    ui_pairing.pair_card(run_id, settings, pair, nodes, window_label(tuple(settings["event_window_main"])))
    with st.expander("全ペアの一覧"):
        st.dataframe(pairs[["treated", "treated_name", "control", "control_name", "ps_treated", "ps_control", "distance"]]
                     .rename(columns={"treated": "処置コード", "treated_name": "言及あり（処置）", "control": "対照コード",
                                      "control_name": "言及なし（対照）", "ps_treated": "処置の傾向スコア",
                                      "ps_control": "対照の傾向スコア", "distance": "距離（ロジット）"}),
                     hide_index=True, width="stretch")
with tab_roster:
    st.caption("全企業が、ペアになったか・脱落したか・使われなかったかを区分の順に並べている。")
    listing = ui_pairing.roster(nodes)
    st.dataframe(listing, hide_index=True, width="stretch", column_config={
        "傾向スコア（%）": st.column_config.ProgressColumn("傾向スコア（%）", min_value=0, max_value=100, format="%.1f"),
        "AI言及（件）": st.column_config.NumberColumn(format="%d"),
        "相手になった回数": st.column_config.NumberColumn(format="%d"),
    })

stage_panel.logs_and_errors(run_id, NUMBER)
stage_panel.file_list("出力ファイル", [
    (out / "ps_model.json", "係数・標準誤差・疑似R²・警告"),
    (out / "propensity.csv", "企業ごとの傾向スコア"),
    (out / "matched_pairs.csv", "主分析のペア"),
    (out / "dropped_treated.csv", "主分析で脱落した処置群"),
    (out / "matching_grid.csv", "設定ごとのペア数・脱落数・SMD"),
])
