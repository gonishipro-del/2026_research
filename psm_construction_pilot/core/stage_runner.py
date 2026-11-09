"""段階の実行（状態の更新・ログ・例外の記録をまとめて行う）。

各段階のモジュールは run(run_id, progress) -> StageResult を持つ。
アプリのバックグラウンド実行も、各モジュールの CLI も、ここの execute を通す。

単体実行: python -m core.stage_runner --stage 1 [--run run_YYYYMMDD_HHMMSS]
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import traceback
from dataclasses import dataclass

import config  # noqa: F401  （sys.path の設定）
from core import runs, stages
from core.progress import Progress


@dataclass
class StageResult:
    status: str  # stages.DONE か stages.REVIEW
    message: str = ""


def execute(run_id: str, number: int, *, echo: bool = False) -> StageResult:
    stage = stages.BY_NUMBER[number]
    if stage.module is None:
        raise ValueError(f"段階{number}は実行処理を持ちません（アプリで操作する段階）")

    progress = Progress(run_id, number, echo=echo)
    progress.reset()
    runs.set_status(run_id, number, stages.RUNNING, "実行中", pid=os.getpid())
    # 一度でも実行した下流は、この段階の出力が変わるので「要再実行」にする
    runs.mark_stale_from(run_id, number + 1, f"段階{number}を再実行")
    runs.log_event(run_id, "段階の実行開始", stage=number)
    progress.log(f"段階{number}（{stage.title}）を開始 run={run_id}")

    try:
        module = importlib.import_module(stage.module)
        result: StageResult = module.run(run_id, progress)
    except Exception as error:  # noqa: BLE001  失敗は状態とログに残して呼び出し元へ返す
        progress.log("[失敗]\n" + traceback.format_exc())
        message = f"{type(error).__name__}: {error}"
        runs.set_status(run_id, number, stages.FAILED, message)
        runs.log_event(run_id, "段階の失敗", stage=number, detail=message)
        return StageResult(stages.FAILED, message)

    runs.set_status(run_id, number, result.status, result.message)
    runs.log_event(run_id, "段階の終了", stage=number, detail=f"{result.status} {result.message}")
    progress.log(f"終了: {result.status} {result.message}")
    return result


def cli(number: int, argv: list[str] | None = None) -> int:
    """各モジュールの CLI 共通部分。現在の run（--run で指定可）で段階を実行する。"""
    parser = argparse.ArgumentParser(description=f"段階{number}: {stages.BY_NUMBER[number].title}")
    parser.add_argument("--run", help="run の ID（省略時は現在の run）")
    args = parser.parse_args(argv)
    run_id = args.run or runs.current_run()
    reason = runs.blocking_reason(run_id, number)
    if reason:
        print(f"実行できません: {reason}", file=sys.stderr)
        return 2
    result = execute(run_id, number, echo=True)
    print(f"{result.status} {result.message}")
    return 0 if result.status != stages.FAILED else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="段階を実行する")
    parser.add_argument("--stage", type=int, required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    result = execute(args.run, args.stage)
    return 0 if result.status != stages.FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
