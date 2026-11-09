"""概要: 段階の状態、run の一覧、実行履歴。"""
from __future__ import annotations

import streamlit as st

import config
from core import inputs, runs, state
from ui import common

run_id = runs.current_run()
st.title("建設業 PSM 試運転")
st.markdown(
    "決算短信での AI 言及を処置とした傾向スコアマッチングとイベントスタディを、段階ごとに"
    "ボタンで進める。前の段階が完了するまで次の段階は実行できない。上流を再実行・編集すると、"
    "下流は「要再実行」になる。"
)

st.subheader("段階の状態")
st.dataframe(common.status_table(run_id), hide_index=True, width="stretch")

st.subheader("入力ファイル")
registered = inputs.registry()
for kind, label in inputs.KINDS.items():
    entry = registered.get(kind)
    if not entry:
        st.markdown(f"- {label}: 未登録（「入力ファイルの更新」で確認してください）")
        continue
    mark = "✅" if entry["validation"]["ok"] else "❌"
    st.markdown(f"- {mark} {label}: `{entry['file']}`（更新 {entry['modified_at']}、"
                f"SHA256 `{entry['sha256'][:16]}…`）")

st.subheader("run の一覧")
all_runs = state.load()["runs"]
st.dataframe(
    [{"run": key, "作成": value["created_at"], "親": value.get("parent") or "",
      "理由": value.get("reason", ""), "現在": "●" if key == run_id else ""}
     for key, value in sorted(all_runs.items(), reverse=True)],
    hide_index=True, width="stretch",
)

st.subheader("実行履歴")
if config.RUN_HISTORY_CSV.exists():
    history = common.read_csv(config.RUN_HISTORY_CSV)
    st.dataframe(history.iloc[::-1], hide_index=True, width="stretch", height=300)
