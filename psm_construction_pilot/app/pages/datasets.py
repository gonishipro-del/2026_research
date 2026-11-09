"""データセット：決算短信と予想の資料の収集（決算プロからのダウンロード）と、分析に使うデータセットの選択。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import datasets, inputs, jobs, progress, runs, settings, stages
from ui import chain_panel

st.title("データセット")
st.markdown(
    "決算プロ（ke.kabupro.jp）から、各社の**本決算短信**と、利益サプライズに使う**予想の資料**（本決算の直前の四半期短信と、"
    "それ以降の業績予想の修正）をダウンロードする。1回の収集ごとに `data/datasets/{日時}_{名前}/` を作って保存し、"
    "以前のデータセットは上書きしない。収集はバックグラウンドで進み、画面を閉じても続く。途中で止まった場合は、"
    "未処理の企業から再開できる。")
st.caption("アクセス間隔・同時接続数は、既存の収集プログラム（collect.py）と同じ設定。企業ページには過去1年分の開示が載っている。")

for message in st.session_state.pop("datasets_flash", []):
    st.success(message)

jobs.reap_dead_collections()
run_id = runs.current_run()
current = runs.run_config(run_id)
st.info(f"今の run（`{run_id}`）が使っているデータセット: **{current['dataset']}**")

entries = datasets.all_datasets()
st.subheader("データセットの一覧")
st.dataframe(pd.DataFrame([{
    "ID": entry["id"], "名前": entry["label"], "作成": entry["created_at"], "状態": entry["status"],
    "予想の資料": "あり" if entry.get("has_forecast") else "なし", "結果": entry.get("message", entry.get("note", "")),
    "使用中": "●" if entry["id"] == current["dataset"] else "",
} for entry in entries]), hide_index=True, width="stretch")

st.subheader("新しく収集する")
with st.form("create_dataset"):
    label = st.text_input("名前（フォルダ名に付く）", placeholder="例: prime_all")
    columns = st.columns(2)
    market = columns[0].text_input("市場・商品区分", current["market"])
    excluded = columns[1].multiselect("除外する業種", datasets.FINANCIAL_INDUSTRIES + ["（なし）"],
                                      default=datasets.FINANCIAL_INDUSTRIES)
    limit = st.number_input("試しに先頭の何社だけ集める（0 なら全社）", 0, 5000, 0,
                            help="動作確認用。証券コード順の先頭から指定した社数だけ集める")
    st.caption("上場銘柄一覧は、入力フォルダの最新版を写して使う（データセットのフォルダに master.xlsx として残る）。")
    if st.form_submit_button("作成して収集を始める", type="primary"):
        if not label.strip():
            st.error("名前を入れてください")
        else:
            try:
                dataset_id = datasets.create(label, market=market.strip(),
                                             excluded_industries=[name for name in excluded if name != "（なし）"],
                                             master_path=inputs.current_file("master"), limit=int(limit) or None)
                jobs.launch_collection(dataset_id)
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"始められません: {error}")
            else:
                st.session_state["datasets_flash"] = [f"データセット `{dataset_id}` の収集を始めました。"]
                st.session_state["dataset_view"] = dataset_id
                st.rerun()

st.subheader("データセットの詳細")
ids = [entry["id"] for entry in entries]
default = st.session_state.get("dataset_view", current["dataset"])
chosen = st.selectbox("データセット", ids, index=ids.index(default) if default in ids else 0)
entry = datasets.get(chosen)
folder = datasets.path(chosen)
st.markdown(f"状態: **{entry['status']}**　{entry.get('message', entry.get('note', ''))}")
st.caption(f"フォルダ: {folder}　対象: {entry['scope'].get('market')}、除外業種: "
           f"{'・'.join(entry['scope'].get('excluded_industries') or []) or 'なし'}"
           + (f"、先頭{entry['scope']['limit']}社のみ" if entry["scope"].get("limit") else ""))


@st.fragment(run_every=3 if entry["status"] == datasets.COLLECTING else None)
def show_progress() -> None:
    jobs.reap_dead_collections()
    latest = datasets.get(chosen)
    status = progress.read_progress_in(folder) if not latest.get("legacy") else None
    if status and status.get("total"):
        st.progress(min(status["done"] / status["total"], 1.0),
                    text=f"{status['done']}/{status['total']} {status['message']}")
    if entry["status"] == datasets.COLLECTING and latest["status"] != datasets.COLLECTING:
        st.rerun(scope="app")


if not entry.get("legacy"):
    show_progress()
    columns = st.columns(3)
    if entry["status"] in (datasets.INTERRUPTED, datasets.FAILED, datasets.NOT_COLLECTED):
        if columns[0].button("収集を再開する", type="primary"):
            try:
                jobs.launch_collection(chosen)
            except RuntimeError as error:
                st.error(str(error))
            else:
                st.rerun()
    if entry.get("counts"):
        counts = entry["counts"]
        metrics = st.columns(5)
        metrics[0].metric("対象", counts["companies"])
        metrics[1].metric("本決算短信", counts["annual_ok"])
        metrics[2].metric("予想の資料あり", counts["with_forecast_documents"])
        metrics[3].metric("短信・ページなし", counts["tanshin_not_found"] + counts["page_not_found"])
        metrics[4].metric("エラー", counts["errors"])
    errors = progress.read_errors_in(folder)
    with st.expander(f"企業ごとのエラー・未取得（{len(errors)}件）"):
        st.dataframe(errors, hide_index=True, width="stretch") if len(errors) else st.caption("ありません")
    with st.expander("ログ"):
        st.code(progress.read_log_in(folder) or "（ログなし）", language=None)

st.subheader("このデータセットで実行する")
if entry["status"] != datasets.COLLECTED:
    st.caption("収集が終わったデータセットを選ぶと、ここから実行できます。")
else:
    switching = entry["id"] != current["dataset"]
    st.markdown(
        ("段階0の設定のデータセットをこれに切り替えて保存し、" if switching else "")
        + "**段階1から順に、バックグラウンドで続けて実行**する。要確認が残ったとき・凍結が必要なとき（段階5→6）・"
        "失敗したときは、そこで止まる。画面で確認してから「続きから実行」で再開する。")
    if switching:
        st.caption("切り替えると段階1以降は「要再実行」になる（ATT・評価の結果を表示済みの run なら、新しい run を作る）。")
    columns = st.columns(2)
    run_clicked = columns[0].button("このデータセットで実行（段階1から）", type="primary")
    switch_clicked = switching and columns[1].button("切り替えるだけ（実行しない）")
    if run_clicked or switch_clicked:
        target_run = run_id
        try:
            if switching:
                target_run, _, _ = settings.save({**current, "dataset": chosen}, note=f"データセットを {chosen} に変更")
            elif runs.stage_state(run_id, stages.SETTINGS)["status"] != stages.DONE:
                target_run, _, _ = settings.save(current, note="設定を確定")
            if run_clicked:
                jobs.launch_chain(target_run, stages.INGEST)
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        else:
            flash = [f"`{chosen}` で段階1から続けて実行を始めました。" if run_clicked
                     else f"段階1以降で `{chosen}` を使うようにしました。"]
            if target_run != run_id:
                flash.append(f"結果閲覧後の変更のため、新しい run `{target_run}` を作りました。")
            st.session_state["datasets_flash"] = flash
            st.rerun()

st.subheader("続けて実行の状況（今の run）")
chain_panel.render(runs.current_run(), key="datasets")
