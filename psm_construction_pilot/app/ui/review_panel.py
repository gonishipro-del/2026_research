"""段階3「要確認の処理」タブ：要確認の値がある会社をまとめて除外する。または会社ごとに、含める・含めないを選び、値を確認・修正する。

値は読みやすい単位（総資産は百万円、比率は%）で表示・入力し、保存時に共変量の単位に換算する。
保存は core.review の手動入力（manual_overrides.csv への追記）を通すので、元データは変わらない。
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import streamlit as st

from core import covariates, overrides, page_images, review
from core.covariates import MANUAL, REVIEW

# 変数 → (表示名, 単位, 共変量の値 → 表示の値, 表示の値 → 共変量の値)
UNITS = {
    "log_total_assets": ("総資産", "百万円", lambda v: math.exp(v) / 1e6, lambda x: math.log(x * 1e6)),
    "roa_ordinary": ("ROA（経常利益÷総資産）", "%", lambda v: v * 100, lambda x: x / 100),
    "leverage": ("負債比率（負債÷総資産）", "%", lambda v: v * 100, lambda x: x / 100),
    "pbr": ("PBR", "倍", lambda v: v, lambda x: x),
    "earnings_surprise": ("利益サプライズ（(実績−予想)÷前期末総資産）", "%", lambda v: v * 100, lambda x: x / 100),
}
KEEP, USE_AS_IS, ENTER = "未処理のまま", "この値を使う", "正しい値を入力"


def _display(variable: str, raw: object) -> str:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return "—"
    label, unit, to_display, _ = UNITS[variable]
    shown = to_display(value)
    return f"{shown:,.0f} {unit}" if unit == "百万円" else f"{shown:,.3f} {unit}"


def flagged_companies(run_id: str) -> list[str]:
    """収集の時点で「要確認」の値があった会社（証券コード）。処理済みの会社も含める。"""
    collected = covariates.load_cells(run_id)
    return sorted(collected.loc[collected["status"] == REVIEW, "code"].unique().tolist())


def render(run_id: str, cells: pd.DataFrame, cov: pd.DataFrame, on_saved) -> None:
    """on_saved(messages, new_run) は保存後に呼ぶ（画面のメッセージ表示と再描画）。"""
    codes = flagged_companies(run_id)
    if not codes:
        st.caption("要確認の値はありません。")
        return
    excluded = overrides.active("exclude").set_index("code")
    st.markdown("要確認の値がある会社ごとに、分析に**含めるか・含めないか**を選ぶ。含める場合は、要確認の値ごとに"
                "「この値を使う」か「正しい値を入力」を選んで保存する。入力した値は手動入力ログに残り、あとで取り消せる。")

    labels = {}
    for code in codes:
        company = cells[cells["code"] == code]
        is_excluded = code in excluded.index and excluded.loc[code, "new_value"] == "1"
        pending = int(((company["status"] == REVIEW) & company["variable"].isin(UNITS)).sum())
        state = "除外中" if is_excluded else (f"未処理 {pending}件" if pending else "処理済み")
        labels[code] = (f"{code} {company['name'].iloc[0]}　（{state}）", pending and not is_excluded)
    pending_total = sum(1 for _, (_, pending) in labels.items() if pending)
    _bulk_section(codes, cells, excluded, labels, on_saved)
    st.divider()
    st.markdown("##### 1社ずつ処理する")
    st.caption(f"要確認の値があった {len(codes)}社のうち、未処理 {pending_total}社（未処理の会社を先に並べる）")
    ordered = sorted(codes, key=lambda c: (not labels[c][1], c))
    code = st.selectbox("会社", ordered, format_func=lambda c: labels[c][0], key="review_company")
    company = cells[cells["code"] == code]
    is_excluded = code in excluded.index and excluded.loc[code, "new_value"] == "1"
    _company_form(run_id, code, company["name"].iloc[0], company, cov, is_excluded,
                  excluded.loc[code, "override_id"] if is_excluded else "", on_saved)


def review_summary(codes: list[str], cells: pd.DataFrame) -> pd.DataFrame:
    """会社ごとの、要確認の項目（収集の時点）と、まだ処理していない項目の数。"""
    rows = []
    for code in codes:
        company = cells[(cells["code"] == code) & cells["variable"].isin(UNITS)]
        flagged = company[company["status"].isin([REVIEW, MANUAL])]
        rows.append({"code": code, "name": company["name"].iloc[0] if len(company) else "",
                     "items": "、".join(UNITS[v][0].split("（")[0] for v in flagged["variable"]),
                     "pending": int((company["status"] == REVIEW).sum())})
    return pd.DataFrame(rows, columns=["code", "name", "items", "pending"])


def _bulk_section(codes: list[str], cells: pd.DataFrame, excluded: pd.DataFrame, labels: dict, on_saved) -> None:
    """要確認がある会社を、まとめて分析から除外する／まとめて含め直す。"""
    is_excluded = {code: code in excluded.index and excluded.loc[code, "new_value"] == "1" for code in codes}
    summary = review_summary(codes, cells)
    pending = summary[(summary["pending"] > 0) & ~summary["code"].map(is_excluded)]["code"].tolist()
    not_excluded = [code for code in codes if not is_excluded[code]]
    excluded_now = [code for code in codes if is_excluded[code]]

    st.markdown("##### まとめて除外する")
    if not not_excluded:
        st.caption("要確認の値があった会社は、すべて除外中です。")
    else:
        scope = st.radio("対象", [f"未処理の会社（{len(pending)}社）",
                                  f"要確認の値があったすべての会社（{len(not_excluded)}社、値を確認済みの会社も含む）"],
                         horizontal=True, key="bulk_scope")
        candidates = pending if scope.startswith("未処理") else not_excluded
        if not candidates:
            st.caption("未処理の会社はありません。")
        else:
            table = summary[summary["code"].isin(candidates)].rename(
                columns={"code": "証券コード", "name": "企業名", "items": "要確認の項目", "pending": "未処理の項目数"})
            st.dataframe(table, hide_index=True, width="stretch", height=min(35 * len(table) + 38, 300))
            with st.form("bulk_exclude"):
                chosen = st.multiselect("除外する会社（外したい会社はここで取り除く）", candidates, default=candidates,
                                        format_func=lambda c: labels[c][0], key=f"bulk_codes_{len(candidates)}_{scope[:3]}")
                reason = st.text_input("除外する理由（必須。全社に同じ理由を記録する）",
                                       placeholder="例: 要確認の値を確認できないため、頑健性の確認として除外")
                if st.form_submit_button("選んだ会社をまとめて除外する", type="primary"):
                    try:
                        new_run = review.exclude_many(chosen, reason)
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        on_saved([f"{len(chosen)}社をまとめて分析から除外しました。"], new_run)
    if excluded_now:
        with st.expander(f"除外中の会社をまとめて含め直す（{len(excluded_now)}社）"):
            with st.form("bulk_include"):
                chosen = st.multiselect("含め直す会社", excluded_now, default=excluded_now,
                                        format_func=lambda c: labels[c][0], key=f"bulk_include_{len(excluded_now)}")
                reason = st.text_input("含め直す理由（必須）")
                if st.form_submit_button("選んだ会社をまとめて含め直す"):
                    try:
                        new_run = review.include_many(chosen, reason)
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        on_saved([f"{len(chosen)}社を分析に含め直しました。"], new_run)
    st.caption("除外・含め直しは会社ごとに手動入力ログへ記録され、「手動入力ログ」タブから1件ずつ取り消すこともできる。"
               "段階4（マッチング）以降は「要再実行」になる。")


def _company_form(run_id: str, code: str, name: str, company: pd.DataFrame, cov: pd.DataFrame,
                  is_excluded: bool, exclude_id: str, on_saved) -> None:
    left, right = st.columns([1, 1])
    with left:
        _page_viewer(code, cov)
    with right:
        if is_excluded:
            st.info("この会社は分析から除外しています。")
            with st.form(f"include_{code}"):
                reason = st.text_input("含め直す理由（必須）")
                if st.form_submit_button("除外を取り消して含める"):
                    try:
                        new_run = review.revoke(exclude_id, reason)
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        on_saved([f"{code} {name} を分析に含め直しました。"], new_run)
            return

        choice = st.radio("この会社を", ["含める（値を確認・修正する）", "含めない（分析から除外する）"],
                          horizontal=True, key=f"include_choice_{code}")
        if choice.startswith("含めない"):
            with st.form(f"exclude_{code}"):
                reason = st.text_input("除外する理由（必須）", placeholder="例: 財務値の抽出に誤りがあり、正しい値を確認できない")
                if st.form_submit_button("この会社を除外する", type="primary"):
                    try:
                        new_run = review.exclude(code, reason)
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        on_saved([f"{code} {name} を分析から除外しました。"], new_run)
            return

        targets = company[company["variable"].isin(UNITS) & company["status"].isin([REVIEW, MANUAL])]
        with st.form(f"values_{code}"):
            decisions = []
            for _, cell in targets.iterrows():
                variable = cell["variable"]
                label, unit, _, from_display = UNITS[variable]
                st.markdown(f"**{label}**　現在の値: {_display(variable, cell['value'])}"
                            + ("　（手動で確認済み）" if cell["status"] == MANUAL else ""))
                st.caption(f"{cell['reason_code']}：{cell['detail']}")
                columns = st.columns([2, 1])
                action = columns[0].radio("扱い", [KEEP, USE_AS_IS, ENTER], horizontal=True,
                                          key=f"action_{code}_{variable}", label_visibility="collapsed")
                typed = columns[1].text_input(f"正しい値（{unit}）", key=f"value_{code}_{variable}",
                                              label_visibility="visible")
                decisions.append((variable, cell["value"], action, typed, from_display, unit))
            source = st.text_input("出典（正しい値を入力する場合は必須）", placeholder="例: 決算短信p.8 連結貸借対照表")
            reason = st.text_input("理由（必須）", placeholder="例: 本表の単位を誤読していたため、BSの値を入力")
            submitted = st.form_submit_button("保存", type="primary")

        if submitted:
            _save(run_id, code, name, decisions, source, reason, on_saved)


def _save(run_id: str, code: str, name: str, decisions: list, source: str, reason: str, on_saved) -> None:
    chosen = [d for d in decisions if d[2] != KEEP]
    if not chosen:
        st.info("「この値を使う」か「正しい値を入力」を選んだ項目がありません。")
        return
    if not reason.strip():
        st.error("理由を入れてください")
        return
    planned = []
    for variable, current, action, typed, from_display, unit in chosen:
        if action == USE_AS_IS:
            planned.append((variable, float(current), "抽出値を確認のうえ採用"))
            continue
        if not source.strip():
            st.error("正しい値を入力する場合は、出典を入れてください")
            return
        try:
            shown = float(typed.replace(",", ""))
            value = from_display(shown)
        except ValueError:
            st.error(f"{UNITS[variable][0]} の値を数値で入れてください（単位: {unit}）")
            return
        planned.append((variable, value, source.strip()))

    new_run = run_id
    for variable, value, used_source in planned:
        new_run = review.set_value(new_run, code, variable, value, used_source, reason.strip())
    on_saved([f"{code} {name}：{len(planned)}件を保存しました。"], new_run)


def _page_viewer(code: str, cov: pd.DataFrame) -> None:
    if code not in cov.index:
        st.warning("PDFがありません")
        return
    row = cov.loc[code]
    pdf_path = Path(row["pdf_path"])
    if not pdf_path.exists():
        st.warning("PDFがありません")
        return
    hints = {"サマリー頁": row["summary_page"], "貸借対照表": row["bs_page"], "損益計算書": row["pl_page"]}
    options = {f"本決算短信 {name}（p.{int(float(page))}）": (pdf_path, int(float(page)))
               for name, page in hints.items() if page}
    forecast_pdf = Path(row["forecast_pdf"]) if row.get("forecast_pdf") else None
    if forecast_pdf is not None and forecast_pdf.exists():
        options[f"予想の資料：{row['forecast_source']}（{row['forecast_date']}、p.1）"] = (forecast_pdf, 1)
    columns = st.columns([3, 1])
    picked = columns[0].selectbox("見るページ", list(options) + ["本決算短信の番号で指定"], key=f"page_pick_{code}")
    if picked == "本決算短信の番号で指定":
        total = page_images.page_count(pdf_path)
        page = columns[1].number_input("ページ", 1, total, 1, key=f"page_no_{code}")
    else:
        pdf_path, page = options[picked]
    total = page_images.page_count(pdf_path)
    try:
        st.image(str(page_images.render_page(pdf_path, int(page))), caption=f"{pdf_path.name} p.{page}/{total}")
    except Exception as error:  # noqa: BLE001  描画できない理由を出す
        st.error(f"ページを描画できません: {error}")
