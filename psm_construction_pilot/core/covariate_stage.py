"""段階3: 共変量の収集と欠損の確認。

前半で共変量を集め（core.covariates）、後半で手動入力を重ねて分析対象を確定する（core.review）。
「要確認」の値が残る場合は「要確認」で止まり、確認するまで段階4（マッチング）へ進めない。

単体実行: python -m core.covariate_stage [--run RUN_ID]
"""
from __future__ import annotations

from core import covariates, review, stage_runner, stages
from core.progress import Progress


def run(run_id: str, progress: Progress) -> stage_runner.StageResult:
    collected = covariates.run(run_id, progress)
    finalized = review.finalize(run_id, progress)
    return stage_runner.StageResult(finalized.status, f"{collected.message}／{finalized.message}")


if __name__ == "__main__":
    raise SystemExit(stage_runner.cli(stages.COVARIATES))
