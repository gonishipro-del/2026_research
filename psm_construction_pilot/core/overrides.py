"""手動入力ログ（state/manual_overrides.csv、追記専用）。

元データは上書きせず、手動入力はすべてこのログに1行ずつ追記する。取り消しも
「取り消し」の行を追記して表す。生成時は、取り消されていない最新の行を元データより優先する。

kind の種類:
  hit_label   AIキーワードのヒットの分類（target=ヒットID、new_value=有効/誤検出）
  value       共変量の値（target=変数名）
  ai_flag     ai_flag の上書き（new_value=0/1）
  exclude     分析からの除外（new_value=1）
  add_company 企業の手動追加（new_value=JSON: 企業名・決算発表日・PDFのパス）
  revoke      取り消し（revokes=取り消す override_id）
"""
from __future__ import annotations

import uuid

import pandas as pd

import config
from core import state

COLUMNS = ["override_id", "timestamp", "run_id", "kind", "code", "target",
           "old_value", "new_value", "source", "reason", "revokes"]
KINDS = ("hit_label", "value", "ai_flag", "exclude", "add_company", "revoke")
HIT_VALID = "有効"
HIT_FALSE = "誤検出"


def append(run_id: str, kind: str, code: str, target: str = "", *, old_value: object = "",
           new_value: object = "", source: str = "", reason: str = "", revokes: str = "") -> str:
    if kind not in KINDS:
        raise ValueError(f"未知の種類: {kind}")
    override_id = uuid.uuid4().hex[:12]
    state.append_csv(config.MANUAL_OVERRIDES_CSV, COLUMNS, {
        "override_id": override_id, "timestamp": state.now(), "run_id": run_id, "kind": kind,
        "code": code, "target": target, "old_value": old_value, "new_value": new_value,
        "source": source, "reason": reason, "revokes": revokes,
    })
    return override_id


def revoke(run_id: str, override_id: str, reason: str) -> str:
    rows = load_all()
    hit = rows[rows["override_id"] == override_id]
    if hit.empty:
        raise KeyError(f"手動入力 {override_id} がありません")
    row = hit.iloc[0]
    return append(run_id, "revoke", row["code"], row["target"], old_value=row["new_value"],
                  reason=reason, revokes=override_id)


def load_all() -> pd.DataFrame:
    if not config.MANUAL_OVERRIDES_CSV.exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(config.MANUAL_OVERRIDES_CSV, dtype=str, encoding="utf-8-sig",
                       keep_default_na=False)


def active(kind: str) -> pd.DataFrame:
    """取り消されていない手動入力のうち、(code, target) ごとに最新の1行。ログの順＝時系列。"""
    rows = load_all()
    revoked = set(rows.loc[rows["kind"] == "revoke", "revokes"])
    live = rows[(rows["kind"] == kind) & ~rows["override_id"].isin(revoked)]
    return live.drop_duplicates(subset=["code", "target"], keep="last").reset_index(drop=True)
