"""段階を続けて実行する処理：止まる条件と、続きから実行する段階。"""
from __future__ import annotations

from core import pipeline, runs, stage_runner, stages


def _fake_execute(outcomes: dict[int, str]):
    """段階の処理の代わりに、決めた状態で終わらせる。"""
    def execute(run_id: str, number: int, *, echo: bool = False):
        status = outcomes.get(number, stages.DONE)
        runs.set_status(run_id, number, status, "テスト")
        return stage_runner.StageResult(status, "テスト")
    return execute


def test_chain_stops_at_review(sandbox, monkeypatch):
    run_id = runs.current_run()
    runs.set_status(run_id, stages.SETTINGS, stages.DONE)
    monkeypatch.setattr(stage_runner, "execute", _fake_execute({stages.COVARIATES: stages.REVIEW}))
    result = pipeline.run_chain(run_id, stages.INGEST)
    assert result["status"] == pipeline.STOPPED and result["done"] == [stages.INGEST, stages.AI_COUNT]
    assert "要確認" in result["message"]
    assert pipeline.first_unfinished(run_id) == stages.COVARIATES


def test_chain_stops_before_car_until_frozen(sandbox, monkeypatch):
    run_id = runs.current_run()
    runs.set_status(run_id, stages.SETTINGS, stages.DONE)
    monkeypatch.setattr(stage_runner, "execute", _fake_execute({}))
    result = pipeline.run_chain(run_id, stages.INGEST)
    assert result["status"] == pipeline.STOPPED and result["done"][-1] == stages.FREEZE
    assert "凍結" in result["message"]

    from core import state
    state.update(lambda current: current["runs"][run_id].update(frozen={"at": "テスト"}))
    result = pipeline.run_chain(run_id, pipeline.first_unfinished(run_id))
    assert result["status"] == pipeline.FINISHED and result["done"] == [stages.CAR, stages.ATT, stages.EVALUATE]
    assert pipeline.first_unfinished(run_id) is None
