"""上流の変更（設定・入力ファイル・手動入力・ヒット分類）を記録し、下流を「要再実行」にする。

段階7・8の結果を一度でも表示した run で上流を変えた場合は「結果閲覧後の変更」として
新しい run を作り、元の run は残す。
"""
from __future__ import annotations

from core import runs, state


def begin_upstream_change(what: str, first_stage: int) -> tuple[str, bool]:
    """変更を適用する run を返す。(run_id, 結果閲覧後の変更か)。

    呼び出し側はこの run に変更を書き込んだ後、finish_upstream_change を呼ぶ。
    """
    run_id = runs.current_run()
    if not runs.results_viewed(run_id):
        return run_id, False
    new_run = runs.create_run(parent=run_id, reason=f"結果閲覧後の変更: {what}")

    def mutate(current: dict) -> None:
        current["runs"][new_run]["changed_after_view"].append(
            {"at": state.now(), "what": what, "from_run": run_id})
        current["runs"][new_run]["results_viewed"] = {}
    state.update(mutate)
    runs.log_event(new_run, "結果閲覧後の変更", stage=first_stage, detail=what, after_results_view=True)
    return new_run, True


def finish_upstream_change(run_id: str, what: str, first_stage: int) -> list[int]:
    return runs.mark_stale_from(run_id, first_stage, f"上流の変更: {what}")
