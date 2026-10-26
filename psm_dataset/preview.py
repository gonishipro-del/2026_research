"""試作確認用: 指定銘柄の抽出値（サマリー/本表の両方）と最終レコードを縦に表示する。"""
from __future__ import annotations

import argparse
import json

from . import config
from .assemble import build_record
from .build_dataset import collect_files, extract_all
from .industry import load_industry_table
from .prices import price_before_announce


def _fmt(value) -> str:
    if value is None:
        return "(なし)"
    if isinstance(value, float):
        return f"{value:,.4f}" if abs(value) < 1000 else f"{value:,.0f}"
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("codes", nargs="+")
    parser.add_argument("--no-prices", action="store_true")
    args = parser.parse_args()

    infos = collect_files(args.codes, None)
    extractions = extract_all(infos, workers=1, refresh=True)
    industry = load_industry_table()
    for info in infos:
        extraction = extractions[info.path.name]
        print("=" * 78)
        print(f"{info.code} {info.name}  発表日 {info.announce_date}  [{info.path.name}]")
        print(f"  抽出status={extraction.get('status')} 基準={extraction.get('standard')} "
              f"範囲={extraction.get('scope')} 頁数={extraction.get('page_count')}")
        if extraction.get("status") != "ok":
            print(f"  失敗理由: {extraction.get('fail_reason')}")
        else:
            keys = sorted(set(extraction["summary"]) | set(extraction["statement"]))
            print(f"  {'項目':<20}{'サマリー頁':>22}{'BS/PL本表':>22}")
            for key in keys:
                print(f"  {key:<20}{_fmt(extraction['summary'].get(key)):>22}{_fmt(extraction['statement'].get(key)):>22}")
            print(f"  株式数 発行済={_fmt(extraction.get('shares_issued'))} 自己株={_fmt(extraction.get('treasury_shares'))}"
                  f"  AI出現数={extraction.get('ai_count')}")
        price = None if args.no_prices else price_before_announce(info.code, info.announce_date)
        record = build_record(info, industry.get(info.code), extraction, price)
        print("  --- 最終レコード ---")
        for key, value in record.items():
            if key != "備考":
                print(f"  {key:<20}{_fmt(value)}")
        print(f"  備考: {record['備考'] or '(なし)'}")
    return 0
