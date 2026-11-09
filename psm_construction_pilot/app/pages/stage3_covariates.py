"""段階3: 共変量の収集と欠損の確認（手動補完、分析対象の確定）。"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from core import covariates, overrides, page_images, progress, review, runs, stages
from core.covariates import REVIEW, VARIABLES
from ui import common, missing_view, review_panel, stage_panel

NUMBER = stages.COVARIATES
run_id = runs.current_run()
entry = stage_panel.header(run_id, NUMBER)
st.markdown(
    "採用した決算短信の**当期末**の値から共変量を集め（前期末値への切替は今後の検討事項）、段階2の ai_flag と合わせて"
    "分析用データセットを作る。財務値の抽出は既存の psm_dataset（サマリー頁を優先し本表でクロスチェック）、"
    "株価は yfinance の `{code}.T`。範囲外の値は捨てずに「要確認」にする。"
    "手動入力は `state/manual_overrides.csv` に追記するだけで元データは変えず、その場でデータセットを作り直す"
    "（段階4以降は「要再実行」になる）。**欠損が残る企業は完全ケースとして除外する。**")
with st.expander("変数の定義"):
    st.markdown("""
| 列名 | 定義 |
|---|---|
| log_total_assets | ln(総資産)。連結BSの資産合計。サマリー頁の値でクロスチェック。連結がなければ単体で代替 |
| roa_ordinary | 経常利益 ÷ 総資産。経常利益がない会計基準は税引前利益で代替（代替値） |
| leverage | 負債合計 ÷ 総資産。負債合計が直接取れなければ 総資産 − 純資産合計。1−純資産÷総資産 と照合 |
| pbr | 決算発表日の前営業日の終値 ÷ BPS。BPSは開示値を優先し自前計算で照合（乖離5%超は要確認） |
| profit_change | (当期経常利益 − 前期経常利益) ÷ 前期末総資産。前期値は同じ短信のサマリー頁の前期行 |
""")
stage_panel.run_controls(run_id, NUMBER)

for message in st.session_state.pop("stage3_flash", []):
    st.success(message)

out = runs.stage_dir(run_id, NUMBER)
if not (out / "cell_status.csv").exists() or entry["status"] in (stages.NOT_RUN, stages.RUNNING):
    stage_panel.logs_and_errors(run_id, NUMBER)
    st.stop()

dataset, cells = review.build_dataset(run_id)
cov = covariates.load_covariates(run_id).set_index("code")
collected = json.loads((out / "summary.json").read_text(encoding="utf-8"))
analysed = dataset[dataset["in_analysis"] == 1]
columns = st.columns(6)
columns[0].metric("企業", len(dataset))
columns[1].metric("分析対象（完全ケース）", len(analysed))
columns[2].metric("処置／対照", f"{int((analysed['ai_flag'] == 1).sum())}／{int((analysed['ai_flag'] == 0).sum())}")
columns[3].metric("除外", int((dataset["in_analysis"] == 0).sum()))
columns[4].metric("未確認の「要確認」セル", int(analysed["review_cells"].sum()))
columns[5].metric("抽出失敗／株価失敗", f"{collected['failed_extraction']}／{collected['failed_prices']}")
if entry["status"] == stages.REVIEW:
    st.warning("「要確認」の値が残っているため、段階4へ進めません。「要確認の処理」タブで、会社ごとに含めるか・"
               "含めないかを選び、値を確認・修正してください（「分析対象の確定」タブで、まとめてそのまま使うこともできる）。")


def flash(*messages: str) -> None:
    st.session_state["stage3_flash"] = list(messages)
    st.rerun()


def note_new_run(new_run: str) -> list[str]:
    return [f"結果閲覧後の変更のため、新しい run `{new_run}` を作りました。"] if new_run != run_id else []


tab_map, tab_review, tab_detail, tab_company, tab_log, tab_fix, tab_table = st.tabs(
    ["取得状況・欠損", "要確認の処理", "企業の詳細と手動入力", "企業の追加・除外・ai_flag", "手動入力ログ", "分析対象の確定",
     "共変量の表"])

# --- 取得状況・欠損 ----------------------------------------------------------------
with tab_map:
    st.caption("手動入力を反映した現在の状態。")
    missing_view.render(cells, cov["file"], key="stage3")

# --- 要確認の処理 ----------------------------------------------------------------
with tab_review:
    review_panel.render(run_id, cells, cov, lambda messages, new_run: flash(*messages, *note_new_run(new_run)))

# --- 企業の詳細と手動入力 ------------------------------------------------------------
with tab_detail:
    labels = (dataset["code"] + " " + dataset["name"]).tolist()
    bad_first = sorted(range(len(labels)), key=lambda i: (-int(dataset["review_cells"].iloc[i])
                                                         - 10 * int(dataset["in_analysis"].iloc[i] == 0), labels[i]))
    picked = st.selectbox("企業（要確認・欠損の多い順）", [labels[i] for i in bad_first])
    code = picked.split(" ")[0]
    company_cells = cells[cells["code"] == code]
    row = cov.loc[code] if code in cov.index else None
    pdf_path = Path(row["pdf_path"]) if row is not None else None

    left, right = st.columns([1, 1])
    with left:
        if pdf_path is not None and pdf_path.exists():
            hints = {"サマリー頁": row["summary_page"], "貸借対照表": row["bs_page"], "損益計算書": row["pl_page"]}
            hint_options = [f"{name}（p{int(float(page))}）" for name, page in hints.items() if page]
            total_pages = page_images.page_count(pdf_path)
            choice = st.radio("ページ", hint_options + ["番号で指定"], horizontal=True)
            if choice == "番号で指定":
                page = st.number_input("ページ番号", 1, total_pages, 1)
            else:
                page = int(choice.split("（p")[1].rstrip("）"))
            try:
                st.image(str(page_images.render_page(pdf_path, int(page))), caption=f"{pdf_path.name} p{page}/{total_pages}")
            except Exception as error:  # noqa: BLE001  描画できない理由を出す
                st.error(f"ページを描画できません: {error}")
            st.download_button("元のPDFを保存", pdf_path.read_bytes(), file_name=pdf_path.name, key=f"pdf_{code}")
        else:
            st.warning("PDFがありません")
    with right:
        st.markdown("**変数の状態**")
        st.dataframe(company_cells[["variable", "value", "status", "reason_code", "detail", "original_value"]]
                     .rename(columns={"variable": "変数", "value": "値", "status": "状態", "reason_code": "理由",
                                      "detail": "詳細", "original_value": "元の値"}),
                     hide_index=True, width="stretch")
        if row is not None:
            st.markdown("**抽出された候補値**")
            candidates = {
                "会計基準／連結・単体": f"{row['accounting_standard']}／{row['statement_scope']}",
                "総資産": row["total_assets"], "BPS（採用）": row["bps"], "BPS（開示）": row["bps_disclosed"],
                "BPS（自前計算）": row["bps_calc"], "BPS乖離率": row["bps_deviation"],
                "発表前営業日の終値": f"{row['price_pre_announce']}（{row['price_date']}、分割補正{row['price_split_factor']}倍）",
                "前期": row["prior_period"], "前期経常利益": row["prior_ordinary_profit"],
                "前期税引前利益": row["prior_pretax_profit"], "前期末総資産": row["prior_total_assets"],
            }
            st.dataframe(pd.DataFrame({"項目": list(candidates), "値": [str(v) for v in candidates.values()]}),
                         hide_index=True, width="stretch")
            with st.expander("抽出時の備考"):
                st.write(row["notes"].replace("；", "\n\n") or "（なし）")
        errors = progress.read_errors(run_id, NUMBER)
        errors = errors[errors["code"] == code]
        with st.expander(f"エラーログ（{len(errors)}件）"):
            st.dataframe(errors, hide_index=True, width="stretch") if len(errors) else st.caption("なし")

        st.markdown("**値を入力する**（左のページを見ながら）")
        with st.form(f"manual_value_{code}"):
            variable = st.selectbox("変数", VARIABLES)
            raw = st.text_input("値", help="比率は小数で（例: 0.523）。log_total_assets は下の欄から計算できる")
            assets_million = st.text_input("（log_total_assets のみ）総資産を百万円で入れると ln(総資産×10^6) を値にする")
            source = st.text_input("出典（必須）", placeholder=f"例: 決算短信p.{page if pdf_path else ''} 連結BS")
            reason = st.text_input("理由（必須）", placeholder="例: 本表の単位を誤読していたため／要確認の値を確認のうえ採用")
            if st.form_submit_button("保存", type="primary"):
                try:
                    if variable == "log_total_assets" and assets_million.strip():
                        value = math.log(float(assets_million.replace(",", "")) * 1e6)
                    else:
                        value = float(raw.replace(",", ""))
                    new_run = review.set_value(run_id, code, variable, value, source, reason)
                except ValueError as error:
                    st.error(str(error))
                else:
                    flash(f"{code} {variable} = {value:.6g} を保存しました（手動補完）。", *note_new_run(new_run))
        st.caption("要確認の値をそのまま採用する場合も、同じ値を入れて出典・理由を書けば「手動補完（確認済み）」として記録される。")

# --- 企業の追加・除外・ai_flag ------------------------------------------------------
with tab_company:
    st.subheader("ai_flag の手動上書き")
    with st.form("ai_flag"):
        target = st.selectbox("企業", labels, key="ai_flag_company")
        flag = st.radio("ai_flag", [1, 0], horizontal=True)
        basis = st.text_input("根拠（必須）", placeholder="例: p.5 の記述を確認。中期経営計画でAI活用を明記")
        if st.form_submit_button("上書きする"):
            try:
                new_run = review.set_ai_flag(run_id, target.split(" ")[0], int(flag), basis)
            except (ValueError, KeyError) as error:
                st.error(str(error))
            else:
                flash(f"{target} の ai_flag を {flag} にしました。", *note_new_run(new_run))

    st.subheader("分析から除外")
    with st.form("exclude"):
        target = st.selectbox("企業", labels, key="exclude_company")
        reason = st.text_input("理由（必須）")
        if st.form_submit_button("除外する"):
            try:
                new_run = review.exclude(target.split(" ")[0], reason)
            except ValueError as error:
                st.error(str(error))
            else:
                flash(f"{target} を除外しました（取り消しは手動入力ログから）。", *note_new_run(new_run))

    st.subheader("企業の手動追加")
    st.caption("PDF は data/manual/pdfs/ に {証券コード}_{企業名}_{YYYYMMDD}.pdf として保存し、段階1から再実行が必要になる。")
    with st.form("add_company"):
        columns = st.columns(3)
        new_code = columns[0].text_input("証券コード")
        new_name = columns[1].text_input("企業名")
        new_date = columns[2].date_input("決算発表日", value=date.today())
        upload = st.file_uploader("決算短信PDF", type=["pdf"])
        reason = st.text_input("理由（必須）")
        if st.form_submit_button("追加する"):
            try:
                if upload is None:
                    raise ValueError("PDFを選んでください")
                new_run = review.add_company(new_code, new_name, new_date, upload.getvalue(), reason)
            except (ValueError, FileExistsError) as error:
                st.error(str(error))
            else:
                flash(f"{new_code} {new_name} を追加しました。段階1から再実行してください。", *note_new_run(new_run))

# --- 手動入力ログ ----------------------------------------------------------------
with tab_log:
    log = overrides.load_all()
    st.caption("追記専用。取り消しは「取り消し（revoke）」の行を追記して表す。")
    if log.empty:
        st.caption("手動入力はまだありません")
    else:
        revoked = set(log.loc[log["kind"] == "revoke", "revokes"])
        log = log.assign(取り消し済み=log["override_id"].isin(revoked).map({True: "●", False: ""}))
        st.dataframe(log.iloc[::-1], hide_index=True, width="stretch")
        live = log[(log["kind"] != "revoke") & (log["取り消し済み"] == "")]
        if not live.empty:
            with st.form("revoke"):
                options = (live["override_id"] + "｜" + live["kind"] + "｜" + live["code"] + "｜" + live["target"]
                           + "｜" + live["new_value"]).tolist()
                chosen = st.selectbox("取り消す手動入力", options)
                reason = st.text_input("理由（必須）")
                if st.form_submit_button("取り消す"):
                    try:
                        new_run = review.revoke(chosen.split("｜")[0], reason)
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        flash("取り消しました。", *note_new_run(new_run))

# --- 分析対象の確定 ----------------------------------------------------------------
with tab_fix:
    st.markdown("**欠損が残る企業は完全ケースとして除外する。** 未確認の「要確認」セルが残る場合は、"
                "値を直すか、このまま使うことを確認するまで段階4へ進めない。")
    excluded = dataset[dataset["in_analysis"] == 0]
    st.markdown(f"除外する企業: **{len(excluded)}社**")
    if len(excluded):
        st.dataframe(excluded[["code", "name", "exclusion_reason"]], hide_index=True, width="stretch")
    review_rows = cells[(cells["status"] == REVIEW) & cells["code"].isin(analysed["code"])]
    if len(review_rows):
        st.markdown(f"未確認の「要確認」セル: **{len(review_rows)}**")
        st.dataframe(review_rows[["code", "name", "variable", "value", "reason_code", "detail"]],
                     hide_index=True, width="stretch")
    if entry["status"] == stages.REVIEW:
        with st.form("confirm_review"):
            note = st.text_input("「要確認」の値をこのまま使う理由（必須）")
            if st.form_submit_button("このまま使うことを確認して先へ進む"):
                if not note.strip():
                    st.error("理由を入れてください")
                else:
                    review.confirm_review_cells(run_id, note.strip())
                    st.rerun()

# --- 共変量の表 ------------------------------------------------------------------
with tab_table:
    table = covariates.load_covariates(run_id)
    shown = ["code", "name", "announce_date", "accounting_standard", "statement_scope"]
    for variable in VARIABLES:
        shown += [variable, f"{variable}_status"]
    st.dataframe(table[shown], hide_index=True, width="stretch")
    with st.expander("中間値（総資産・BPS・株価・前期値など）"):
        st.dataframe(table[["code", "name", "total_assets", "bps", "bps_disclosed", "bps_calc", "bps_deviation",
                            "price_pre_announce", "price_date", "price_split_factor", "prior_period",
                            "prior_ordinary_profit", "prior_pretax_profit", "prior_total_assets", "notes"]],
                     hide_index=True, width="stretch")
    for filename, title in (("failed_extraction.csv", "抽出できなかった企業"), ("failed_prices.csv", "株価を取得できなかった企業")):
        failed = common.read_csv(out / filename)
        st.markdown(f"**{title}（{len(failed)}社）**")
        if failed.empty:
            st.caption("ありません")
        else:
            st.dataframe(failed, hide_index=True, width="stretch")

stage_panel.logs_and_errors(run_id, NUMBER)
stage_panel.file_list("出力ファイル", [
    (out / "analysis_dataset.csv", "分析用データセット（in_analysis=1 が完全ケース）"),
    (out / "covariates.csv", "企業ごとの共変量・中間値・変数ごとの状態"),
    (out / "cell_status.csv", "企業×変数の状態（収集時点、縦持ち）"),
    (out / "cells_final.csv", "企業×変数の状態（手動入力を反映）"),
    (out / "excluded.csv", "除外した企業と理由"),
    (out / "failed_extraction.csv", "画像PDF等で抽出できなかった企業（手動補完の対象）"),
    (out / "failed_prices.csv", "株価を取得できなかった企業"),
    (out / "manual_overrides_applied.csv", "データセット作成時の手動入力ログ"),
    (out / "summary.json", "収集の件数"),
    (out / "dataset_summary.json", "分析対象の件数"),
])
