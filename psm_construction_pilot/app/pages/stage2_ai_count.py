"""段階2: AI言及のカウントと、ヒット箇所の確認。"""
from __future__ import annotations

import html
import json
from pathlib import Path

import streamlit as st

from core import ai_count, ingest, page_images, runs, stages
from ui import ai_charts, common, stage_panel

NUMBER = stages.AI_COUNT
run_id = runs.current_run()
entry = stage_panel.header(run_id, NUMBER)
settings = runs.run_config(run_id)
st.markdown(f"キーワード: {'、'.join(settings['ai_keywords'])}　／　ヒット数が "
            f"**{settings['treatment_threshold']}以上** で ai_flag=1。英字は単語境界つきで照合"
            "（RAIL・MAIN 等は数えない）。テキストが取れないPDFは判定不能。")
stage_panel.run_controls(run_id, NUMBER)

for message in st.session_state.pop("stage2_flash", []):
    st.success(message)

out = runs.stage_dir(run_id, NUMBER)
summary_path = out / "summary.json"
if not summary_path.exists() or entry["status"] in (stages.NOT_RUN, stages.RUNNING):
    stage_panel.logs_and_errors(run_id, NUMBER)
    st.stop()

summary = json.loads(summary_path.read_text(encoding="utf-8"))
columns = st.columns(4)
columns[0].metric("処置群（言及あり）", summary[ai_count.TREATED])
columns[1].metric("対照群（言及なし）", summary[ai_count.CONTROL])
columns[2].metric("判定不能", summary[ai_count.UNDETERMINED])
columns[3].metric("ヒット", summary["hits_raw"])

if summary["small_groups"]:
    st.warning(f"{'・'.join(summary['small_groups'])}が {settings['min_group_size']}社未満です。"
               "マッチング・検定の結果が不安定になります。続ける場合は確認してください。")
    if entry["status"] == stages.REVIEW:
        with st.form("confirm"):
            note = st.text_input("確認のメモ（必須）")
            if st.form_submit_button("件数を確認して先へ進む"):
                if not note.strip():
                    st.error("メモを入れてください")
                else:
                    ai_count.confirm_small_groups(run_id, note.strip())
                    st.rerun()
if summary["判定不能"]:
    st.info("判定不能の企業は、段階3で欠損として表示する（ai_flag の手動上書きも段階3で行う）。")

flags = ai_count.load_flags(run_id)
hits = ai_count.load_hits(run_id)
st.subheader("言及あり・なしの内訳")
st.altair_chart(ai_charts.group_bar(flags), width="stretch")

st.subheader("企業ごとのヒット件数")
industry_of = ingest.load_targets(run_id).set_index("code")["industry33"]
columns = st.columns([2, 1])
industry = columns[0].selectbox("業種", ["すべて"] + sorted(industry_of[industry_of.index.isin(hits["code"])].unique()),
                                key="hit_industry")
top = columns[1].number_input("件数の多い順に何社まで表示", 5, 2000, 40, step=5, key="hit_top")
shown = hits if industry == "すべて" else hits[hits["code"].map(industry_of) == industry]
keep = shown.groupby("code").size().sort_values(ascending=False).head(int(top)).index
st.altair_chart(ai_charts.hit_bars(shown[shown["code"].isin(keep)], flags[flags["code"].isin(keep)],
                                   settings["treatment_threshold"]), width="stretch")
treated_n = int((flags["group"] == ai_count.TREATED).sum())
st.caption(f"ヒットがあった{hits['code'].nunique()}社のうち、選んだ業種で件数の多い{len(keep)}社を表示。点線を越えた{treated_n}社が言及あり。"
           f"ヒットが0件の{int((flags['hits_raw'].astype(int) == 0).sum())}社（言及なし）は表示していない。")
with st.expander("表で見る"):
    keyword_columns = [column for column in flags.columns if column.startswith("kw_")]
    st.dataframe(flags[["code", "name", "group", "ai_count", "ai_flag", *keyword_columns, "text_status"]]
                 .rename(columns={"code": "コード", "name": "企業名", "group": "群", "ai_count": "ヒット",
                                  "text_status": "テキスト", **{c: c[3:] for c in keyword_columns}}),
                 hide_index=True, width="stretch")

st.subheader("ヒット箇所の確認")
if hits.empty:
    st.caption("ヒットはありません")
else:
    counts = hits.groupby(["code", "name"]).size().reset_index(name="n").sort_values(["n", "code"], ascending=[False, True])
    company = st.selectbox("企業", counts["code"].tolist(),
                           format_func=lambda code: f"{code} {counts.set_index('code').loc[code, 'name']}"
                                                    f"（{counts.set_index('code').loc[code, 'n']}か所）")
    company_hits = hits[hits["code"] == company].assign(
        page_no=lambda frame: frame["page"].astype(int), offset_no=lambda frame: frame["offset"].astype(int)
    ).sort_values(["page_no", "offset_no"]).reset_index(drop=True)
    position = st.radio("何か所目のヒットか", range(len(company_hits)), horizontal=True,
                        format_func=lambda i: f"{i + 1}か所目（p.{company_hits.loc[i, 'page_no']}）",
                        key=f"hit_position_{company}")
    hit = company_hits.loc[position]

    with st.container(border=True):
        st.caption(f"{position + 1}か所目 / 全{len(company_hits)}か所　p.{hit['page_no']}")
        st.markdown(f"…{html.escape(hit['before'])}<mark style='background:#fab219;color:#0b0b0b;padding:0 2px'>"
                    f"<b>{html.escape(hit['match'])}</b></mark>{html.escape(hit['after'])}…", unsafe_allow_html=True)
        if st.button(f"p.{hit['page_no']} を開く", key=f"open_{hit['hit_id']}"):
            st.session_state["stage2_open_page"] = hit["hit_id"]
        if st.session_state.get("stage2_open_page") == hit["hit_id"]:
            pdf_path = Path(ingest.load_targets(run_id).set_index("code").loc[company, "pdf_path"])
            try:
                st.image(str(page_images.render_page(pdf_path, int(hit["page_no"]))),
                         caption=f"{pdf_path.name} p.{hit['page_no']}")
            except Exception as error:  # noqa: BLE001  描画できない理由を画面に出す
                st.error(f"ページを描画できません: {error}")
            if st.button("ページを閉じる", key=f"close_{hit['hit_id']}"):
                st.session_state.pop("stage2_open_page", None)
                st.rerun()

stage_panel.logs_and_errors(run_id, NUMBER)
stage_panel.file_list("出力ファイル", [
    (out / "hits.csv", "ヒット一覧（前後の文脈、ページ）"),
    (out / "extraction.csv", "企業ごとのテキスト抽出結果"),
    (out / "ai_flags.csv", "企業ごとの件数と ai_flag"),
    (out / "summary.json", "処置群・対照群・判定不能の件数"),
])
