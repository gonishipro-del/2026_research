"""段階0: 設定の保存と変更履歴。

変更は state/config_history.csv に、いつ・何を・どの段階の結果を見た後に変えたかとともに残す。
変更した項目に応じて、影響を受ける段階から下流を「要再実行」にする。

単体実行: python -m core.settings   （現在の設定を確定して段階0を完了にする）
"""
from __future__ import annotations

import json

import config
from core import changes, runs, stages, state

HISTORY_COLUMNS = ["timestamp", "run_id", "key", "old_value", "new_value", "last_viewed_stage",
                   "after_results_view", "note"]


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def validate(values: dict) -> list[str]:
    errors = []
    from core import covariates, datasets  # 設定の読み込みより後で読む（循環を避ける）

    try:
        dataset = datasets.get(values["dataset"])
        if dataset["status"] != datasets.COLLECTED:
            errors.append(f"データセット {values['dataset']} はまだ収集が終わっていません（{dataset['status']}）")
    except KeyError as error:
        errors.append(str(error))
    unknown = [name for name in values["covariates"] if name not in covariates.VARIABLES]
    if unknown or not values["covariates"]:
        errors.append(f"共変量は {covariates.VARIABLES} から1つ以上選んでください（不明: {unknown}）")
    if not values["ai_keywords"]:
        errors.append("AI_KEYWORDS を1つ以上入れてください")
    if values["treatment_threshold"] < 1:
        errors.append("処置の閾値は1以上")
    if values["caliper_sd"] <= 0:
        errors.append("キャリパーは正の値")
    start, end = values["estimation_window"]
    if not start < end < min(window[0] for window in values["event_windows"]):
        errors.append("推定期間はイベントウィンドウより前で、開始 < 終了")
    for window in [*values["event_windows"], values["event_window_main"], values["placebo_window"]]:
        if window[0] > window[1]:
            errors.append(f"ウィンドウの開始が終了より後: {window}")
    if values["event_window_main"] not in values["event_windows"]:
        errors.append("主分析のウィンドウは保存するウィンドウの一覧に含めてください")
    if values["stock_currency"] not in ("usd", "jpy"):
        errors.append("株価リターンの通貨は usd か jpy")
    if values["min_estimation_obs"] > end - start + 1:
        errors.append("有効観測数の下限が推定期間の日数を超えています")
    return errors


# 廃止した共変量 → 置き換え先（保存するときに置き換える。過去の run のスナップショットは変えない）
RENAMED_COVARIATES = {"profit_change": "earnings_surprise"}


def migrate(values: dict) -> dict:
    """廃止した設定値を、現在の名前に置き換える。"""
    renamed = []
    for name in values["covariates"]:
        new_name = RENAMED_COVARIATES.get(name, name)
        if new_name not in renamed:
            renamed.append(new_name)
    return {**values, "covariates": renamed}


def save(values: dict, note: str = "") -> tuple[str, list[str], list[int]]:
    """設定を保存する。(適用した run, 変更した項目, 要再実行にした段階) を返す。"""
    values = migrate(config.with_defaults(values))
    errors = validate(values)
    if errors:
        raise ValueError("; ".join(errors))

    run_id = runs.current_run()
    old = runs.run_config(run_id)
    changed = [key for key in values if old.get(key) != values[key]]
    first_settings = runs.stage_state(run_id, 0)["status"] != stages.DONE
    if not changed and not first_settings:
        return run_id, [], []

    marked: list[int] = []
    after_view = False
    if changed:
        what = "設定の変更: " + ", ".join(changed)
        first_stage = min(config.SETTING_FIRST_STAGE.get(key, 1) for key in changed)
        run_id, after_view = changes.begin_upstream_change(what, first_stage)
    last_viewed = state.load().get("last_viewed") or {}

    config.save_config(values, runs.config_path(run_id))
    config.save_config(values)  # 次に作る run の既定値にもする
    for key in changed:
        state.append_csv(config.CONFIG_HISTORY_CSV, HISTORY_COLUMNS, {
            "timestamp": state.now(), "run_id": run_id, "key": key,
            "old_value": _dump(old.get(key)), "new_value": _dump(values[key]),
            "last_viewed_stage": f"段階{last_viewed['stage']}（{last_viewed['at']}）" if last_viewed else "",
            "after_results_view": int(after_view), "note": note,
        })
    if changed:
        marked = changes.finish_upstream_change(run_id, what, first_stage)
    runs.set_status(run_id, 0, stages.DONE, "設定を保存" if changed else "設定を確定")
    runs.log_event(run_id, "設定の保存", stage=0, detail=", ".join(changed) or "変更なし（確定）")
    return run_id, changed, marked


def main() -> int:
    run_id, changed, _ = save(runs.run_config(runs.current_run()), note="CLI で確定")
    print(f"run={run_id} 変更={changed or 'なし'} → 段階0 完了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
