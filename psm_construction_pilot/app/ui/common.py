"""全ページ共通の表示（上部の注記、サイドバーの段階一覧）。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
from core import runs, stages


def banners(run_id: str) -> None:
    st.warning(f"**{config.TRIAL_NOTICE}**", icon="🧪")
    st.caption(f"💱 {config.CURRENCY_NOTICE}")
    changed = runs.run_info(run_id).get("changed_after_view") or []
    if changed:
        lines = "\n".join(f"- {item['at']} {item['what']}（元の run: {item['from_run']}）" for item in changed)
        st.error("**結果閲覧後の変更があります。** この run は、ATT・評価の結果を表示した後に"
                 f"上流を変更して作られました。元の run は残っています。\n\n{lines}", icon="🚩")


def sidebar_status(run_id: str) -> None:
    info = runs.run_info(run_id)
    with st.sidebar:
        st.markdown(f"**現在の run**: `{run_id}`")
        lines = []
        for stage in stages.STAGES:
            entry = info["stages"][str(stage.number)]
            icon = stages.STATUS_ICONS.get(entry["status"], "")
            lines.append(f"{icon} 段階{stage.number} {stage.title}　`{entry['status']}`")
        st.markdown("  \n".join(lines))
        frozen = info.get("frozen")
        st.caption("マッチング: " + (f"凍結済み（{frozen['at']}）" if frozen else "未凍結"))


def status_table(run_id: str) -> pd.DataFrame:
    info = runs.run_info(run_id)
    rows = []
    for stage in stages.STAGES:
        entry = info["stages"][str(stage.number)]
        rows.append({"段階": stage.number, "内容": stage.title,
                     "状態": f"{stages.STATUS_ICONS.get(entry['status'], '')} {entry['status']}",
                     "更新": entry.get("updated_at", ""), "メッセージ": entry.get("message", "")})
    return pd.DataFrame(rows)


def read_csv(path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
