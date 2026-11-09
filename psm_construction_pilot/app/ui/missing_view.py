"""欠損・取得状況の表示（段階3）。

企業×変数のヒートマップ（色と略字で状態・理由）、企業単位の「何が収集できなかったか」の一覧、表で見る、をまとめて出す。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.covariates import MISSING, OK, REVIEW
from ui import charts


def render(cells: pd.DataFrame, files: pd.Series, key: str) -> None:
    """cells は企業×変数の状態（code, name, variable, value, status, reason_code, detail）。
    files は証券コード → 決算短信PDFのファイル名。key は画面部品の重複を避ける接頭辞。"""
    columns = st.columns([2, 2])
    sort_by = columns[0].radio("行の並び", ["証券コード順", "欠損・要確認の多い順"], horizontal=True, key=f"{key}_sort")
    issue_codes = set(cells.loc[cells["status"] != OK, "code"])
    show_all = columns[1].toggle(f"全社を表示（既定は取得済み以外がある{len(issue_codes)}社だけ）", key=f"{key}_all")
    if not show_all:
        cells = cells[cells["code"].isin(issue_codes)]
        if cells.empty:
            st.caption("すべての会社で、すべての値を取得済みです")
            return
    problem = cells.assign(bad=cells["status"].isin([MISSING, REVIEW]).astype(int))
    per_company = problem.groupby(["code", "name"])["bad"].sum().reset_index()
    per_company["label"] = per_company["code"] + " " + per_company["name"]
    if sort_by == "証券コード順":
        order = per_company.sort_values("code")["label"].tolist()
    else:
        order = per_company.sort_values(["bad", "code"], ascending=[False, True])["label"].tolist()

    st.markdown(charts.legend_markdown(), unsafe_allow_html=True)
    left, right = st.columns([3, 2])
    with left:
        st.altair_chart(charts.heatmap(cells, order), width="stretch")
    with right:
        st.markdown("**何が収集できなかったか（企業単位）**")
        issues = cells[cells["status"] != OK]
        if issues.empty:
            st.caption("すべて取得済みです")
        else:
            listing = issues.assign(企業=issues["code"] + " " + issues["name"]).groupby("企業").apply(
                lambda frame: pd.Series({
                    "変数": "、".join(frame["variable"]),
                    "状態・理由": "、".join(f"{v}={s}{'（' + r + '）' if r and r != s else ''}"
                                         for v, s, r in zip(frame["variable"], frame["status"], frame["reason_code"])),
                    "PDF": files.get(frame["code"].iloc[0], ""),
                }), include_groups=False).reset_index()
            st.dataframe(listing, hide_index=True, width="stretch")
    with st.expander("表で見る（企業×変数の状態）"):
        columns = [c for c in ["code", "name", "variable", "value", "status", "reason_code", "detail", "source_type"]
                   if c in cells.columns]
        st.dataframe(cells[columns], hide_index=True, width="stretch")
