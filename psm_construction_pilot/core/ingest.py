"""段階1: 対象企業の確定と決算資料の取り込み。

上場銘柄一覧からプライム市場かつ対象業種の企業を抽出し、決算短信PDFを対応づける。
PDF は (a) 既存フォルダのスキャン、(b) アプリからの手動アップロード（data/manual/pdfs/）の2経路。
ファイル名 {証券コード}_{企業名}_{YYYYMMDD}.pdf から証券コード・企業名・決算発表日を取る。
通期決算短信のみが対象。同一企業に複数あれば最新の通期を採用し、除外分と理由を記録する。

出力（output/run_*/stage1_ingest/）:
  targets.csv        対象企業一覧（PDFあり/なし、通期か、除外理由）
  excluded_pdfs.csv  採用しなかったPDFと理由
  summary.json       件数

単体実行: python -m core.ingest [--run RUN_ID]
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import config
from core import datasets, inputs, master, overrides, runs, stage_runner, stages
from core.pdf_text import page_text
from core.progress import Progress
from psm_dataset.extract_pdf import detect_title_info
from psm_dataset.parse_filename import FileInfo, parse_filename

ANNUAL, INTERIM, UNKNOWN = "通期", "四半期・中間", "判定不能"
SOURCE_EXISTING, SOURCE_MANUAL = "データセット", "手動アップロード"


def target_companies(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    """市場で絞り、対象業種（空なら全業種）から除外業種を除く。"""
    rows = frame[frame["市場・商品区分"] == settings["market"]]
    if settings["target_industries"]:
        rows = rows[rows["33業種区分"].isin(settings["target_industries"])]
    rows = rows[~rows["33業種区分"].isin(settings.get("excluded_industries") or [])]
    return rows.sort_values("コード").reset_index(drop=True)


def master_file(settings: dict) -> Path:
    """データセットに写した上場銘柄一覧があればそれを、なければ入力フォルダの最新版を使う。"""
    snapshot = datasets.path(settings["dataset"]) / "master.xlsx"
    if snapshot.exists():
        return snapshot
    ok, reason = inputs.is_valid("master")
    if not ok:
        raise RuntimeError(reason)
    return inputs.current_file("master")


def is_preferred_share(code: str, name: str) -> bool:
    """5桁コードの銘柄（社債型種類株式など）は普通株式ではないので分析対象から外す。"""
    return len(code) == 5 or "種類株式" in name


def scan_pdfs(folders: dict[str, Path], progress: Progress) -> list[tuple[FileInfo, str]]:
    found = []
    for source, folder in folders.items():
        if not folder.exists():
            progress.log(f"PDFフォルダがありません（{source}）: {folder}")
            continue
        for path in sorted(folder.glob("*.pdf")):
            info = parse_filename(path)
            if info is None:
                progress.log(f"ファイル名を解釈できないPDFを無視: {path.name}")
                continue
            found.append((info, source))
    return found


def classify_period(path: Path) -> dict:
    """表題から通期か四半期・中間かを判定する。テキストが取れなければ判定不能。"""
    text = page_text(path)
    if text.failure or not text.pages:
        return {"period_type": UNKNOWN, "title": "", "standard": "", "text_status": text.failure or "ページなし"}
    title = detect_title_info(text.pages[0])
    return {
        "period_type": ANNUAL if title["is_annual"] else INTERIM,
        "title": title["title"], "standard": title["standard"], "text_status": "抽出可",
    }


def manual_companies() -> pd.DataFrame:
    """段階3で手動追加された企業（証券コード・企業名・決算発表日）。"""
    rows = []
    for _, row in overrides.active("add_company").iterrows():
        detail = json.loads(row["new_value"])
        rows.append({"コード": row["code"], "銘柄名": detail.get("name", ""), "手動追加": 1})
    return pd.DataFrame(rows)


def run(run_id: str, progress: Progress) -> stage_runner.StageResult:
    settings = runs.run_config(run_id)
    dataset = datasets.get(settings["dataset"])
    master_path = master_file(settings)
    frame = master.load(master_path)
    targets = target_companies(frame, settings)
    industries = "・".join(settings["target_industries"]) or "全業種"
    excluded = "・".join(settings.get("excluded_industries") or []) or "なし"
    progress.log(f"データセット: {dataset['id']}（{dataset['label']}）")
    progress.log(f"上場銘柄一覧: {master_path}（{len(frame)}行）→ {settings['market']} × {industries}"
                 f"（除外: {excluded}）: {len(targets)}社")

    targets["手動追加"] = 0
    added = manual_companies()
    if not added.empty:
        added = added[~added["コード"].isin(targets["コード"])]
        known = frame.set_index("コード")
        for column in targets.columns:
            if column not in added:
                added[column] = added["コード"].map(known[column]) if column in known else ""
        targets = pd.concat([targets, added[targets.columns]], ignore_index=True)
        progress.log(f"手動追加の企業: {len(added)}社")

    pdf_dir = datasets.pdf_dir(settings["dataset"])
    pdfs = scan_pdfs({SOURCE_EXISTING: pdf_dir, SOURCE_MANUAL: config.MANUAL_PDF_DIR}, progress)
    by_code: dict[str, list[tuple[FileInfo, str]]] = {}
    for info, source in pdfs:
        by_code.setdefault(info.code, []).append((info, source))
    progress.log(f"PDFフォルダ: {pdf_dir}（{len(pdfs)}件）")

    company_rows, excluded_rows = [], []
    for index, company in targets.iterrows():
        code, name = company["コード"], company["銘柄名"]
        progress.update(index, len(targets), f"{code} {name}")
        row = {
            "code": code, "name": name, "market": company["市場・商品区分"],
            "industry33_code": company["33業種コード"], "industry33": company["33業種区分"],
            "industry17": company["17業種区分"], "scale": company["規模区分"],
            "source": "手動追加" if company.get("手動追加") == 1 else "上場銘柄一覧",
        }
        candidates = sorted(by_code.get(code, []), key=lambda item: (item[0].announce_date, item[1] == SOURCE_MANUAL),
                            reverse=True)
        row["pdf_count"] = len(candidates)
        classified = []
        for info, source in candidates:
            try:
                period = classify_period(info.path)
            except Exception as error:  # noqa: BLE001  1社の失敗で全体を止めない
                progress.error(code, name, "PDF読み込み失敗", f"{info.path.name}: {error}")
                period = {"period_type": UNKNOWN, "title": "", "standard": "", "text_status": f"読み込み失敗: {error}"}
            classified.append((info, source, period))

        adopted = next((item for item in classified if item[2]["period_type"] != INTERIM), None)
        for info, source, period in classified:
            if adopted is not None and info.path == adopted[0].path:
                continue
            reason = ("四半期・中間の短信" if period["period_type"] == INTERIM
                      else f"より新しい短信を採用（{adopted[0].path.name}）")
            excluded_rows.append({"code": code, "name": name, "file": info.path.name, "pdf_source": source,
                                  "announce_date": info.announce_date.isoformat(),
                                  "period_type": period["period_type"], "title": period["title"], "reason": reason})

        if adopted is None:
            row.update(pdf_available="なし" if not candidates else "あり", adopted_pdf="", pdf_path="",
                       pdf_source="", announce_date="", period_type="", title="", standard="", text_status="",
                       included=0,
                       exclusion_reason="決算短信PDFなし" if not candidates else "通期の決算短信なし")
        else:
            info, source, period = adopted
            row.update(pdf_available="あり", adopted_pdf=info.path.name, pdf_path=str(info.path),
                       pdf_source=source, announce_date=info.announce_date.isoformat(), **period,
                       included=1, exclusion_reason="")
            if period["period_type"] == UNKNOWN:
                progress.log(f"{code} {name}: 通期か判定不能（{period['text_status']}）。最新のPDFを採用")
        if is_preferred_share(code, name):
            row.update(included=0, exclusion_reason="種類株式（普通株式ではない）")
        company_rows.append(row)
    progress.update(len(targets), len(targets), "完了", force=True)

    out = runs.stage_dir(run_id, stages.INGEST)
    companies = pd.DataFrame(company_rows)
    companies.to_csv(out / "targets.csv", index=False, encoding="utf-8-sig")
    excluded_columns = ["code", "name", "file", "pdf_source", "announce_date", "period_type", "title", "reason"]
    pd.DataFrame(excluded_rows, columns=excluded_columns).to_csv(
        out / "excluded_pdfs.csv", index=False, encoding="utf-8-sig")

    summary = {
        "master_file": master_path.name,
        "dataset": settings["dataset"],
        "pdf_dir": str(pdf_dir),
        "target_companies": int(len(companies)),
        "included": int(companies["included"].sum()) if len(companies) else 0,
        "no_pdf": int((companies["exclusion_reason"] == "決算短信PDFなし").sum()) if len(companies) else 0,
        "preferred_share": int((companies["exclusion_reason"] == "種類株式（普通株式ではない）").sum()) if len(companies) else 0,
        "no_annual": int((companies["exclusion_reason"] == "通期の決算短信なし").sum()) if len(companies) else 0,
        "period_unknown": int((companies.get("period_type") == UNKNOWN).sum()) if len(companies) else 0,
        "excluded_pdfs": len(excluded_rows),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    message = (f"対象{summary['target_companies']}社 / 採用{summary['included']}社 / "
               f"PDFなし{summary['no_pdf']}社 / 種類株式{summary['preferred_share']}社 / 通期なし{summary['no_annual']}社")
    if summary["included"] == 0:
        return stage_runner.StageResult(stages.REVIEW, "採用できる決算短信がありません。" + message)
    return stage_runner.StageResult(stages.DONE, message)


def load_targets(run_id: str) -> pd.DataFrame:
    return pd.read_csv(runs.stage_dir(run_id, stages.INGEST) / "targets.csv", dtype=str, encoding="utf-8-sig",
                       keep_default_na=False)


if __name__ == "__main__":
    raise SystemExit(stage_runner.cli(stages.INGEST))
