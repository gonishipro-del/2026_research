"""段階のバックグラウンド実行。

アプリから別プロセスで core.stage_runner を起動する。進捗・結果はファイルに残るため、
画面を閉じても処理は続き、結果は消えない。
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime

import config
from core import runs, stages, state

_START_TIMEOUT_SEC = 60  # 起動中のまま pid が書かれなければ起動失敗とみなす


def launch(run_id: str, number: int) -> None:
    reason = runs.blocking_reason(run_id, number)
    if reason:
        raise RuntimeError(reason)
    # 起動前に「実行中」にする（二重起動の防止）。pid は子プロセスが自分で書き込む
    runs.set_status(run_id, number, stages.RUNNING, "起動中")
    out_path = runs.stage_dir(run_id, number) / "stdout.log"
    command = [sys.executable, "-m", "core.stage_runner", "--stage", str(number), "--run", run_id]
    options: dict = {"cwd": str(config.PILOT_DIR), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    try:
        with out_path.open("w", encoding="utf-8") as out:
            subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, **options)
    except OSError as error:
        runs.set_status(run_id, number, stages.FAILED, f"起動できません: {error}")
        raise


def is_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def reap_dead_jobs(run_id: str) -> None:
    """「実行中」のまま処理が消えた段階を「失敗」にする（PCの再起動など）。"""
    for number, entry in runs.run_info(run_id)["stages"].items():
        pid = entry.get("pid")
        if entry["status"] != stages.RUNNING:
            continue
        if pid and not is_alive(int(pid)):
            runs.set_status(run_id, int(number), stages.FAILED,
                            "処理が途中で終了しました（ログを確認して再実行してください）")
        elif not pid and _seconds_since(entry["updated_at"]) > _START_TIMEOUT_SEC:
            runs.set_status(run_id, int(number), stages.FAILED,
                            "処理を起動できませんでした（stdout.log を確認してください）")


def _seconds_since(timestamp: str) -> float:
    return (datetime.now() - datetime.fromisoformat(timestamp)).total_seconds()


def launch_collection(dataset_id: str) -> None:
    """データセットの収集を別プロセスで始める（中断したものは、未処理の企業から再開する）。"""
    from core import datasets

    entry = datasets.get(dataset_id)
    if entry.get("legacy"):
        raise RuntimeError("アプリ以前のデータセットは読み取り専用です")
    if entry["status"] == datasets.COLLECTING:
        raise RuntimeError("収集中です")
    datasets.update(dataset_id, status=datasets.COLLECTING, pid=None, message="起動中")
    folder = datasets.path(dataset_id)
    command = [sys.executable, "-m", "core.collector", "--dataset", dataset_id]
    options: dict = {"cwd": str(config.PILOT_DIR), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    try:
        with (folder / "stdout.log").open("w", encoding="utf-8") as out:
            subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, **options)
    except OSError as error:
        datasets.update(dataset_id, status=datasets.FAILED, message=f"起動できません: {error}")
        raise


def reap_dead_collections() -> None:
    """「収集中」のまま処理が消えたデータセットを「中断」にする（再開できる）。"""
    from core import datasets

    for entry in datasets.all_datasets():
        if entry["status"] != datasets.COLLECTING:
            continue
        pid = entry.get("pid")
        if (pid and not is_alive(int(pid))) or (not pid and _seconds_since(entry.get("updated_at") or state.now())
                                               > _START_TIMEOUT_SEC):
            datasets.update(entry["id"], status=datasets.INTERRUPTED, pid=None,
                            message="収集が途中で止まりました（再開すると未処理の企業から続けます）")



def launch_chain(run_id: str, start: int, end: int = stages.LAST_STAGE) -> None:
    """段階 start〜end を続けて実行する処理を、別プロセスで始める（core.pipeline）。"""
    from core import pipeline

    chain = pipeline.read_chain(run_id)
    if chain and chain.get("status") == pipeline.RUNNING:
        raise RuntimeError("続けて実行中です")
    state.write_json_atomic(pipeline.chain_path(run_id), {
        "status": pipeline.RUNNING, "start": start, "end": end, "pid": None, "message": "起動中",
        "started_at": state.now(), "updated_at": state.now(), "done": [], "current": None})
    command = [sys.executable, "-m", "core.pipeline", "--run", run_id, "--from", str(start), "--to", str(end)]
    options: dict = {"cwd": str(config.PILOT_DIR), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    try:
        with (runs.run_dir(run_id) / "chain_stdout.log").open("w", encoding="utf-8") as out:
            subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, **options)
    except OSError as error:
        state.write_json_atomic(pipeline.chain_path(run_id), {"status": pipeline.FAILED, "message": f"起動できません: {error}",
                                                              "updated_at": state.now()})
        raise


def reap_dead_chain(run_id: str) -> None:
    """「実行中」のまま処理が消えた続けて実行を「停止」にする。"""
    from core import pipeline

    chain = pipeline.read_chain(run_id)
    if not chain or chain.get("status") != pipeline.RUNNING:
        return
    pid = chain.get("pid")
    if (pid and not is_alive(int(pid))) or (not pid and _seconds_since(chain["updated_at"]) > _START_TIMEOUT_SEC):
        chain.update(status=pipeline.STOPPED, message="続けて実行が途中で止まりました（「続きから実行」で再開できます）",
                     updated_at=state.now(), current=None)
        state.write_json_atomic(pipeline.chain_path(run_id), chain)
