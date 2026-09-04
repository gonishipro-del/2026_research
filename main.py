"""東証プライム上場企業の決算短信を収集し、特定ワードを集計するエントリポイント。

  1. data_j.xlsx から東証プライム上場企業を抽出
  2. 決算プロ（株プロ）から各社の直近の本決算短信PDFをダウンロード
  3. PDFからテキストを抽出（結果はキャッシュ）
  4. keywords.txt に定義したワードの出現回数を集計し Excel / CSV に出力

使い方:
  python main.py                      # 全工程を実行
  python main.py --limit 20           # 先頭20社だけで動作確認
  python main.py --skip-download      # ダウンロード済みデータで集計だけやり直す
  python main.py --keywords other.txt # 別のワード定義ファイルを使う
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config
from src.analyzer import analyze, summarize
from src.companies import load_companies
from src.downloader import TanshinDownloader, load_metadata
from src.kabupro_client import KabuproClient
from src.keywords import load_keywords
from src.pdf_text import extract_all
from src.report import write_csv, write_xlsx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="処理する企業数の上限（0で全社）")
    parser.add_argument("--skip-download", action="store_true", help="PDFの取得を行わない")
    parser.add_argument(
        "--refresh-download", action="store_true", help="取得済みでも再ダウンロードする"
    )
    parser.add_argument(
        "--refresh-text", action="store_true", help="テキスト抽出キャッシュを作り直す"
    )
    parser.add_argument(
        "--keywords", type=Path, default=config.KEYWORD_FILE, help="ワード定義ファイル"
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()

    print("[1/4] 上場企業リストの読み込み")
    companies = load_companies(config.LISTED_COMPANY_XLSX, config.TARGET_MARKET)
    if args.limit:
        companies = companies[: args.limit]
    print(f"  対象: {config.TARGET_MARKET} {len(companies)} 社")

    print("[2/4] 決算短信PDFの取得")
    if args.skip_download:
        print("  --skip-download 指定のためスキップ")
        stored = load_metadata(config.METADATA_JSONL)
        metadata = [stored[c.code] for c in companies if c.code in stored]
    else:
        client = KabuproClient(
            base_url=config.KABUPRO_BASE_URL,
            user_agent=config.USER_AGENT,
            interval_sec=config.REQUEST_INTERVAL_SEC,
            timeout_sec=config.REQUEST_TIMEOUT_SEC,
            max_retries=config.MAX_RETRIES,
            retry_wait_sec=config.RETRY_WAIT_SEC,
        )
        downloader = TanshinDownloader(
            client=client,
            pdf_dir=config.PDF_DIR,
            metadata_path=config.METADATA_JSONL,
            max_workers=config.MAX_WORKERS,
        )
        metadata = downloader.run(companies, refresh=args.refresh_download)

    if not metadata:
        print("  取得結果がありません。処理を終了します。")
        return 1
    ok_records = [r for r in metadata if r["status"] == "ok"]
    print(f"  短信PDF取得成功: {len(ok_records)} / {len(metadata)} 社")

    print("[3/4] PDFからのテキスト抽出")
    config.TEXT_DIR.mkdir(parents=True, exist_ok=True)
    tasks = [
        (
            record["code"],
            config.BASE_DIR / record["pdf_path"],
            config.TEXT_DIR / f"{record['code']}.txt",
        )
        for record in ok_records
        if (config.BASE_DIR / record["pdf_path"]).exists()
    ]
    extract_all(tasks, workers=config.EXTRACT_WORKERS, refresh=args.refresh_text)

    print("[4/4] ワード集計と出力")
    keywords = load_keywords(args.keywords)
    print(f"  検索ワード {len(keywords)} 件: {', '.join(k.word for k in keywords)}")
    rows = analyze(metadata, config.TEXT_DIR, keywords, config.MIN_TEXT_CHARS)
    summary = summarize(rows, keywords)

    write_csv(rows, config.REPORT_CSV)
    write_xlsx(rows, summary, config.REPORT_XLSX)

    print("\n=== 集計サマリ ===")
    for key, value in summary.items():
        if isinstance(value, dict):
            continue
        print(f"  {key}: {value}")
    print("  ワード別 該当社数:")
    for word, count in summary["ワード別該当社数"].items():
        print(f"    {word}: {count}")
    print(f"\n  Excel: {config.REPORT_XLSX}")
    print(f"  CSV  : {config.REPORT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
