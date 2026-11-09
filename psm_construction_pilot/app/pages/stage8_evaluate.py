"""段階8: 評価（頑健性・感度）。"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from core import runs, stages
from ui import stage_panel

NUMBER = stages.EVALUATE
run_id = runs.current_run()
entry = stage_panel.header(run_id, NUMBER)
st.markdown("窓・マッチングの設定（復元・キャリパー）・プラセボ・期待収益率モデルを変えた ATT を、主分析と同じ軸に並べる。")
stage_panel.run_controls(run_id, NUMBER)

out = runs.stage_dir(run_id, NUMBER)
if not (out / "sensitivity.csv").exists() or entry["status"] in (stages.NOT_RUN, stages.RUNNING):
    stage_panel.logs_and_errors(run_id, NUMBER)
    st.stop()

runs.mark_results_viewed(run_id, NUMBER)
st.subheader("フォレストプロット")
st.image(str(out / "forest.png"))
sens = pd.read_csv(out / "sensitivity.csv")
kinds = {"window": "窓", "match": "マッチ設定", "placebo": "プラセボ", "market": "期待収益率モデル"}
st.dataframe(sens.assign(種類=sens["kind"].map(kinds)).drop(columns="kind").rename(
    columns={"label": "推定", "att": "ATT", "ci_low": "95%CI下限", "ci_high": "95%CI上限", "n": "ペア"}).style.format(
    {"ATT": "{:+.4f}", "95%CI下限": "{:+.4f}", "95%CI上限": "{:+.4f}"}), hide_index=True, width="stretch")

st.subheader("マッチングの設定ごと（主分析の窓）")
grid = pd.read_csv(out / "grid.csv")
st.dataframe(grid[["config", "main", "n", "att", "ci_low", "ci_high", "max_abs_smd_after"]].rename(
    columns={"config": "設定", "main": "主分析", "n": "ペア", "att": "ATT", "ci_low": "95%CI下限",
             "ci_high": "95%CI上限", "max_abs_smd_after": "SMD最大（マッチ後）"}).style.format(
    {"ATT": "{:+.4f}", "95%CI下限": "{:+.4f}", "95%CI上限": "{:+.4f}", "SMD最大（マッチ後）": "{:.3f}"}),
    hide_index=True, width="stretch")

left, right = st.columns(2)
with left:
    st.subheader("Rosenbaum bounds")
    rosen = pd.read_csv(out / "rosenbaum.csv") if (out / "rosenbaum.csv").stat().st_size > 5 else pd.DataFrame()
    if rosen.empty:
        st.caption("主分析が復元ありのため計算していない（復元なし・1対1のペアが対象）")
    else:
        st.dataframe(rosen.rename(columns={"gamma": "Γ", "p_upper": "p値の上限", "p_lower": "p値の下限"}).style.format(
            {"Γ": "{:.2f}", "p値の上限": "{:.4f}", "p値の下限": "{:.4f}"}), hide_index=True, width="stretch")
        st.caption("仮定: 観測されない交絡により、ペア内で処置を受けるオッズが最大 Γ 倍異なりうる。"
                   "Wilcoxon 符号順位統計量の正規近似で、ATT の符号の方向に片側検定。Γ=1 は隠れたバイアスなし。")
with right:
    st.subheader("期待収益率モデルの比較")
    market = json.loads((out / "market_model.json").read_text(encoding="utf-8"))
    stats = market["stats"]
    st.metric(f"市場モデル（{market['benchmark']}）の ATT", f"{stats.get('att', float('nan')):+.4f}",
              help="FF5 の主分析と比べる")
    if stats.get("ci95"):
        st.caption(f"95%CI [{stats['ci95'][0]:+.4f}, {stats['ci95'][1]:+.4f}]、ペア {stats['n']}")
    st.caption("ファクターが米ドル建てであることの影響を確認するための頑健性検証。TOPIX（J-Quants）は APIキーが"
               "環境変数に無いため使えず、日経平均（^N225）で代替している。")

stage_panel.logs_and_errors(run_id, NUMBER)
stage_panel.file_list("出力ファイル", [
    (out / "sensitivity.csv", "代替推定の一覧"), (out / "grid.csv", "マッチ設定ごとの ATT・SMD"),
    (out / "rosenbaum.csv", "Rosenbaum bounds"), (out / "market_model.json", "市場モデルの ATT"),
    (out / "market_car.csv", "市場モデルの CAR"), (out / "forest.png", "フォレストプロット"),
])
