"""AIキーワードのヒット検出と、手動分類の ai_flag への反映。"""
from __future__ import annotations

import pandas as pd

import config
from core import ai_count, overrides, runs

KEYWORDS = config.DEFAULTS["ai_keywords"]


def test_word_boundary_excludes_rail_main():
    pages = [["RAIL事業とMAIN工事、AIG保険", "AI活用を進める"], ["生成AIの導入", "人工知能(AI)分野"]]
    hits = ai_count.find_hits(pages, KEYWORDS, 30)
    assert [(hit["page"], hit["match"]) for hit in hits] == [
        (1, "AI"), (2, "生成AI"), (2, "人工知能"), (2, "AI")]  # 生成AI は AI と二重計上しない


def test_context_and_page_saved():
    pages = [["前置き"], ["当社は施工管理にAIを導入し、生産性を高めた"]]
    hit = ai_count.find_hits(pages, KEYWORDS, 5)[0]
    assert hit["page"] == 2
    assert hit["before"] == "施工管理に"
    assert hit["after"] == "を導入し、"


def _write_stage2(run_id: str, hits: list[dict], extraction: list[dict]) -> None:
    out = runs.stage_dir(run_id, 2)
    pd.DataFrame(hits, columns=ai_count.HIT_COLUMNS).to_csv(out / "hits.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(extraction).to_csv(out / "extraction.csv", index=False, encoding="utf-8-sig")


def _hit(code: str, page: int, offset: int) -> dict:
    return {"hit_id": ai_count.hit_id(code, f"{code}.pdf", page, offset, "AI"), "code": code, "name": code,
            "file": f"{code}.pdf", "page": page, "offset": offset, "keyword": "AI",
            "before": "", "match": "AI", "after": ""}


def test_false_positive_label_flips_ai_flag(sandbox):
    run_id = runs.current_run()
    hits = [_hit("1001", 1, 10), _hit("1002", 1, 5), _hit("1002", 2, 7)]
    extraction = [{"code": c, "name": c, "file": f"{c}.pdf", "pages": 3, "text_status": "抽出可"}
                  for c in ("1001", "1002", "1003")]
    extraction.append({"code": "1004", "name": "1004", "file": "1004.pdf", "pages": 2, "text_status": "画像PDF"})
    _write_stage2(run_id, hits, extraction)

    summary = ai_count.summarize(run_id)
    assert (summary["処置群"], summary["対照群"], summary["判定不能"]) == (2, 1, 1)

    ai_count.label_hits([{"hit_id": hits[0]["hit_id"], "code": "1001", "old": "", "new": overrides.HIT_FALSE,
                          "reason": "社名の一部"}])
    flags = ai_count.load_flags(run_id).set_index("code")
    assert flags.loc["1001", "ai_flag"] == "0"
    assert flags.loc["1002", "ai_count"] == "2"
    assert flags.loc["1004", "group"] == ai_count.UNDETERMINED
