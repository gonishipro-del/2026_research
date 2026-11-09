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
from core import runs, stages

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
