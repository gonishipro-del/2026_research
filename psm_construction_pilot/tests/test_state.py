"""段階の状態管理、手動入力の優先順位、結果閲覧後の変更。"""
from __future__ import annotations

from core import overrides, runs, settings, stages


def _complete_through(run_id: str, last: int) -> None:
    for number in range(last + 1):
        runs.set_status(run_id, number, stages.DONE)


def test_next_stage_blocked_until_previous_done(sandbox):
    run_id = runs.current_run()
    assert "段階0" in runs.blocking_reason(run_id, 1)
    _complete_through(run_id, 0)
    assert runs.blocking_reason(run_id, 1) == ""
    assert runs.blocking_reason(run_id, 2) != ""


def test_car_requires_freeze(sandbox):
    run_id = runs.current_run()
    _complete_through(run_id, stages.FREEZE)
    assert "凍結" in runs.blocking_reason(run_id, stages.CAR)


def test_upstream_change_marks_downstream_stale(sandbox):
    run_id = runs.current_run()
    _complete_through(run_id, 3)
    marked = runs.mark_stale_from(run_id, 2, "テスト")
    assert marked == [2, 3]
    assert runs.stage_state(run_id, 1)["status"] == stages.DONE
    assert runs.stage_state(run_id, 4)["status"] == stages.NOT_RUN  # 未実行のものはそのまま


def test_settings_change_after_results_view_creates_new_run(sandbox):
    run_id = runs.current_run()
    settings.save(runs.run_config(run_id))
    _complete_through(run_id, stages.ATT)
    runs.mark_results_viewed(run_id, stages.ATT)

    values = {**runs.run_config(run_id), "caliper_sd": 0.3}
    new_run, changed, marked = settings.save(values, "テスト")
    assert new_run != run_id and changed == ["caliper_sd"]
    assert runs.run_config(run_id)["caliper_sd"] == 0.2  # 元の run は残る
    assert runs.run_config(new_run)["caliper_sd"] == 0.3
    assert runs.stage_state(new_run, stages.COVARIATES)["status"] == stages.DONE
    assert runs.stage_state(new_run, stages.MATCHING)["status"] == stages.STALE
    assert runs.run_info(new_run)["changed_after_view"]


def test_overrides_latest_wins_and_revoke_restores(sandbox):
    run_id = runs.current_run()
    first = overrides.append(run_id, "value", "1001", "pbr", new_value="1.0", source="p.3", reason="欠損")
    second = overrides.append(run_id, "value", "1001", "pbr", new_value="1.2", source="p.3", reason="訂正")
    assert overrides.active("value").iloc[0]["new_value"] == "1.2"
    overrides.revoke(run_id, second, "誤入力")
    assert overrides.active("value").iloc[0]["override_id"] == first
    overrides.revoke(run_id, first, "不要")
    assert overrides.active("value").empty
