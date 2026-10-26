"""段階3: supplement.csv をマージして最終CSVを再生成する。"""
from __future__ import annotations

from .build_dataset import build_parser, run


def main() -> int:
    args = build_parser().parse_args()
    run(args, use_supplement=True)
    return 0
