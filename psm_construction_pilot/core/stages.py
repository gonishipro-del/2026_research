"""段階の定義と状態の名前。"""
from __future__ import annotations

from dataclasses import dataclass

NOT_RUN = "未実行"
RUNNING = "実行中"
DONE = "完了"
REVIEW = "要確認"
STALE = "要再実行"
FAILED = "失敗"

STATUS_ICONS = {NOT_RUN: "⚪", RUNNING: "🔄", DONE: "✅", REVIEW: "⚠️", STALE: "🔁", FAILED: "❌"}


@dataclass(frozen=True)
class Stage:
    number: int
    key: str  # 出力フォルダ名に使う
    title: str
    module: str | None  # 実行処理のモジュール（設定・凍結のようにボタン操作だけの段階は None）


# 段階の番号。ほかのモジュールは数字を直接書かず、これらの名前で参照する
SETTINGS, INGEST, AI_COUNT, COVARIATES, MATCHING, FREEZE, CAR, ATT, EVALUATE = range(9)

STAGES = [
    Stage(SETTINGS, "settings", "設定", None),
    Stage(INGEST, "ingest", "対象企業の確定と決算資料の取り込み", "core.ingest"),
    Stage(AI_COUNT, "ai_count", "AI言及のカウント", "core.ai_count"),
    Stage(COVARIATES, "covariates", "共変量の収集と欠損の確認", "core.covariate_stage"),
    Stage(MATCHING, "matching", "傾向スコアの推定とマッチング", "core.matcher"),
    Stage(FREEZE, "freeze", "バランス診断とマッチングの確定（凍結）", "core.balance"),
    Stage(CAR, "car", "CARの計算", "core.car"),
    Stage(ATT, "att", "ATTの推定", "core.att"),
    Stage(EVALUATE, "evaluate", "評価（頑健性・感度）", "core.evaluate"),
]
BY_NUMBER = {stage.number: stage for stage in STAGES}
LAST_STAGE = STAGES[-1].number

# 入力ファイルを差し替えたとき、最初に影響を受ける段階
INPUT_FIRST_STAGE = {"master": INGEST, "factors": CAR}
# 結果を見た後の上流変更を「結果閲覧後の変更」として記録する対象の段階
RESULT_STAGES = (ATT, EVALUATE)


def stage_dirname(number: int) -> str:
    return f"stage{number}_{BY_NUMBER[number].key}"
