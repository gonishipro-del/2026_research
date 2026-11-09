"""段階5: バランス診断とマッチングの確定（凍結）。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import balance, runs, stages
from ui import stage_panel

NUMBER = stages.FREEZE
run_id = runs.current_run()
entry = stage_panel.header(run_id, NUMBER)
st.markdown("標準化平均差（SMD）をマッチング前後で比べる（目安 |SMD| < 0.1）。分母はマッチング前のプールした標準偏差。"
            "診断を確認したら「マッチングを確定」で凍結する。**凍結するまで段階6は実行できない。**")
stage_panel.run_controls(run_id, NUMBER)

out = runs.stage_dir(run_id, NUMBER)
if not (out / "balance.csv").exists() or entry["status"] in (stages.NOT_RUN, stages.RUNNING):
    stage_panel.logs_and_errors(run_id, NUMBER)
    st.stop()

table = pd.read_csv(out / "balance.csv")
over = table[table["smd_after"].abs() > balance.SMD_TARGET]
if len(over):
    st.warning("マッチ後の |SMD| が 0.1 を超える変数: " + "、".join(
        f"{row.covariate}（{row.smd_after:+.3f}）" for row in over.itertuples()))

left, right = st.columns([3, 2])
with left:
    st.subheader("Love plot")
    if (out / "love.png").exists():
        st.image(str(out / "love.png"))
with right:
    st.subheader("SMD")
    st.dataframe(table[["covariate", "smd_before", "smd_after"]].rename(
        columns={"covariate": "共変量", "smd_before": "マッチ前", "smd_after": "マッチ後"}).style.format(
        {"マッチ前": "{:+.3f}", "マッチ後": "{:+.3f}"}), hide_index=True, width="stretch")
    st.subheader("傾向スコアの分布（共通サポート）")
    overlap = pd.read_csv(out / "overlap.csv", index_col=0)
    overlap.index = ["最小", "25%", "中央値", "75%", "最大"]
    st.dataframe(overlap.style.format("{:.3f}"), width="stretch")

st.subheader("設定ごとの SMD の最大値")
st.dataframe(pd.read_csv(out / "balance_grid.csv").rename(
    columns={"config": "設定", "pairs": "ペア", "max_abs_smd_after": "SMD最大（マッチ後）"}), hide_index=True, width="stretch")

dropped = pd.read_csv(runs.stage_dir(run_id, stages.MATCHING) / "dropped_treated.csv", dtype=str)
st.caption(f"主分析で脱落した処置群: {len(dropped)}社（詳細は段階4）")

st.subheader("マッチングの確定（凍結）")
frozen = runs.run_info(run_id).get("frozen")
if frozen:
    st.success(f"凍結済み: `{frozen['config']}`（復元{'あり' if frozen['replacement'] else 'なし'}、"
               f"キャリパー {frozen['caliper_sd']}）　{frozen['at']}　"
               f"マッチ結果 SHA256 `{frozen['matched_pairs_sha256'][:16]}…`")
    st.caption("上流（設定・入力・手動入力）を変えるか段階4を再実行すると、凍結は解除される。")
else:
    settings = runs.run_config(run_id)
    st.info(f"主分析の設定: 復元{'あり' if settings['replacement_main'] else 'なし'}、キャリパー {settings['caliper_sd']}"
            "（変えるときは段階0の設定で）")
    if st.button("マッチングを確定", type="primary", disabled=entry["status"] != stages.DONE):
        balance.freeze(run_id)
        st.rerun()

stage_panel.logs_and_errors(run_id, NUMBER)
stage_panel.file_list("出力ファイル", [
    (out / "balance.csv", "共変量ごとの SMD"), (out / "balance_grid.csv", "設定ごとの SMD の最大値"),
    (out / "overlap.csv", "傾向スコアの分布"), (out / "love.png", "Love plot"), (out / "frozen.json", "凍結の記録"),
])
