"""決算短信テキストに対するワード集計を担当するモジュール。"""
from __future__ import annotations

from pathlib import Path

from .keywords import Keyword
from .pdf_text import normalize_for_search

_STATUS_LABEL = {
    "ok": "取得済",
    "page_not_found": "企業ページなし",
    "tanshin_not_found": "本決算短信なし",
    "pdf_empty": "PDF欠損（サイト側）",
    "error": "取得エラー",
}


def analyze(
    metadata: list[dict],
    text_dir: Path,
    keywords: list[Keyword],
    min_text_chars: int,
) -> list[dict]:
    """1社1行の集計結果を作る。"""
    rows: list[dict] = []

    for record in metadata:
        row: dict = {
            "コード": record["code"],
            "銘柄名": record["name"],
            "33業種区分": record["industry33"],
            "規模区分": record["scale"],
            "ステータス": _STATUS_LABEL.get(record["status"], record["status"]),
            "決算短信表題": record["title"],
            "開示日": record["disclosed_on"],
            "PDFファイル": record["pdf_path"],
            "抽出文字数": 0,
            "該当有無": "",
            "ヒットワード数": 0,
            "合計ヒット数": 0,
        }
        for keyword in keywords:
            row[keyword.word] = 0
        row["代表的な出現箇所"] = ""
        row["備考"] = record.get("error", "")

        text_path = text_dir / f"{record['code']}.txt"
        if record["status"] != "ok" or not text_path.exists():
            row["該当有無"] = "判定不可"
            rows.append(row)
            continue

        raw_text = text_path.read_text(encoding="utf-8")
        row["抽出文字数"] = len(raw_text)
        if len(raw_text) < min_text_chars:
            row["該当有無"] = "判定不可"
            row["備考"] = "テキスト抽出量が僅少（画像PDFの可能性）"
            rows.append(row)
            continue

        text = normalize_for_search(raw_text)
        total = 0
        hit_words = 0
        context = ""
        for keyword in keywords:
            count = keyword.count(text)
            row[keyword.word] = count
            if count:
                total += count
                hit_words += 1
                if not context:
                    context = keyword.first_context(text)

        row["合計ヒット数"] = total
        row["ヒットワード数"] = hit_words
        row["該当有無"] = "○" if total else "－"
        row["代表的な出現箇所"] = context
        rows.append(row)

    return rows


def summarize(rows: list[dict], keywords: list[Keyword]) -> dict:
    """集計結果の全体サマリを作る。"""
    judged = [r for r in rows if r["該当有無"] in ("○", "－")]
    hit = [r for r in rows if r["該当有無"] == "○"]
    return {
        "対象企業数": len(rows),
        "短信取得成功": len([r for r in rows if r["ステータス"] == "取得済"]),
        "判定可能": len(judged),
        "いずれかのワードに該当": len(hit),
        "該当率": f"{len(hit) / len(judged):.1%}" if judged else "-",
        "ワード別該当社数": {
            keyword.word: len([r for r in judged if r[keyword.word] > 0])
            for keyword in keywords
        },
    }
