"""段階2用: failed_extraction.csv のPDFをページ画像(PNG)に書き出す。

書き出した画像を Claude Code が読み込み、サマリー頁・BS・PLの数値を supplement.csv に記録する。
"""
from __future__ import annotations

import argparse
import csv

import pypdfium2 as pdfium

from . import config


def main() -> int:
    parser = argparse.ArgumentParser(description="抽出失敗PDFのページ画像を書き出す")
    parser.add_argument("--codes", nargs="+", help="対象コード（省略時は failed_extraction.csv の全件）")
    parser.add_argument("--max-pages", type=int, default=12, help="先頭から何頁まで書き出すか")
    parser.add_argument("--scale", type=float, default=1.6, help="描画倍率（72dpi基準）")
    args = parser.parse_args()

    with config.FAILED_EXTRACTION_CSV.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if not args.codes or row["code"] in args.codes]

    for row in rows:
        out_dir = config.RENDERED_PAGES_DIR / row["code"]
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            document = pdfium.PdfDocument(str(config.PDF_INPUT_DIR / row["filename"]))
        except Exception as error:  # noqa: BLE001
            print(f"{row['code']}: PDFを開けない ({error})")
            continue
        pages = min(len(document), args.max_pages)
        for index in range(pages):
            image = document[index].render(scale=args.scale).to_pil()
            image.save(out_dir / f"p{index + 1:02d}.png")
        document.close()
        print(f"{row['code']}: {pages}頁を {out_dir} に出力")
    return 0
