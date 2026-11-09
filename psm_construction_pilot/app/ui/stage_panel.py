"""段階ページの共通部品（実行ボタン、進捗バー、ログ、企業ごとのエラー、入出力ファイル）。"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from core import jobs, progress, runs, stages

_POLL_SEC = 2


def header(run_id: str, number: int) -> dict:
    stage = stages.BY_NUMBER[number]
    entry = runs.stage_state(run_id, number)
    st.title(f"段階{number}: {stage.title}")
    icon = stages.STATUS_ICONS.get(entry["status"], "")
    st.markdown(f"状態: {icon} **{entry['status']}**　{entry.get('message', '')}　"
                f"<span style='color:gray'>{entry.get('updated_at', '')}</span>", unsafe_allow_html=True)
    return entry


def run_controls(run_id: str, number: int) -> None:
    """実行・再実行ボタンと進捗。実行中は数秒ごとに進捗を読み直す。"""
    entry = runs.stage_state(run_id, number)
    reason = runs.blocking_reason(run_id, number)
    label = "実行" if entry["status"] == stages.NOT_RUN else "再実行"
    columns = st.columns([1, 4])
    if columns[0].button(label, type="primary", disabled=bool(reason), key=f"run_{number}"):
        try:
            jobs.launch(run_id, number)
        except RuntimeError as error:
            st.error(str(error))
        else:
            st.rerun()
    if reason and entry["status"] != stages.RUNNING:
        columns[1].caption(f"実行できません: {reason}")
    elif entry["status"] != stages.NOT_RUN:
        columns[1].caption("再実行すると、この段階より下流は「要再実行」になります。処理はバックグラウンドで続き、画面を閉じても結果は残ります。")
    _progress_fragment(run_id, number, entry["status"] == stages.RUNNING)


def _progress_fragment(run_id: str, number: int, running: bool) -> None:
    @st.fragment(run_every=_POLL_SEC if running else None)
    def show() -> None:
        jobs.reap_dead_jobs(run_id)
        status = runs.stage_state(run_id, number)["status"]
        current = progress.read_progress(run_id, number)
        if current and current["total"]:
            st.progress(min(current["done"] / current["total"], 1.0),
                        text=f"{current['done']}/{current['total']} {current['message']}")
        if running and status != stages.RUNNING:
            st.rerun(scope="app")  # 終わったらページ全体を描き直して結果を出す
    show()


def logs_and_errors(run_id: str, number: int) -> None:
    errors = progress.read_errors(run_id, number)
    with st.expander(f"企業ごとのエラー（{len(errors)}件）", expanded=bool(len(errors))):
        if errors.empty:
            st.caption("エラーはありません")
        else:
            st.dataframe(errors, hide_index=True, width="stretch")
    with st.expander("ログ"):
        text = progress.read_log(run_id, number)
        st.code(text or "（ログなし）", language=None)


def file_list(title: str, files: list[tuple[Path, str]]) -> None:
    """ファイルの一覧（存在と説明、ダウンロード）。"""
    with st.expander(title):
        for path, description in files:
            columns = st.columns([3, 4, 1])
            columns[0].markdown(f"`{path.name}`")
            columns[1].caption(f"{description}　{path}")
            if path.exists() and path.is_file():
                columns[2].download_button("保存", path.read_bytes(), file_name=path.name,
                                           key=f"dl_{path}")
            else:
                columns[2].caption("なし")
