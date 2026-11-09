"""段階0: 設定。"""
from __future__ import annotations

import json

import streamlit as st

import config
from core import covariates, datasets, inputs, master, runs, settings
from ui import common, stage_panel

run_id = runs.current_run()
stage_panel.header(run_id, 0)
current = runs.run_config(run_id)
for message in st.session_state.pop("stage0_flash", []):
    st.success(message)

st.info(f"💱 **通貨の注意**: {config.CURRENCY_NOTICE}"
        "頑健性検証（段階8）で、TOPIX（または日経平均）を用いた市場モデルの結果も並べる。")

try:
    industry_options = master.industries(master.load(inputs.current_file("master")), current["market"])
except (FileNotFoundError, ValueError) as error:
    st.error(f"上場銘柄一覧を読めないため業種の選択肢を出せません: {error}")
    industry_options = sorted(set(current["target_industries"]) | set(current["excluded_industries"]))
industry_options = sorted(set(industry_options) | set(current["target_industries"]) | set(current["excluded_industries"]))
dataset_ids = datasets.collected_ids()
if current["dataset"] not in dataset_ids:
    dataset_ids.append(current["dataset"])


def parse_windows(text: str) -> list[list[int]]:
    windows = json.loads(f"[{text}]")
    return [[int(start), int(end)] for start, end in windows]


def window_text(windows: list[list[int]]) -> str:
    return ", ".join(f"[{start},{end}]" for start, end in windows)


with st.form("settings"):
    st.subheader("対象")
    dataset = st.selectbox("データセット（収集済みのもの）", dataset_ids, index=dataset_ids.index(current["dataset"]),
                           help="新しく集めるときは「データセット」ページで。")
    target_industries = st.multiselect(
        "対象業種（33業種区分。空なら全業種）", industry_options, default=current["target_industries"])
    excluded_industries = st.multiselect(
        "除外する業種", industry_options, default=current["excluded_industries"],
        help="既定は金融業（銀行・保険・証券・その他金融）。負債比率や ROA の意味が他の業種と違うため")

    st.subheader("処置（AI言及）")
    keywords = st.text_area("AI_KEYWORDS（1行に1語）", "\n".join(current["ai_keywords"]), height=180)
    columns = st.columns(3)
    threshold = columns[0].number_input("処置の閾値（有効ヒット数がこれ以上で ai_flag=1）", 1, 100,
                                        int(current["treatment_threshold"]))
    min_group = columns[1].number_input("処置群・対照群の最小社数（未満なら警告）", 1, 1000,
                                        int(current["min_group_size"]))
    context = columns[2].number_input("ヒット前後に保存する文字数", 5, 200, int(current["hit_context_chars"]))

    st.subheader("マッチング")
    columns = st.columns(3)
    caliper = columns[0].number_input("キャリパー（ロジット傾向スコアの標準偏差の倍数）", 0.01, 2.0,
                                      float(current["caliper_sd"]), step=0.05)
    replacement = columns[1].radio("主分析の復元", ["なし", "あり"], index=int(current["replacement_main"]),
                                   horizontal=True, help="復元あり・なしの両方を計算して並べる。主分析は段階5の凍結時に確定する。")
    seed = columns[2].number_input("seed", 0, 2**31 - 1, int(current["seed"]))
    chosen_covariates = st.multiselect("共変量（傾向スコアの説明変数）", covariates.VARIABLES,
                                       default=[c for c in current["covariates"] if c in covariates.VARIABLES])
    exact_match = st.checkbox("33業種の中で完全一致させる（同じ業種の会社どうしでペアを組む。業種は説明変数に入れない）",
                              bool(current["exact_match_industry"]))

    st.subheader("イベントスタディ")
    columns = st.columns(3)
    windows_text = columns[0].text_input("保存するイベントウィンドウ", window_text(current["event_windows"]))
    main_window = columns[1].text_input("主分析のウィンドウ", window_text([current["event_window_main"]]))
    placebo = columns[2].text_input("プラセボのウィンドウ", window_text([current["placebo_window"]]))
    columns = st.columns(3)
    estimation = columns[0].text_input("推定期間（営業日、発表日=0）", window_text([current["estimation_window"]]))
    min_obs = columns[1].number_input("推定の有効観測数の下限", 10, 1000, int(current["min_estimation_obs"]))
    include_alpha = columns[2].checkbox("異常収益率の計算でαを含める", bool(current["include_alpha"]))
    currency_labels = {"usd": "ドル建てに換算する（ファクターに合わせる）", "jpy": "円建てのまま（換算しない）"}
    stock_currency = st.radio("株価リターンの通貨", list(currency_labels), format_func=currency_labels.get,
                              index=list(currency_labels).index(current["stock_currency"]), horizontal=True,
                              help="ファクターと RF は米ドル建て。ドル建てに換算すると単位が揃う。レートは yfinance の JPY=X を取得して使う。")

    st.subheader("評価")
    columns = st.columns(3)
    caliper_grid = columns[0].text_input("キャリパーの感度分析", ", ".join(map(str, current["caliper_grid"])))
    bootstrap = columns[1].number_input("ブートストラップ回数", 100, 100000, int(current["bootstrap_reps"]))
    gammas = columns[2].text_input("Rosenbaum bounds の Γ", ", ".join(map(str, current["rosenbaum_gammas"])))

    note = st.text_input("変更の理由・メモ（変更履歴に残す）")
    submitted = st.form_submit_button("保存", type="primary")

if submitted:
    try:
        values = {
            **current,
            "target_industries": target_industries,
            "dataset": dataset,
            "excluded_industries": excluded_industries,
            "ai_keywords": [word.strip() for word in keywords.splitlines() if word.strip()],
            "treatment_threshold": int(threshold),
            "min_group_size": int(min_group),
            "hit_context_chars": int(context),
            "caliper_sd": float(caliper),
            "replacement_main": replacement == "あり",
            "seed": int(seed),
            "covariates": chosen_covariates,
            "exact_match_industry": bool(exact_match),
            "event_windows": parse_windows(windows_text),
            "event_window_main": parse_windows(main_window)[0],
            "placebo_window": parse_windows(placebo)[0],
            "estimation_window": parse_windows(estimation)[0],
            "min_estimation_obs": int(min_obs),
            "include_alpha": bool(include_alpha),
            "stock_currency": stock_currency,
            "caliper_grid": [float(value) for value in caliper_grid.split(",") if value.strip()],
            "bootstrap_reps": int(bootstrap),
            "rosenbaum_gammas": [float(value) for value in gammas.split(",") if value.strip()],
        }
        saved_run, changed, marked = settings.save(values, note)
    except (ValueError, json.JSONDecodeError, IndexError, TypeError) as error:
        st.error(f"保存できません: {error}")
    else:
        flash = ["保存しました。" + (f"変更: {', '.join(changed)}" if changed else "変更なし（設定を確定）")
                 + (f"／要再実行: 段階{', '.join(map(str, marked))}" if marked else "")]
        if saved_run != run_id:
            flash.append(f"結果閲覧後の変更のため、新しい run `{saved_run}` を作りました（元の run は残っています）。")
        st.session_state["stage0_flash"] = flash
        st.rerun()

st.subheader("設定の変更履歴")
if config.CONFIG_HISTORY_CSV.exists():
    st.dataframe(common.read_csv(config.CONFIG_HISTORY_CSV).iloc[::-1], hide_index=True, width="stretch")
else:
    st.caption("変更はまだありません")
