"""段階6: CAR を計算できない企業の一覧と、確認して先へ進める操作。"""
from __future__ import annotations

import pandas as pd
import pytest

from core import car, runs, stages


def test_uncomputed_and_confirm(sandbox):
    run_id = runs.current_run()
    pd.DataFrame([
        {"code": "1001", "name": "A", "car_m1_p1": 0.01, "warnings": ""},
        {"code": "368A", "name": "B", "car_m1_p1": "", "warnings": "推定期間の履歴が不足"},
        {"code": "1003", "name": "C", "car_m1_p1": "", "warnings": ""},
    ]).to_csv(runs.stage_dir(run_id, stages.CAR) / "car.csv", index=False, encoding="utf-8-sig")
    missing = car.uncomputed(run_id, "m1_p1")
    assert missing["code"].tolist() == ["368A", "1003"]
    assert missing["reason"].tolist() == ["推定期間の履歴が不足", "CAR計算失敗（errors.csv）"]

    with pytest.raises(ValueError):
        car.confirm_uncomputed(run_id, "メモ")  # 要確認ではない
    runs.set_status(run_id, stages.CAR, stages.REVIEW, "警告あり")
    with pytest.raises(ValueError):
        car.confirm_uncomputed(run_id, " ")
    car.confirm_uncomputed(run_id, "新規上場のため")
    assert runs.stage_state(run_id, stages.CAR)["status"] == stages.DONE
