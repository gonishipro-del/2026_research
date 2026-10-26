"""PSM用データセット作成の全体統合（途中再開可能・進捗表示付き）。

使い方:
  python build_dataset.py                    # 全社
  python build_dataset.py --limit 20         # 先頭20社で動作確認
  python build_dataset.py --codes 1301 2897  # 指定銘柄だけ
  python build_dataset.py --no-prices        # 株価取得を省く（抽出だけ確認）
  python build_dataset.py --use-supplement   # supplement.csv をマージ（merge_supplement.py と同じ）

抽出結果（cache/extract/）と株価（cache/prices/）はキャッシュされ、再実行時は未処理分だけ処理する。
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from . import config
from .assemble import OUTPUT_COLUMNS, build_record
from .extract_pdf import extract_pdf
from .industry import load_industry_table
from .parse_filename import FileInfo, parse_filename
from .prices import PriceResult, price_before_announce
from .supplement import load_supplement

logger = logging.getLogger("psm")


def setup_logging() -> None:
    config.PSM_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(config.EXTRACTION_LOG, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


def _cache_file(info: FileInfo) -> Path:
    return config.EXTRACT_CACHE_DIR / f"{info.path.stem}.json"


def _extract_worker(path_str: str) -> dict:
    return extract_pdf(Path(path_str))


def extract_all(infos: list[FileInfo], workers: int, refresh: bool) -> dict[str, dict]:
    """PDFごとの抽出結果を返す（キャッシュ済みは再抽出しない）。キーはファイル名。"""
    config.EXTRACT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    pending: list[FileInfo] = []
    for info in infos:
        cache = _cache_file(info)
        if cache.exists() and not refresh:
            results[info.path.name] = json.loads(cache.read_text(encoding="utf-8"))
        else:
            pending.append(info)
    print(f"[抽出] 対象{len(infos)}件（キャッシュ利用{len(results)}件 / 新規{len(pending)}件）")

    if pending:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            outputs = executor.map(_extract_worker, [str(info.path) for info in pending], chunksize=2)
            for done, (info, output) in enumerate(zip(pending, outputs), start=1):
                _cache_file(info).write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
                results[info.path.name] = output
                if done % 50 == 0 or done == len(pending):
                    print(f"  抽出 {done}/{len(pending)}")
    return results


def collect_files(codes: list[str] | None, limit: int | None) -> list[FileInfo]:
    infos = []
    for path in sorted(config.PDF_INPUT_DIR.glob("*.pdf")):
        info = parse_filename(path)
        if info is None:
            logger.warning("ファイル名をパースできず除外: %s", path.name)
            continue
        if codes and info.code not in codes:
            continue
        infos.append(info)
    if limit:
        infos = infos[:limit]
    return infos


def select_annual(infos: list[FileInfo], extractions: dict[str, dict]) -> list[FileInfo]:
    """同一銘柄に複数ファイルがあれば最新の通期を1つ採用し、除外分をログに残す。"""
    by_code: dict[str, list[FileInfo]] = defaultdict(list)
    for info in infos:
        by_code[info.code].append(info)

    selected = []
    for code, group in sorted(by_code.items()):
        group.sort(key=lambda item: item.announce_date, reverse=True)
        # 通期かどうか不明（画像PDF等で表題が読めない）ものは通期扱いにする
        annual = [item for item in group if extractions[item.path.name].get("is_annual", True)]
        chosen = annual[0] if annual else None
        for item in group:
            if item is chosen:
                continue
            reason = "四半期・中間の短信" if item not in annual else "より新しい通期短信が存在"
            logger.info("除外 %s: %s", item.path.name, reason)
        if chosen is None:
            logger.warning("通期の短信が見つからず銘柄ごと除外: %s", code)
            continue
        selected.append(chosen)
    return selected


def run(args: argparse.Namespace, use_supplement: bool) -> None:
    setup_logging()
    infos = collect_files(args.codes, args.limit)
    if not infos:
        raise SystemExit(f"対象PDFがありません: {config.PDF_INPUT_DIR}")
    industry_table = load_industry_table()
    supplements = load_supplement(config.SUPPLEMENT_CSV) if use_supplement else {}
    if use_supplement:
        print(f"[補完] supplement.csv: {len(supplements)}社")

    extractions = extract_all(infos, args.workers, args.refresh_extract)
    selected = select_annual(infos, extractions)
    logger.info("対象PDF %d件 → 採用 %d社", len(infos), len(selected))

    failed_extraction = [
        {"code": info.code, "filename": info.path.name, "失敗理由": extractions[info.path.name].get("fail_reason", "")}
        for info in selected
        if extractions[info.path.name].get("status") != "ok"
    ]
    for row in failed_extraction:
        logger.warning("抽出失敗 %s: %s", row["filename"], row["失敗理由"])

    records, failed_prices = [], []
    for done, info in enumerate(selected, start=1):
        extraction = extractions[info.path.name]
        price: PriceResult | None = None
        if not args.no_prices:
            price = price_before_announce(info.code, info.announce_date, refresh=args.refresh_prices)
            if price.failed:
                failed_prices.append({"code": info.code, "name": info.name, "announce_date": info.announce_date, "理由": price.note})
                logger.warning("株価取得失敗 %s: %s", info.code, price.note)
        record = build_record(info, industry_table.get(info.code), extraction, price, supplements.get(info.code))
        records.append(record)
        if done % 25 == 0 or done == len(selected):
            print(f"[組立] {done}/{len(selected)} {info.code} {info.name}")

    write_outputs(records, failed_extraction, failed_prices, partial=bool(args.codes or args.limit))


def write_outputs(records: list[dict], failed_extraction: list[dict], failed_prices: list[dict], partial: bool) -> None:
    config.PSM_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
    dataset_path = config.DATASET_CSV if not partial else config.PSM_OUTPUT_DIR / "dataset_psm_partial.csv"
    frame.to_csv(dataset_path, index=False, encoding="utf-8-sig")
    print(f"[出力] {dataset_path}（{len(frame)}社）")

    if not partial:
        for path, rows, fields in (
            (config.FAILED_EXTRACTION_CSV, failed_extraction, ["code", "filename", "失敗理由"]),
            (config.FAILED_PRICES_CSV, failed_prices, ["code", "name", "announce_date", "理由"]),
        ):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        print(f"[出力] 抽出失敗 {len(failed_extraction)}社 / 株価取得失敗 {len(failed_prices)}社")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PSM用データセットを作成する")
    parser.add_argument("--codes", nargs="+", help="対象の証券コード（省略時は全社）")
    parser.add_argument("--limit", type=int, help="先頭N件だけ処理（動作確認用）")
    parser.add_argument("--no-prices", action="store_true", help="yfinanceの株価取得を省く")
    parser.add_argument("--refresh-extract", action="store_true", help="抽出キャッシュを無視して再抽出")
    parser.add_argument("--refresh-prices", action="store_true", help="株価キャッシュを無視して再取得")
    parser.add_argument("--workers", type=int, default=config.EXTRACT_WORKERS)
    parser.add_argument("--use-supplement", action="store_true", help="supplement.csv を反映する")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run(args, use_supplement=args.use_supplement)
    return 0
