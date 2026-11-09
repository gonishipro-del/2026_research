"""建設業 PSM 試運転アプリ（Streamlit）。

起動（リポジトリ直下から）:
  venv\\Scripts\\python.exe -m streamlit run psm_construction_pilot/app/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PILOT_DIR = Path(__file__).resolve().parents[1]
if str(PILOT_DIR) not in sys.path:
    sys.path.insert(0, str(PILOT_DIR))

import streamlit as st  # noqa: E402

import config  # noqa: E402,F401  （psm_dataset を import できるようにする）
from core import jobs, runs, stages  # noqa: E402
from ui import common  # noqa: E402

st.set_page_config(page_title="PSM 試運転", page_icon="🧪", layout="wide")

run_id = runs.current_run()
jobs.reap_dead_jobs(run_id)
info = runs.run_info(run_id)


def stage_label(number: int, title: str) -> str:
    status = info["stages"][str(number)]["status"]
    return f"{stages.STATUS_ICONS.get(status, '')} 段階{number} {title}"


PAGES = Path(__file__).parent / "pages"
navigation = st.navigation({
    "全体": [
        st.Page(PAGES / "home.py", title="概要", icon="🏠", default=True),
        st.Page(PAGES / "datasets.py", title="データセット（収集）", icon="🗂️"),
        st.Page(PAGES / "inputs.py", title="入力ファイルの更新", icon="📥"),
    ],
    "段階": [
        st.Page(PAGES / "stage0_settings.py", title=stage_label(stages.SETTINGS, "設定")),
        st.Page(PAGES / "stage1_ingest.py", title=stage_label(stages.INGEST, "対象企業")),
        st.Page(PAGES / "stage2_ai_count.py", title=stage_label(stages.AI_COUNT, "AI言及")),
        st.Page(PAGES / "stage3_covariates.py", title=stage_label(stages.COVARIATES, "共変量と欠損の確認")),
        st.Page(PAGES / "stage4_matching.py", title=stage_label(stages.MATCHING, "傾向スコアとマッチング")),
        st.Page(PAGES / "stage5_freeze.py", title=stage_label(stages.FREEZE, "バランス診断と凍結")),
        st.Page(PAGES / "stage6_car.py", title=stage_label(stages.CAR, "CAR")),
        st.Page(PAGES / "stage7_att.py", title=stage_label(stages.ATT, "ATT")),
        st.Page(PAGES / "stage8_evaluate.py", title=stage_label(stages.EVALUATE, "評価（頑健性・感度）")),
    ],
})
common.banners(run_id)
common.sidebar_status(run_id)
navigation.run()
