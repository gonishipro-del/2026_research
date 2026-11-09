"""入力ファイルの更新: 現在の入力と検証結果、差し替え（旧版は archive/ に残す）。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
from core import inputs, stages
from ui import common

st.title("入力ファイルの更新")
st.markdown("入力は常に `data/inputs/` のファイルを使う（Webからの自動取得はしない）。差し替えると旧版を "
            "`archive/` に残し、下流の段階を「要再実行」にする。検証に通らないファイルには差し替えない。")


def show_factor_report(report: dict) -> None:
    if "stats" not in report:
        return
    st.markdown(f"- 期間: {report['first_date']} 〜 {report['last_date']}（{report['rows']}行、"
                f"日付の最大間隔 {report['max_gap_days']}日）")
    st.markdown(f"- 列: {', '.join(report['columns'])}　地域の判定根拠: {report.get('region_basis', '')}")
    stats = pd.DataFrame(report["stats"]).T
    stats.columns = ["平均", "標準偏差", "最小", "最大"]
    st.dataframe(stats.style.format("{:.6f}"), width="stretch")
    missing = pd.DataFrame(report["missing_counts"]).T
    st.caption("欠損コードの件数（行=コード）")
    st.dataframe(missing, width="stretch")


def show_master_report(report: dict) -> None:
    if "prime_count" not in report:
        return
    st.markdown(f"- 「日付」列: {', '.join(report['as_of_dates'])}　全{report['rows']}行　"
                f"プライム（内国株式）: {report['prime_count']}社")
    for name, detail in report["industries"].items():
        st.markdown(f"- {name}: {detail['prime_count']}社（33業種コード {', '.join(detail['industry33_codes'])}、"
                    f"17業種 {', '.join(detail['industry17'])}）")


for kind, label in inputs.KINDS.items():
    st.header(label)
    st.caption(f"置き場所: {config.INPUT_DIRS[kind]}　影響する段階: 段階{stages.INPUT_FIRST_STAGE[kind]}以降")
    try:
        entry = inputs.register_current(kind)
    except (FileNotFoundError, ValueError) as error:
        st.error(str(error))
        entry = None
    if entry:
        report = entry["validation"]
        (st.success if report["ok"] else st.error)(
            f"`{entry['file']}`　更新 {entry['modified_at']}　SHA256 `{entry['sha256']}`")
        for message in report["errors"]:
            st.error(message)
        for message in report.get("warnings", []):
            st.warning(message)
        (show_factor_report if kind == "factors" else show_master_report)(report)
        with st.expander("検証結果（全文）"):
            st.json(report)

    uploaded = st.file_uploader(f"差し替えるファイル（{', '.join(sorted(inputs.ALLOWED_SUFFIXES[kind]))}）",
                                key=f"upload_{kind}")
    if uploaded is not None and st.button("差し替える", key=f"replace_{kind}"):
        ok, report = inputs.replace(kind, uploaded.name, uploaded.getvalue())
        if ok:
            st.success("差し替えました。旧版は archive/ に残しました。")
            st.rerun()
        else:
            st.error("検証に通らないため差し替えていません: " + "; ".join(report["errors"]))

st.header("入力ファイルの履歴")
if config.INPUT_HISTORY_CSV.exists():
    st.dataframe(common.read_csv(config.INPUT_HISTORY_CSV).iloc[::-1], hide_index=True, width="stretch")
