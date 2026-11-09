"""「続けて実行」の表示と操作（概要ページ・データセットページで共通）。"""
from __future__ import annotations

import streamlit as st

from core import jobs, pipeline, progress, runs, stages

_POLL_SEC = 3


def render(run_id: str, key: str) -> None:
    jobs.reap_dead_chain(run_id)
    chain = pipeline.read_chain(run_id)
    running = bool(chain) and chain.get("status") == pipeline.RUNNING

    @st.fragment(run_every=_POLL_SEC if running else None)
    def show() -> None:
        jobs.reap_dead_chain(run_id)
        latest = pipeline.read_chain(run_id)
        if not latest:
            st.caption("まだ続けて実行していません。")
            return
        if latest["status"] == pipeline.RUNNING:
            current = latest.get("current")
            st.info(f"🔄 続けて実行中（段階{latest.get('start')}〜{latest.get('end')}）: {latest.get('message', '')}")
            if current is not None:
                status = progress.read_progress(run_id, int(current))
                if status and status.get("total"):
                    st.progress(min(status["done"] / status["total"], 1.0),
                                text=f"段階{current}: {status['done']}/{status['total']} {status['message']}")
        elif latest["status"] == pipeline.FINISHED:
            st.success(f"✅ {latest['message']}（{latest.get('updated_at', '')}）")
        else:
            st.warning(f"⏸ {latest['message']}（{latest.get('updated_at', '')}）")
        if running and latest["status"] != pipeline.RUNNING:
            st.rerun(scope="app")

    show()
    next_stage = pipeline.first_unfinished(run_id)
    label = f"続きから実行（段階{next_stage}から最後まで）" if next_stage is not None else "すべての段階が完了しています"
    if st.button(label, disabled=running or next_stage is None or next_stage == stages.SETTINGS, key=f"chain_{key}"):
        try:
            jobs.launch_chain(run_id, next_stage)
        except RuntimeError as error:
            st.error(str(error))
        else:
            st.rerun()
    if next_stage == stages.SETTINGS:
        st.caption("段階0（設定）を保存してから実行してください。")
    st.caption("要確認が残ったとき、凍結が必要なとき（段階5→6）、失敗したときは、そこで止まる。画面で確認してから続きを実行する。")
