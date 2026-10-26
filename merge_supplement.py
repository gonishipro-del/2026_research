"""段階3: supplement.csv（画像PDFの読み取り結果）をマージして最終CSVを再生成する。

抽出・株価はキャッシュを使うため、単独で何度でも再実行できる。
"""
from psm_dataset.merge_supplement import main

if __name__ == "__main__":
    raise SystemExit(main())
