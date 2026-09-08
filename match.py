"""傾向スコアマッチングのエントリポイント。

使い方:
  python match.py                              # 収集結果から分析用データを作る
  python match.py --covariates financials.csv  # 財務指標を共変量に加える
"""
from matching.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
