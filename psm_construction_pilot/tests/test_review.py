"""段階4: 手動入力の優先順位、取り消し、完全ケースの判定。"""
from __future__ import annotations

import pandas as pd
import pytest

from core import ai_count, covariates, overrides, review, runs, stages


@pytest.fixture
def prepared(sandbox):
    """段階1〜3の出力を小さな合成データで用意する（1003 は pbr が欠損、1004 は AI判定不能）。"""
    run_id = runs.current_run()
    codes = ["1001", "1002", "1003", "1004"]
    pd.DataFrame({"code": codes, "name": codes, "announce_date": "2026-05-14", "industry33": "建設業",
                  "adopted_pdf": [f"{c}.pdf" for c in codes], "included": "1"}).to_csv(
        runs.stage_dir(run_id, 1) / "targets.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame({"code": codes, "name": codes, "file": "", "text_status": ["抽出可"] * 3 + ["画像PDF"],
                  "ai_count": ["2", "0", "1", ""], "ai_flag": ["1", "0", "1", ""],
                  "group": [ai_count.TREATED, ai_count.CONTROL, ai_count.TREATED, ai_count.UNDETERMINED]}).to_csv(
        runs.stage_dir(run_id, 2) / "ai_flags.csv", index=False, encoding="utf-8-sig")
    rows = []
    for code in codes:
        for variable in covariates.VARIABLES:
            missing = (code == "1003" and variable == "pbr") or code == "1004"
            rows.append({"code": code, "name": code, "variable": variable, "value": "" if missing else "0.5",
                         "status": covariates.MISSING if missing else covariates.OK,
                         "reason_code": covariates.R_PRICE if missing else "", "detail": ""})
    pd.DataFrame(rows).to_csv(runs.stage_dir(run_id, 3) / "cell_status.csv", index=False, encoding="utf-8-sig")
    return run_id


def test_complete_case_excludes_missing(prepared):
    dataset, _ = review.build_dataset(prepared)
    included = dataset.set_index("code")["in_analysis"]
    assert included.to_dict() == {"1001": 1, "1002": 1, "1003": 0, "1004": 0}
    assert "pbr" in dataset.set_index("code").loc["1003", "exclusion_reason"]


def test_manual_value_takes_priority_and_revoke_restores(prepared):
    review.set_value(prepared, "1003", "pbr", 1.25, "決算短信p.1", "株価を手で確認")
    dataset, cells = review.build_dataset(prepared)
    row = dataset.set_index("code").loc["1003"]
    assert row["in_analysis"] == 1 and row["pbr"] == pytest.approx(1.25) and row["pbr_source"] == "manual"
    cell = cells[(cells["code"] == "1003") & (cells["variable"] == "pbr")].iloc[0]
    assert cell["status"] == covariates.MANUAL and cell["original_value"] == ""

    override_id = overrides.active("value").iloc[0]["override_id"]
    review.revoke(override_id, "誤入力")
    dataset, _ = review.build_dataset(prepared)
    assert dataset.set_index("code").loc["1003", "in_analysis"] == 0


def test_manual_value_requires_source_and_reason(prepared):
    with pytest.raises(ValueError):
        review.set_value(prepared, "1003", "pbr", 1.0, "", "理由")


def test_ai_flag_override_and_exclusion(prepared):
    review.set_ai_flag(prepared, "1002", 1, "p.5 でAI活用を確認")
    review.exclude("1001", "テスト")
    dataset, _ = review.build_dataset(prepared)
    rows = dataset.set_index("code")
    assert rows.loc["1002", "ai_flag"] == 1 and rows.loc["1002", "ai_flag_source"] == "manual"
    assert rows.loc["1001", "in_analysis"] == 0


def test_manual_value_rebuilds_dataset_without_rerunning_stage(prepared):
    runs.set_status(prepared, stages.COVARIATES, stages.REVIEW)
    runs.set_status(prepared, stages.MATCHING, stages.DONE)
    review.set_value(prepared, "1003", "pbr", 1.25, "決算短信p.1", "株価を手で確認")
    saved = pd.read_csv(runs.stage_dir(prepared, stages.COVARIATES) / "analysis_dataset.csv", dtype={"code": str})
    assert saved.set_index("code").loc["1003", "in_analysis"] == 1  # その場で作り直されている
    assert runs.stage_state(prepared, stages.COVARIATES)["status"] == stages.DONE
    assert runs.stage_state(prepared, stages.MATCHING)["status"] == stages.STALE


def test_exclude_many_and_include_many(prepared):
    runs.set_status(prepared, stages.COVARIATES, stages.REVIEW)
    runs.set_status(prepared, stages.MATCHING, stages.DONE)
    review.exclude("1001", "先に1社だけ除外")
    review.exclude_many(["1001", "1002", "1002"], "要確認の会社をまとめて除外")
    assert review.excluded_codes() == ["1001", "1002"]
    logged = overrides.active("exclude")
    assert len(logged[logged["source"] == "段階3（一括）"]) == 1  # 除外中の1001と重複は飛ばす
    rows = review.build_dataset(prepared)[0].set_index("code")
    assert rows.loc["1001", "in_analysis"] == 0 and rows.loc["1002", "in_analysis"] == 0
    assert runs.stage_state(prepared, stages.MATCHING)["status"] == stages.STALE

    review.include_many(["1001", "1002"], "やはり含める")
    assert review.excluded_codes() == []
    rows = review.build_dataset(prepared)[0].set_index("code")
    assert rows.loc["1001", "in_analysis"] == 1 and rows.loc["1002", "in_analysis"] == 1


def test_exclude_many_requires_reason_and_targets(prepared):
    with pytest.raises(ValueError):
        review.exclude_many(["1001"], " ")
    review.exclude_many(["1001"], "理由")
    with pytest.raises(ValueError):
        review.exclude_many(["1001"], "理由")  # すでに除外中だけ
    with pytest.raises(ValueError):
        review.include_many(["1002"], "理由")  # 除外中ではない
