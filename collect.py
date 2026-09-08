"""データ収集のエントリポイント（決算短信の取得 → テキスト抽出 → ワード集計）。

使い方:
  python collect.py                      # 全工程を実行
  python collect.py --limit 20           # 先頭20社だけで動作確認
  python collect.py --skip-download      # ダウンロード済みデータで集計だけやり直す
  python collect.py --keywords other.txt # 別のワード定義ファイルを使う
"""
from collection.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
