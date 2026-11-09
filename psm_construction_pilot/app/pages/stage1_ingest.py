"""段階1: 対象企業の確定と決算資料の取り込み。"""
from __future__ import annotations

import json

import streamlit as st

import config
from core import inputs, runs, stages
from ui import common, stage_panel

NUMBER = stages.INGEST
run_id = runs.current_run()
entry = stage_panel.header(run_id, NUMBER)
settings = runs.run_config(run_id)
st.markdown(f"上場銘柄一覧から **{settings['market']} × {'・'.join(settings['target_industries'])}** "
            "の企業を抽出し、通期の決算短信PDFを対応づける。同一企業に複数あれば最新の通期を採用する。")
stage_panel.run_controls(run_id, NUMBER)

out = runs.stage_dir(run_id, NUMBER)
stage_panel.file_list("入力ファイル", [
    (inputs.current_file("master"), "上場銘柄一覧"),
    (config.resolve_pdf_dir(settings), "決算短信PDFの既存フォルダ（参照のみ）"),
    (config.MANUAL_PDF_DIR, "手動アップロードしたPDF（段階3で追加）"),
])

summary_path = out / "summary.json"
if summary_path.exists() and entry["status"] != stages.NOT_RUN:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    columns = st.columns(5)
    columns[0].metric("対象企業", summary["target_companies"])
    columns[1].metric("採用（PDFあり・通期）", summary["included"])
    columns[2].metric("PDFなし", summary["no_pdf"])
    columns[3].metric("種類株式", summary.get("preferred_share", 0))
    columns[4].metric("採用しなかったPDF", summary["excluded_pdfs"])

    targets = common.read_csv(out / "targets.csv")
    st.subheader("対象企業一覧")
    only_excluded = st.toggle("除外した企業だけ表示")
    view = targets[targets["included"] == "0"] if only_excluded else targets
    st.dataframe(view[["code", "name", "industry33", "scale", "pdf_available", "adopted_pdf", "pdf_source",
                       "announce_date", "period_type", "standard", "text_status", "included", "exclusion_reason"]]
                 .rename(columns={"code": "コード", "name": "企業名", "industry33": "業種", "scale": "規模",
                                  "pdf_available": "PDF", "adopted_pdf": "採用したPDF", "pdf_source": "経路",
                                  "announce_date": "決算発表日", "period_type": "通期か", "standard": "会計基準",
                                  "text_status": "テキスト", "included": "採用", "exclusion_reason": "除外理由"}),
                 hide_index=True, width="stretch")

    excluded = common.read_csv(out / "excluded_pdfs.csv")
    st.subheader(f"採用しなかったPDF（{len(excluded)}件）")
    if excluded.empty:
        st.caption("ありません（各社のPDFは1件ずつ）")
    else:
        st.dataframe(excluded, hide_index=True, width="stretch")

stage_panel.logs_and_errors(run_id, NUMBER)
stage_panel.file_list("出力ファイル", [
    (out / "targets.csv", "対象企業一覧（PDFあり/なし、通期か、除外理由）"),
    (out / "excluded_pdfs.csv", "採用しなかったPDFと理由"),
    (out / "summary.json", "件数"),
])
