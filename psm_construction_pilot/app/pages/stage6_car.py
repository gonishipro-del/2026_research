"""段階6: CAR の計算。"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import config
from core import runs, stages
from core.car import window_label, window_title
from ui import stage_panel

NUMBER = stages.CAR
run_id = runs.current_run()
entry = stage_panel.header(run_id, NUMBER)
settings = runs.run_config(run_id)
st.markdown(
    f"Fama-French 5ファクター（Japan 日次）。推定期間 {settings['estimation_window']}（営業日、発表日=0）、"
    f"有効観測数の下限 {settings['min_estimation_obs']}、α を{'含める' if settings['include_alpha'] else '含めない'}。"
    "株価とファクターの両方にある日だけを営業日として数える。対象は凍結したマッチと感度分析のマッチに含まれる企業。")
st.info(f"💱 {config.CURRENCY_NOTICE}")
currency_path = runs.stage_dir(run_id, NUMBER) / "currency.json"
if currency_path.exists():
    currency = json.loads(currency_path.read_text(encoding="utf-8"))
    if currency.get("stock_currency") == "usd":
        st.caption(f"この CAR は株価をドル建てに換算して計算した。レート: {currency['source']}、"
                   f"{currency['first_date']}〜{currency['last_date']}（取得 {currency['fetched_at']}）。"
                   f"日付の合わせ方: {currency['alignment']}。使ったレートは fx_usdjpy.csv に保存している。")
    else:
        st.caption("この CAR は株価を円建てのまま計算した（ファクターは米ドル建て）。")
stage_panel.run_controls(run_id, NUMBER)

out = runs.stage_dir(run_id, NUMBER)
if not (out / "car.csv").exists() or entry["status"] in (stages.NOT_RUN, stages.RUNNING):
    stage_panel.logs_and_errors(run_id, NUMBER)
    st.stop()

car = pd.read_csv(out / "car.csv", dtype={"code": str})
windows = {window_label(tuple(w)): f"CAR {window_title(tuple(w))}" for w in settings["event_windows"]}
windows["placebo"] = f"CAR プラセボ {window_title(tuple(settings['placebo_window']))}"
windows = {label: title for label, title in windows.items() if f"car_{label}" in car.columns}
columns = st.columns(4)
columns[0].metric("対象企業", len(car))
main = window_label(tuple(settings["event_window_main"]))
columns[1].metric(f"主分析 {window_title(tuple(settings['event_window_main']))} のCARを計算", int(car[f"car_{main}"].notna().sum()))
columns[2].metric("推定の観測数（中央値）", int(car["n_est"].median()))
columns[3].metric("決定係数（中央値）", f"{car['r2'].median():.3f}")

st.subheader("窓ごとに CAR を計算できた企業")
st.dataframe(pd.DataFrame([{"窓": title.removeprefix("CAR "), "計算できた企業": int(car[f"car_{label}"].notna().sum()),
                            "計算できなかった企業": int(car[f"car_{label}"].isna().sum()),
                            "CARの平均": car[f"car_{label}"].mean()} for label, title in windows.items()])
             .style.format({"CARの平均": "{:+.4f}"}), hide_index=True, width="stretch")
st.caption("長い窓は、発表日から窓の末尾までがファクターの最終日を過ぎる企業や、窓の中に株価の欠損がある企業を計算から外す。")

st.subheader("企業ごとの CAR")
shown = ["code", "name", "announce_date", *[f"car_{label}" for label in windows], "n_est", "alpha",
         "beta_mkt", "beta_smb", "beta_hml", "beta_rmw", "beta_cma", "r2", "status", "warnings"]
formats = {column: "{:+.4f}" for column in shown if column.startswith(("car_", "alpha", "beta_"))}
formats["r2"] = "{:.3f}"
st.dataframe(car[shown].style.format(formats, na_rep="—").relabel_index(
    [windows.get(c[4:], c) if c.startswith("car_") else c for c in shown], axis="columns"),
    hide_index=True, width="stretch")

warnings = pd.read_csv(out / "warnings.csv", dtype=str)
with st.expander(f"警告（{len(warnings)}件）"):
    st.dataframe(warnings, hide_index=True, width="stretch")

stage_panel.logs_and_errors(run_id, NUMBER)
stage_panel.file_list("出力ファイル", [
    (out / "car.csv", "企業ごとの CAR・α・β・決定係数・警告"),
    (out / "ar_daily.csv", "企業×日の異常収益率"),
    (out / "warnings.csv", "警告"),
    (out / "fx_usdjpy.csv", "換算に使ったドル円レート"),
    (out / "currency.json", "通貨の扱いとレートの取得元"),
])
