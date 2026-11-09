"""run（output/run_{日時}/）と、run ごとの段階の状態の管理。

run は設定のスナップショットと各段階の出力を持つ。結果閲覧後に上流を変えたときは
新しい run を作り（親 run の出力を複製）、元の run は残す。
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import config
from core import stages, state

RUN_HISTORY_COLUMNS = ["timestamp", "run_id", "event", "stage", "detail", "after_results_view"]


def run_dir(run_id: str) -> Path:
    return config.OUTPUT_DIR / run_id


def stage_dir(run_id: str, number: int) -> Path:
    path = run_dir(run_id) / stages.stage_dirname(number)
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path(run_id: str) -> Path:
    return run_dir(run_id) / "config.yaml"


def run_config(run_id: str) -> dict:
    return config.load_config(config_path(run_id))


def log_event(run_id: str, event: str, stage: int | str = "", detail: str = "",
              after_results_view: bool = False) -> None:
    state.append_csv(config.RUN_HISTORY_CSV, RUN_HISTORY_COLUMNS, {
        "timestamp": state.now(), "run_id": run_id, "event": event, "stage": stage,
        "detail": detail, "after_results_view": int(after_results_view),
    })


def _new_run_id(existing: dict) -> str:
    base = "run_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id, suffix = base, 1
    while run_id in existing or run_dir(run_id).exists():
        suffix += 1
        run_id = f"{base}_{suffix}"
    return run_id


def _empty_stage_states() -> dict:
    return {str(stage.number): {"status": stages.NOT_RUN, "updated_at": "", "message": ""}
            for stage in stages.STAGES}


def create_run(parent: str | None = None, reason: str = "") -> str:
    """新しい run を作って現在の run にする。parent があれば出力・設定・状態を引き継ぐ。"""
    def mutate(current: dict) -> str:
        run_id = _new_run_id(current["runs"])
        if parent:
            shutil.copytree(run_dir(parent), run_dir(run_id))
            stage_states = {key: dict(value) for key, value in current["runs"][parent]["stages"].items()}
            frozen = current["runs"][parent].get("frozen")
        else:
            run_dir(run_id).mkdir(parents=True)
            config.save_config(config.load_config(), config_path(run_id))
            stage_states, frozen = _empty_stage_states(), None
        current["runs"][run_id] = {
            "created_at": state.now(), "parent": parent, "reason": reason,
            "stages": stage_states, "frozen": frozen, "results_viewed": {},
            "changed_after_view": [],
        }
        current["current_run"] = run_id
        return run_id

    run_id = state.update(mutate)
    log_event(run_id, "run作成", detail=f"親={parent or 'なし'} {reason}".strip())
    return run_id


def current_run() -> str:
    """現在の run。まだ無ければ作る。"""
    run_id = state.load().get("current_run")
    return run_id if run_id else create_run(reason="初回")


def run_info(run_id: str) -> dict:
    return state.load()["runs"][run_id]


def stage_state(run_id: str, number: int) -> dict:
    return run_info(run_id)["stages"][str(number)]


def set_status(run_id: str, number: int, status: str, message: str = "", **extra: object) -> None:
    def mutate(current: dict) -> None:
        entry = {"status": status, "updated_at": state.now(), "message": message}
        entry.update(extra)
        current["runs"][run_id]["stages"][str(number)] = entry
    state.update(mutate)


def mark_stale_from(run_id: str, first_stage: int, reason: str) -> list[int]:
    """first_stage 以降で、一度でも実行した段階を「要再実行」にする。対象の段階番号を返す。"""
    def mutate(current: dict) -> list[int]:
        marked = []
        for number in range(first_stage, stages.LAST_STAGE + 1):
            entry = current["runs"][run_id]["stages"][str(number)]
            if entry["status"] in (stages.DONE, stages.REVIEW, stages.FAILED):
                entry.update(status=stages.STALE, updated_at=state.now(), message=reason)
                marked.append(number)
        if first_stage <= stages.FREEZE and current["runs"][run_id].get("frozen"):
            current["runs"][run_id]["frozen"] = None  # マッチングの前提が変わったら凍結も解く
        return marked

    marked = state.update(mutate)
    if marked:
        log_event(run_id, "要再実行に変更", stage=",".join(map(str, marked)), detail=reason)
    return marked


def blocking_reason(run_id: str, number: int) -> str:
    """段階 number を実行できない理由。実行できれば空文字。"""
    info = run_info(run_id)
    if info["stages"][str(number)]["status"] == stages.RUNNING:
        return "実行中です"
    for previous in range(number):
        status = info["stages"][str(previous)]["status"]
        if status != stages.DONE:
            return f"段階{previous}（{stages.BY_NUMBER[previous].title}）が完了していません（{status}）"
    if number >= stages.CAR and not info.get("frozen"):
        return f"段階{stages.FREEZE}でマッチングを確定（凍結）していません"
    return ""


def mark_results_viewed(run_id: str, number: int) -> None:
    def mutate(current: dict) -> None:
        viewed = current["runs"][run_id].setdefault("results_viewed", {})
        viewed.setdefault(str(number), state.now())
        current["last_viewed"] = {"run_id": run_id, "stage": number, "at": state.now()}
    state.update(mutate)


def results_viewed(run_id: str) -> bool:
    return bool(run_info(run_id).get("results_viewed"))
