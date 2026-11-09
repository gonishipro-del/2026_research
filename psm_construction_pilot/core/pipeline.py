"""段階を続けて実行する（データセットを選んで一括実行・続きから実行）。

指定した段階から順に core.stage_runner.execute を呼ぶ。次の場合はそこで止まり、理由を記録する:
  - 段階が「完了」以外（要確認・失敗）で終わった … 画面で確認・修正してから「続きから実行」する
  - 次の段階が実行できない（凍結していない など）… 段階5で診断を確認して凍結してから続ける
進み具合は output/run_*/chain.json に書き、アプリから読む。

単体実行: python -m core.pipeline --run RUN_ID --from 1 --to 8
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback

import config  # noqa: F401  （sys.path の設定）
from core import runs, stage_runner, stages, state

RUNNING, STOPPED, FINISHED, FAILED = "実行中", "停止", "完了", "失敗"


def chain_path(run_id: str):
    return runs.run_dir(run_id) / "chain.json"


def read_chain(run_id: str) -> dict | None:
    return state.read_json(chain_path(run_id), None)


def _write(run_id: str, **fields: object) -> dict:
    current = read_chain(run_id) or {}
    current.update(fields, updated_at=state.now())
    state.write_json_atomic(chain_path(run_id), current)
    return current


def first_unfinished(run_id: str) -> int | None:
    """「完了」になっていない最初の段階（全部完了なら None）。"""
    info = runs.run_info(run_id)
    for stage in stages.STAGES:
        if info["stages"][str(stage.number)]["status"] != stages.DONE:
            return stage.number
    return None


def run_chain(run_id: str, start: int, end: int = stages.LAST_STAGE) -> dict:
    _write(run_id, status=RUNNING, start=start, end=end, pid=os.getpid(), started_at=state.now(),
           current=None, done=[], message="実行中")
    runs.log_event(run_id, "続けて実行の開始", stage=f"{start}-{end}")
    done: list[int] = []
    try:
        for number in range(start, end + 1):
            stage = stages.BY_NUMBER[number]
            if stage.module is None:  # 設定のように操作だけの段階
                continue
            reason = runs.blocking_reason(run_id, number)
            if reason:
                message = f"段階{number}の前で停止: {reason}"
                if number == stages.CAR and "凍結" in reason:
                    message += "。段階5でバランス診断を確認し、「マッチングを確定」してから「続きから実行」してください"
                return _write(run_id, status=STOPPED, current=None, done=done, message=message)
            _write(run_id, current=number, done=done, message=f"段階{number}（{stage.title}）を実行中")
            result = stage_runner.execute(run_id, number)
            if result.status != stages.DONE:
                return _write(run_id, status=STOPPED, current=None, done=done,
                              message=f"段階{number}が「{result.status}」で止まりました: {result.message}"
                                      "。確認・修正してから「続きから実行」してください")
            done.append(number)
        return _write(run_id, status=FINISHED, current=None, done=done, message=f"段階{start}〜{end}を実行しました")
    except Exception as error:  # noqa: BLE001  状態に残して呼び出し元へ返す
        return _write(run_id, status=FAILED, current=None, done=done,
                      message=f"{type(error).__name__}: {error}", traceback=traceback.format_exc())
    finally:
        runs.log_event(run_id, "続けて実行の終了", detail=(read_chain(run_id) or {}).get("message", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="段階を続けて実行する")
    parser.add_argument("--run", required=True)
    parser.add_argument("--from", dest="start", type=int, required=True)
    parser.add_argument("--to", dest="end", type=int, default=stages.LAST_STAGE)
    args = parser.parse_args()
    result = run_chain(args.run, args.start, args.end)
    print(result["status"], result["message"])
    return 0 if result["status"] in (FINISHED, STOPPED) else 1


if __name__ == "__main__":
    sys.exit(main())
