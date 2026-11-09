"""段階2: AI言及のカウント。

採用した決算短信の全ページから AI_KEYWORDS を数え、ヒットごとに前後の文脈とページ番号を保存する。
キーワードの照合は既存の psm_dataset.ai_terms と同じ規則（英字は単語境界つきで RAIL・MAIN 等を除外、
長い語を優先して「生成AI」を「AI」と二重計上しない、改行で分断された日本語を結合）。

ヒットはアプリで「有効/誤検出」に手動分類でき（manual_overrides.csv の hit_label）、
誤検出を除いたヒット数が閾値以上なら ai_flag=1。テキストが取れないPDFは判定不能。

出力（output/run_*/stage2_ai_count/）:
  hits.csv       ヒット一覧（ヒットID、ページ、キーワード、前後の文脈）
  extraction.csv 企業ごとのテキスト抽出結果（判定不能の理由）
  ai_flags.csv   企業ごとの件数と ai_flag（手動分類を反映）
  summary.json   処置群・対照群・判定不能の件数

単体実行: python -m core.ai_count [--run RUN_ID]
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from core import ingest, overrides, runs, stage_runner, stages
from core.pdf_text import page_text
from core.progress import Progress
from psm_dataset.ai_terms import build_pattern, prepare_text

TREATED, CONTROL, UNDETERMINED = "処置群", "対照群", "判定不能"
HIT_COLUMNS = ["hit_id", "code", "name", "file", "page", "offset", "keyword", "before", "match", "after"]


def find_hits(pages: list[list[str]], keywords: list[str], context: int) -> list[dict]:
    """ページごとにヒットを探す。offset は検索用に整形したページ本文での位置。"""
    pattern = build_pattern(keywords)
    hits = []
    for number, lines in enumerate(pages, start=1):
        text = prepare_text([lines])
        for found in pattern.finditer(text):
            start, end = found.span()
            hits.append({
                "page": number, "offset": start, "keyword": found.group(0),
                "before": text[max(0, start - context):start].replace("\n", " "),
                "match": found.group(0),
                "after": text[end:end + context].replace("\n", " "),
            })
    return hits


def hit_id(code: str, file: str, page: int, offset: int, keyword: str) -> str:
    """PDF内の位置で決まるID。再実行しても同じヒットには同じIDがつき、手動分類が引き継がれる。"""
    return f"{code}|{file}|p{page}|{offset}|{keyword}"


def run(run_id: str, progress: Progress) -> stage_runner.StageResult:
    settings = runs.run_config(run_id)
    targets = ingest.load_targets(run_id)
    included = targets[targets["included"] == "1"].reset_index(drop=True)
    progress.log(f"対象: {len(included)}社 キーワード: {settings['ai_keywords']}")

    hit_rows, extraction_rows = [], []
    for index, company in included.iterrows():
        code, name = company["code"], company["name"]
        progress.update(index, len(included), f"{code} {name}")
        path = Path(company["pdf_path"])
        try:
            text = page_text(path)
        except Exception as error:  # noqa: BLE001  1社の失敗で全体を止めない
            progress.error(code, name, "PDF読み込み失敗", str(error))
            extraction_rows.append({"code": code, "name": name, "file": path.name, "pages": 0,
                                    "text_status": f"読み込み失敗: {error}"})
            continue
        if text.failure:
            progress.error(code, name, "画像PDF/抽出不能", text.failure)
        extraction_rows.append({"code": code, "name": name, "file": path.name, "pages": len(text.pages),
                                "text_status": text.failure or "抽出可"})
        if text.failure:
            continue
        for hit in find_hits(text.pages, settings["ai_keywords"], settings["hit_context_chars"]):
            hit_rows.append({"hit_id": hit_id(code, path.name, hit["page"], hit["offset"], hit["keyword"]),
                             "code": code, "name": name, "file": path.name, **hit})
    progress.update(len(included), len(included), "集計", force=True)

    out = runs.stage_dir(run_id, stages.AI_COUNT)
    pd.DataFrame(hit_rows, columns=HIT_COLUMNS).to_csv(out / "hits.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(extraction_rows).to_csv(out / "extraction.csv", index=False, encoding="utf-8-sig")
    summary = summarize(run_id)
    progress.log(f"ヒット{len(hit_rows)}件 / {summary['message']}")
    return stage_runner.StageResult(summary["status"], summary["message"])


def load_hits(run_id: str) -> pd.DataFrame:
    """ヒット一覧に、手動分類（有効/誤検出、未分類は空）を付けて返す。"""
    hits = pd.read_csv(runs.stage_dir(run_id, stages.AI_COUNT) / "hits.csv", dtype=str, encoding="utf-8-sig",
                       keep_default_na=False)
    labels = overrides.active("hit_label").set_index("target")
    hits["label"] = hits["hit_id"].map(labels["new_value"]).fillna("")
    hits["label_reason"] = hits["hit_id"].map(labels["reason"]).fillna("")
    return hits


def summarize(run_id: str) -> dict:
    """hits.csv と手動分類から ai_flags.csv と summary.json を作り直す（PDFは読み直さない）。"""
    settings = runs.run_config(run_id)
    out = runs.stage_dir(run_id, stages.AI_COUNT)
    hits = load_hits(run_id)
    extraction = pd.read_csv(out / "extraction.csv", dtype=str, encoding="utf-8-sig", keep_default_na=False)

    raw = hits.groupby("code").size()
    false_hits = hits[hits["label"] == overrides.HIT_FALSE].groupby("code").size()
    confirmed = hits[hits["label"] == overrides.HIT_VALID].groupby("code").size()
    flags = extraction[["code", "name", "file", "text_status"]].copy()
    flags["hits_raw"] = flags["code"].map(raw).fillna(0).astype(int)
    flags["hits_false"] = flags["code"].map(false_hits).fillna(0).astype(int)
    flags["hits_confirmed"] = flags["code"].map(confirmed).fillna(0).astype(int)
    flags["ai_count"] = flags["hits_raw"] - flags["hits_false"]
    readable = flags["text_status"] == "抽出可"
    flags["ai_flag"] = (flags["ai_count"] >= settings["treatment_threshold"]).astype(int).astype(str)
    flags["ai_count"] = flags["ai_count"].astype(str)  # 判定不能は空欄にする
    flags.loc[~readable, ["ai_count", "ai_flag"]] = ["", ""]
    flags["group"] = [UNDETERMINED if not ok else (TREATED if flag == "1" else CONTROL)
                      for ok, flag in zip(readable, flags["ai_flag"])]
    by_keyword = hits[hits["label"] != overrides.HIT_FALSE].groupby(["code", "keyword"]).size().unstack(fill_value=0)
    for keyword in by_keyword.columns:
        flags[f"kw_{keyword}"] = flags["code"].map(by_keyword[keyword]).fillna(0).astype(int)
    flags.to_csv(out / "ai_flags.csv", index=False, encoding="utf-8-sig")

    counts = {group: int((flags["group"] == group).sum()) for group in (TREATED, CONTROL, UNDETERMINED)}
    small = [group for group in (TREATED, CONTROL) if counts[group] < settings["min_group_size"]]
    summary = {
        **counts,
        "hits_raw": int(len(hits)),
        "hits_false": int((hits["label"] == overrides.HIT_FALSE).sum()),
        "hits_unlabeled": int((hits["label"] == "").sum()),
        "threshold": settings["treatment_threshold"],
        "small_groups": small,
    }
    summary["message"] = (f"処置群{counts[TREATED]}社 / 対照群{counts[CONTROL]}社 / 判定不能{counts[UNDETERMINED]}社")
    if small:
        summary["status"] = stages.REVIEW
        summary["message"] += f"。{'・'.join(small)}が{settings['min_group_size']}社未満です。確認してください"
    else:
        summary["status"] = stages.DONE
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def load_flags(run_id: str) -> pd.DataFrame:
    return pd.read_csv(runs.stage_dir(run_id, stages.AI_COUNT) / "ai_flags.csv", dtype=str, encoding="utf-8-sig",
                       keep_default_na=False)


def label_hits(labels: list[dict]) -> tuple[str, dict]:
    """ヒットの手動分類を記録し、ai_flag を作り直す。labels: {hit_id, code, old, new, reason} の並び。

    ai_flag が変わると段階3以降の前提が変わるので、下流を「要再実行」にする。
    """
    from core import changes

    what = f"ヒットの手動分類 {len(labels)}件"
    run_id, _ = changes.begin_upstream_change(what, stages.COVARIATES)
    for label in labels:
        overrides.append(run_id, "hit_label", label["code"], label["hit_id"], old_value=label["old"],
                         new_value=label["new"], source="段階2 ヒット確認画面", reason=label["reason"])
    summary = summarize(run_id)
    entry = runs.stage_state(run_id, stages.AI_COUNT)
    if entry["status"] in (stages.DONE, stages.REVIEW):
        # 件数の確認済みフラグは、件数が変わらない限り引き継ぐ
        confirmed = entry.get("confirmed") and summary["status"] == stages.REVIEW
        runs.set_status(run_id, stages.AI_COUNT, stages.DONE if confirmed else summary["status"], summary["message"],
                        confirmed=bool(confirmed))
    changes.finish_upstream_change(run_id, what, stages.COVARIATES)
    return run_id, summary


def confirm_small_groups(run_id: str, note: str) -> None:
    """処置群・対照群が少ないことを確認したうえで先へ進める。"""
    summary = json.loads((runs.stage_dir(run_id, stages.AI_COUNT) / "summary.json").read_text(encoding="utf-8"))
    runs.set_status(run_id, stages.AI_COUNT, stages.DONE, summary["message"] + "（件数を確認済み）", confirmed=True)
    runs.log_event(run_id, "件数の確認", stage=2, detail=note)


if __name__ == "__main__":
    raise SystemExit(stage_runner.cli(stages.AI_COUNT))
