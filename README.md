# 東証プライム 決算短信 ワード集計

東証プライム上場企業（`data_j.xlsx` の「プライム（内国株式）」）について、
株プロの決算情報サイト **決算プロ（ke.kabupro.jp）** から直近の本決算短信PDFを
一括ダウンロードし、`keywords.txt` に定義した特定ワードの出現状況を表にまとめる。

## セットアップ

```
venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 実行

```
venv\Scripts\python.exe main.py                 # 全工程（取得 → 抽出 → 集計）
venv\Scripts\python.exe main.py --limit 20      # 先頭20社で動作確認
venv\Scripts\python.exe main.py --skip-download # 取得済みデータで集計のみやり直す
venv\Scripts\python.exe main.py --keywords other.txt
```

`--skip-download` は PDF もテキスト抽出結果もキャッシュを再利用するため、
**ワードを変えて集計し直すときは数十秒で終わる**。

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `main.py` | CLI エントリポイント。4工程を順に呼び出すだけの薄い層 |
| `config.py` | パス・通信設定・対象市場区分などの設定値 |
| `keywords.txt` | **検索ワードの定義ファイル**（1行1ワード、`#` はコメント） |
| `src/companies.py` | `data_j.xlsx` を読み、対象市場の企業一覧を作る |
| `src/kabupro_client.py` | 決算プロへの HTTP アクセス（Shift_JIS デコード、リトライ、間隔制御） |
| `src/disclosure.py` | 企業ページの適時開示一覧を解析し、本決算短信を1件選ぶ |
| `src/downloader.py` | PDF の並列ダウンロードと `metadata.jsonl` への記録・再開 |
| `src/pdf_text.py` | PDF からのテキスト抽出（プロセス並列＋キャッシュ）と検索用正規化 |
| `src/keywords.py` | ワード定義の読み込みとマッチング用正規表現の生成 |
| `src/analyzer.py` | 1社1行の集計とサマリ作成 |
| `src/report.py` | Excel / CSV への書き出し |

## 出力

| パス | 内容 |
| --- | --- |
| `20260904dataset/pdf/` | 決算短信PDF（`証券コード_銘柄名_開示日.pdf`） |
| `20260904dataset/text/` | PDF から抽出したテキスト（再集計用キャッシュ） |
| `20260904dataset/metadata.jsonl` | 1社1行の取得結果（表題・開示日・URL・ステータス） |
| `output/keyword_report.xlsx` | 集計表（「ワード集計」「サマリ」の2シート） |
| `output/keyword_report.csv` | 同内容の CSV（UTF-8 BOM付き） |

集計表は1行1社で、証券コード・銘柄名・業種・短信表題・開示日・PDFパスに加え、
**ワードごとの出現回数**、合計ヒット数、ヒットワード数、該当有無、
代表的な出現箇所（前後40字）を持つ。

## 仕様上の決めごと

- **対象書類**: 各社の直近の**本決算（通期）決算短信を1件**。四半期短信、訂正、
  決算説明会資料・補足資料は対象外（`src/disclosure.py` の選定ロジック）。
- **マッチング**: NFKC 正規化により全角/半角（`ＡＩ`/`AI`）と大文字小文字を同一視。
  英数字で始まる/終わるワードは単語境界を課すため、`MAINTENANCE` や `CHAIN` の
  "AI" は誤検出しない。日本語を含むワードは部分一致。
  PDF のレイアウト由来で日本語の間に入る改行・空白は除去してから検索する。
- **判定不可**: PDF がテキスト非埋め込み（画像PDF）の場合や短信を取得できなかった
  場合は、該当有無を「判定不可」とし備考に理由を記録する（0件と区別できる）。
- **サーバ負荷への配慮**: 同時接続3、リクエスト間隔0.5秒、リトライ3回
  （`config.py` で調整可）。決算プロのデータを引用する際は出典明記が求められる。
