"""傾向スコアマッチングの CLI 本体。

現時点では分析用データの構築（処置群・対照群の切り分けと共変量の準備）までを
実装している。傾向スコアの推定とマッチングは `propensity.py` / `matcher.py` を
追加してここから呼び出す。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .dataset import build_units, describe, merge_covariates, read_rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="match.py", description="収集済みデータを用いた傾向スコアマッチング"
    )
    parser.add_argument(
        "--input", type=Path, default=config.ANALYSIS_INPUT_CSV, help="ワード集計CSV"
    )
    parser.add_argument(
        "--covariates",
        type=Path,
        default=config.COVARIATE_CSV,
        help="財務指標など追加共変量のCSV（証券コードで結合）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)

    print("[1/2] 分析用データの読み込み")
    rows = read_rows(args.input)
    print(f"  入力: {args.input} （{len(rows)} 社）")
    if args.covariates:
        rows = merge_covariates(rows, args.covariates, config.COVARIATE_KEY_COLUMN)
        print(f"  追加共変量: {args.covariates}")

    print("[2/2] 処置群・対照群の切り分け")
    units, dropped = build_units(
        rows,
        treated_mark=config.TREATED_MARK,
        control_mark=config.CONTROL_MARK,
        categorical=config.CATEGORICAL_COVARIATES,
        numeric=config.NUMERIC_COVARIATES,
    )
    if not units:
        print("  母集団が空です。処理を終了します。")
        return 1

    for key, value in describe(units).items():
        print(f"  {key}: {value}")
    print(f"  除外: {len(dropped)} 社")

    print("\n傾向スコアの推定とマッチングは未実装です。")
    print("共変量（config.py の CATEGORICAL_COVARIATES / NUMERIC_COVARIATES）を")
    print("確定させたうえで propensity.py / matcher.py を追加してください。")
    return 0
