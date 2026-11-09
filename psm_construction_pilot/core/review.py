"""段階3（後半）: 欠損の確認と手動補完、分析対象（完全ケース）の確定。

段階2（ai_flag）と段階3（共変量）の結果に、手動入力（state/manual_overrides.csv）を重ねて
分析用データセットを作る。手動入力は元データを上書きせず、生成時に優先して反映する
（source_type=manual）。欠損が残る企業は完全ケースとして除外する。

手動入力の操作（値の入力、ai_flag の上書き、除外・まとめて除外、企業の追加、取り消し）もここに置く。
操作をすると分析用データセットをその場で作り直し、段階4（マッチング）以降を「要再実行」にする
（企業の追加は段階1から）。

出力（output/run_*/stage3_covariates/）:
  analysis_dataset.csv          企業ごとの ai_flag・共変量・出典の種類・分析に含めるか
  cells_final.csv               企業×変数の状態（手動入力を反映）
  excluded.csv                  分析から外した企業と理由
  manual_overrides_applied.csv  この確定で反映した手動入力（ログのスナップショット）
  dataset_summary.json          件数
"""
from __future__ import annotations

import json
import math
from datetime import date

import pandas as pd

import config
from core import ai_count, changes, covariates, ingest, overrides, runs, stage_runner, stages
from core.covariates import MANUAL, MISSING, R_IMAGE, R_MANUAL, REVIEW, VARIABLES
from core.progress import Progress

AI_FLAG = "ai_flag"
# 手動入力の種類ごとに、最初に「要再実行」になる段階（分析用データセットはその場で作り直す）
FIRST_STAGE_BY_KIND = {"value": stages.MATCHING, "ai_flag": stages.MATCHING, "exclude": stages.MATCHING,
                       "add_company": stages.INGEST, "hit_label": stages.MATCHING}


# --- 手動入力を重ねたデータ ------------------------------------------------------

def _ai_cells(run_id: str) -> pd.DataFrame:
    """ai_flag を他の変数と同じ形（企業×変数の状態）にする。"""
    flags = ai_count.load_flags(run_id)
    rows = []
    for _, row in flags.iterrows():
        if row["group"] == ai_count.UNDETERMINED:
            rows.append({"code": row["code"], "name": row["name"], "variable": AI_FLAG, "value": "",
                         "status": MISSING, "reason_code": R_IMAGE, "detail": f"AI判定不能: {row['text_status']}"})
        else:
            rows.append({"code": row["code"], "name": row["name"], "variable": AI_FLAG, "value": row["ai_flag"],
                         "status": covariates.OK, "reason_code": "", "detail": f"有効ヒット{row['ai_count']}件"})
    return pd.DataFrame(rows)


def current_cells(run_id: str) -> pd.DataFrame:
    """段階2・3の結果に手動入力を重ねた、企業×変数（ai_flag + 共変量）の状態。"""
    cells = pd.concat([_ai_cells(run_id), covariates.load_cells(run_id)], ignore_index=True)
    cells["source_type"] = "auto"
    cells["original_value"] = cells["value"]
    cells["override_id"] = ""
    manual = pd.concat([overrides.active("value"), overrides.active("ai_flag").assign(target=AI_FLAG)])
    for _, entry in manual.iterrows():
        mask = (cells["code"] == entry["code"]) & (cells["variable"] == entry["target"])
        if not mask.any():
            continue
        cells.loc[mask, ["value", "status", "reason_code", "source_type", "override_id"]] = [
            entry["new_value"], MANUAL, R_MANUAL, "manual", entry["override_id"]]
        cells.loc[mask, "detail"] = f"出典: {entry['source']}／理由: {entry['reason']}（{entry['timestamp']}）"
    return cells


def build_dataset(run_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(分析用データセット, 企業×変数の状態)。完全ケースかどうかと除外理由も付ける。"""
    cells = current_cells(run_id)
    targets = ingest.load_targets(run_id)
    included = targets[targets["included"] == "1"][["code", "name", "announce_date", "industry33", "adopted_pdf"]]
    wide = cells.pivot(index="code", columns="variable", values="value")
    sources = cells.pivot(index="code", columns="variable", values="source_type")
    statuses = cells.pivot(index="code", columns="variable", values="status")
    dataset = included.set_index("code").join(wide).join(sources.add_suffix("_source")).join(
        statuses.add_suffix("_status"))

    # 段階3の出力にある共変量（過去の run は、今と違う共変量で作られていることがある）
    variables = [v for v in cells["variable"].unique() if v != AI_FLAG]
    excluded = overrides.active("exclude").set_index("code")
    reasons, review_counts = [], []
    for code, row in dataset.iterrows():
        reason = []
        if code in excluded.index and excluded.loc[code, "new_value"] == "1":
            reason.append(f"手動で除外: {excluded.loc[code, 'reason']}")
        # 段階2・3に行がない（状態が NaN）場合も欠損とみなす
        missing = [variable for variable in [AI_FLAG, *variables]
                   if pd.isna(row.get(f"{variable}_status")) or row.get(f"{variable}_status") == MISSING]
        if missing:
            reason.append("欠損: " + ", ".join(missing))
        reasons.append("；".join(reason))
        review_counts.append(sum(row.get(f"{variable}_status") == REVIEW for variable in [AI_FLAG, *variables]))
    dataset["exclusion_reason"] = reasons
    dataset["in_analysis"] = (dataset["exclusion_reason"] == "").astype(int)
    dataset["review_cells"] = review_counts
    for variable in variables:
        dataset[variable] = pd.to_numeric(dataset[variable], errors="coerce")
    dataset[AI_FLAG] = pd.to_numeric(dataset[AI_FLAG], errors="coerce").astype("Int64")
    return dataset.reset_index(), cells


def finalize(run_id: str, progress: Progress | None = None) -> stage_runner.StageResult:
    """分析用データセットを作って保存する。段階3の収集の直後と、手動入力のたびに呼ぶ。"""
    dataset, cells = build_dataset(run_id)
    out = runs.stage_dir(run_id, stages.COVARIATES)
    dataset.to_csv(out / "analysis_dataset.csv", index=False, encoding="utf-8-sig")
    cells.to_csv(out / "cells_final.csv", index=False, encoding="utf-8-sig")
    dataset[dataset["in_analysis"] == 0][["code", "name", "exclusion_reason"]].to_csv(
        out / "excluded.csv", index=False, encoding="utf-8-sig")
    overrides.load_all().to_csv(out / "manual_overrides_applied.csv", index=False, encoding="utf-8-sig")

    analysed = dataset[dataset["in_analysis"] == 1]
    unresolved = int(analysed["review_cells"].sum())
    summary = {
        "companies": int(len(dataset)),
        "in_analysis": int(len(analysed)),
        "excluded": int((dataset["in_analysis"] == 0).sum()),
        "treated": int((analysed[AI_FLAG] == 1).sum()),
        "control": int((analysed[AI_FLAG] == 0).sum()),
        "manual_cells": int((cells["source_type"] == "manual").sum()),
        "unresolved_review_cells": unresolved,
        "exclusion_reasons": dataset.loc[dataset["in_analysis"] == 0, "exclusion_reason"].value_counts().to_dict(),
    }
    (out / "dataset_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress is not None:
        progress.log(json.dumps(summary, ensure_ascii=False))
    message = (f"分析対象{summary['in_analysis']}社（処置{summary['treated']}・対照{summary['control']}）／"
               f"除外{summary['excluded']}社／手動{summary['manual_cells']}セル")
    if unresolved:
        return stage_runner.StageResult(
            stages.REVIEW, message + f"／未確認の「要確認」{unresolved}セル。値を確認・修正するか、このまま使うことを確認してください")
    return stage_runner.StageResult(stages.DONE, message)


def confirm_review_cells(run_id: str, note: str) -> None:
    """「要確認」の値をそのまま使うことを確認して先へ進める。"""
    entry = runs.stage_state(run_id, stages.COVARIATES)
    runs.set_status(run_id, stages.COVARIATES, stages.DONE, entry["message"] + "（要確認の値をそのまま使うことを確認済み）")
    runs.log_event(run_id, "要確認の値を確認", stage=stages.COVARIATES, detail=note)


def refresh(run_id: str) -> None:
    """手動入力の後に、分析用データセットと段階3の状態を作り直す（段階3を一度実行済みの場合）。"""
    entry = runs.stage_state(run_id, stages.COVARIATES)
    if entry["status"] not in (stages.DONE, stages.REVIEW):
        return
    if not (runs.stage_dir(run_id, stages.COVARIATES) / "cell_status.csv").exists():
        return
    result = finalize(run_id)
    runs.set_status(run_id, stages.COVARIATES, result.status, result.message)


# --- 手動入力の操作 ------------------------------------------------------------

def _record(kind: str, code: str, what: str, **fields: object) -> str:
    first_stage = FIRST_STAGE_BY_KIND[kind]
    run_id, _ = changes.begin_upstream_change(what, first_stage)
    overrides.append(run_id, kind, code, **fields)
    if first_stage > stages.COVARIATES:
        refresh(run_id)
    changes.finish_upstream_change(run_id, what, first_stage)
    return run_id


def _require(**texts: str) -> None:
    empty = [name for name, text in texts.items() if not str(text).strip()]
    if empty:
        raise ValueError(f"{'・'.join(empty)} は必須です")


def set_value(run_id: str, code: str, variable: str, value: float, source: str, reason: str) -> str:
    """欠損・要確認セルなどに値を入れる。出典（例: 決算短信p.3 連結BS）と理由が必須。"""
    _require(出典=source, 理由=reason)
    if variable not in VARIABLES:
        raise ValueError(f"共変量ではありません: {variable}")
    if not math.isfinite(value):
        raise ValueError("値が数値ではありません")
    cells = current_cells(run_id)
    old = cells[(cells["code"] == code) & (cells["variable"] == variable)]["original_value"]
    return _record("value", code, f"手動補完 {code} {variable}", target=variable,
                   old_value=old.iloc[0] if len(old) else "", new_value=repr(float(value)),
                   source=source.strip(), reason=reason.strip())


def set_ai_flag(run_id: str, code: str, flag: int, basis: str) -> str:
    _require(根拠=basis)
    flags = ai_count.load_flags(run_id).set_index("code")
    old = flags.loc[code, "ai_flag"] if code in flags.index else ""
    return _record("ai_flag", code, f"ai_flag の手動上書き {code}", target=AI_FLAG, old_value=old,
                   new_value=str(int(flag)), source="段階3", reason=basis.strip())


def exclude(code: str, reason: str) -> str:
    _require(理由=reason)
    return _record("exclude", code, f"分析から除外 {code}", target="exclude", new_value="1",
                   source="段階3", reason=reason.strip())


def exclude_many(codes: list[str], reason: str) -> str:
    """複数の会社をまとめて分析から除外する（要確認がある会社の一括除外など）。

    会社ごとに除外の行を手動入力ログに追記するが、run の切り替え・分析用データセットの作り直し・
    「要再実行」は1回だけ行う。すでに除外中の会社は飛ばす。
    """
    _require(理由=reason)
    excluded = set(excluded_codes())
    targets = [code for code in dict.fromkeys(codes) if code not in excluded]
    if not targets:
        raise ValueError("除外する会社がありません（すでに除外中か、選ばれていません）")
    what = f"分析からまとめて除外 {len(targets)}社"
    run_id, _ = changes.begin_upstream_change(what, FIRST_STAGE_BY_KIND["exclude"])
    for code in targets:
        overrides.append(run_id, "exclude", code, target="exclude", new_value="1", source="段階3（一括）",
                         reason=reason.strip())
    refresh(run_id)
    changes.finish_upstream_change(run_id, what, FIRST_STAGE_BY_KIND["exclude"])
    return run_id


def include_many(codes: list[str], reason: str) -> str:
    """除外中の会社をまとめて分析に含め直す（除外の手動入力を取り消す）。"""
    _require(理由=reason)
    live = overrides.active("exclude")
    live = live[(live["new_value"] == "1") & live["code"].isin(codes)]
    if live.empty:
        raise ValueError("含め直す会社がありません（除外中の会社が選ばれていません）")
    what = f"除外のまとめての取り消し {len(live)}社"
    run_id, _ = changes.begin_upstream_change(what, FIRST_STAGE_BY_KIND["exclude"])
    for override_id in live["override_id"]:
        overrides.revoke(run_id, override_id, reason.strip())
    refresh(run_id)
    changes.finish_upstream_change(run_id, what, FIRST_STAGE_BY_KIND["exclude"])
    return run_id


def excluded_codes() -> list[str]:
    """手動で除外中の会社（証券コード）。"""
    live = overrides.active("exclude")
    return sorted(live.loc[live["new_value"] == "1", "code"].tolist())


def add_company(code: str, name: str, announce_date: date, pdf_bytes: bytes, reason: str) -> str:
    """企業を手動で追加する。PDF は data/manual/pdfs/ に命名規則どおりの名前で保存する。"""
    _require(証券コード=code, 企業名=name, 理由=reason)
    code = code.strip().upper()
    filename = f"{code}_{name.strip()}_{announce_date:%Y%m%d}.pdf"
    path = config.MANUAL_PDF_DIR / filename
    if path.exists():
        raise FileExistsError(f"同名のPDFがすでにあります: {path}")
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("PDFファイルではありません")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)
    detail = {"name": name.strip(), "announce_date": announce_date.isoformat(), "pdf": filename}
    return _record("add_company", code, f"企業の手動追加 {code} {name}", target="company",
                   new_value=json.dumps(detail, ensure_ascii=False), source="段階3", reason=reason.strip())


def revoke(override_id: str, reason: str) -> str:
    """手動入力の取り消し（取り消しの行を追記する）。"""
    _require(理由=reason)
    rows = overrides.load_all()
    kind = rows.loc[rows["override_id"] == override_id, "kind"].iloc[0]
    what = f"手動入力の取り消し {override_id}（{kind}）"
    first_stage = FIRST_STAGE_BY_KIND.get(kind, stages.MATCHING)
    run_id, _ = changes.begin_upstream_change(what, first_stage)
    overrides.revoke(run_id, override_id, reason.strip())
    if first_stage > stages.COVARIATES:
        refresh(run_id)
    changes.finish_upstream_change(run_id, what, first_stage)
    return run_id
