"""PSM用データセット作成のエントリポイント（段階1: 抽出 → 株価 → 最終CSV）。

使い方:
  python build_dataset.py                    # 全社
  python build_dataset.py --codes 1301 2897  # 指定銘柄だけ
  python build_dataset.py --no-prices        # 株価取得を省く
"""
from psm_dataset.build_dataset import main

if __name__ == "__main__":
    raise SystemExit(main())
