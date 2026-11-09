# 建設業 PSM 試運転アプリ（psm_construction_pilot）

> **試運転の結果であり、最終結果ではない。**
> 目的はパイプライン全体（収集 → AIカウント → 共変量 → マッチング → CAR → ATT → 評価）の動作確認。

建設業（東証プライム）を対象に、決算短信での AI 言及を処置とした傾向スコアマッチングと
イベントスタディ（Fama-French 5ファクター）を、Streamlit アプリ上でボタンを押して段階的に進める。
既存の `psm_dataset/` 等は変更せず、import と読み取りのみ行う。

## セットアップと起動

リポジトリ直下（`2026_research/`）で実行する。

```
venv\Scripts\python.exe -m pip install -r psm_construction_pilot\requirements.txt
venv\Scripts\python.exe -m streamlit run psm_construction_pilot\app\app.py
```

ブラウザで http://localhost:8501 を開く。各段階は CLI でも単体実行できる（`psm_construction_pilot/` で実行）。

```
..\venv\Scripts\python.exe -m core.inputs       # 入力ファイルの検証と登録
..\venv\Scripts\python.exe -m core.settings     # 現在の設定を確定（段階0）
..\venv\Scripts\python.exe -m core.ingest       # 段階1
..\venv\Scripts\python.exe -m core.ai_count     # 段階2
..\venv\Scripts\python.exe -m core.covariate_stage  # 段階3（共変量の収集と分析対象の確定）
..\venv\Scripts\python.exe -m core.matcher      # 段階4
..\venv\Scripts\python.exe -m core.balance      # 段階5（--freeze で凍結）
..\venv\Scripts\python.exe -m core.car          # 段階6
..\venv\Scripts\python.exe -m core.att          # 段階7
..\venv\Scripts\python.exe -m core.evaluate     # 段階8
..\venv\Scripts\python.exe -m pytest tests      # テスト
```

## 入力ファイル

外部サイトからの自動ダウンロードは行わない。入力は常に以下のフォルダのファイルを使う。

| 入力 | 置き場所 | 内容 |
|---|---|---|
| ファクター | `data/inputs/factors/` | Kenneth French Data Library の **Japan 5 Factors（日次）** CSV。zip のままでも解凍後の CSV でもよい |
| 企業一覧 | `data/inputs/master/` | JPX の上場銘柄一覧 `data_j.xlsx`（最新版） |
| 決算短信PDF | 既存フォルダを参照（コピーしない） | 既定は `../20260904dataset/pdf/`。設定で変更可 |
| 手動アップロード | `data/manual/pdfs/` | アプリから追加した PDF |

- 各入力フォルダには現在のファイルを1つだけ置く。ファイル名・更新日時・SHA256 を `state/inputs.json` と
  `state/input_history.csv` に記録する。
- アプリの「入力ファイルの更新」ページで差し替えられる。検証に通らないファイルには差し替えない。
  差し替え時は旧版を `archive/` に残し、下流の段階を「要再実行」にする（企業一覧→段階1以降、ファクター→段階6以降）。
- ファクターの検証: 列が `Mkt-RF, SMB, HML, RMW, CMA, RF` か（5ファクター）、日付が8桁で間隔が日次か、
  日本向けか。いずれかに反すれば止める（月次・3ファクター・他地域で代用しない）。
  CSV 本文に地域の記載がないため、**地域はファイル名（Japan を含み、ex Japan・Developed 等を含まない）で判定**する。
  値は % 表記なので 100 で割る。欠損コード（-99.99、-999）は欠損にする。

**通貨の扱い（方式A）**：Data Library のファクターは米ドル建て（RF も米国の短期金利）。そこで既定では、株価の日次リターンを
ドル円レートでドル建てに換算してから、CSV の RF（米国金利）をそのまま引く（設定 `stock_currency: usd`）。
$1 + R^{USD}_t = (1 + R^{JPY}_t) 	imes S_{t-1} / S_t$（$S$ は1ドルあたりの円）。

- レートは yfinance の `JPY=X`（日次終値）をプログラムで取得し、`data/cache/fx/` にキャッシュする（`core/fx.py`）。
  CAR を計算した run には、使ったレート（`fx_usdjpy.csv`）と取得元・取得日時（`currency.json`）を保存する。
- **日付の合わせ方**：Yahoo の日付ラベルはロンドン時間の0時区切りで、東京の株価の日付と1日ずれる。日付ラベルが1つ後の
  レートを、東京のその日のレートとして使う。日経平均をドル換算して市場ファクターとの相関を比べると、この合わせ方が
  0.93〜0.95 で最も高い（同じ日のラベルでは 0.64〜0.84）。`python -m core.fx --check` で再現できる。
  この合わせ方で、個別株の決定係数の中央値は 0.33（円建てのまま）から 0.44 に上がった。
- 頑健性検証として、円建てのままの市場モデル（日経平均）の結果も並べる。

## 段階と入出力

出力は `output/run_{日時}/stage{番号}_{名前}/` に置く。各段階の `log.txt`（ログ）、`errors.csv`（企業ごとのエラー）、
`progress.json`（進捗）もここに残る。

| 段階 | 内容 | 主な出力 | 状況 |
|---|---|---|---|
| 0 | 設定 | `config.yaml`（run ごとのスナップショット）、`state/config_history.csv` | 実装済み |
| 1 | 対象企業の確定と決算資料の取り込み | `targets.csv`、`excluded_pdfs.csv`、`summary.json` | 実装済み |
| 2 | AI言及のカウント | `hits.csv`、`extraction.csv`、`ai_flags.csv`、`summary.json` | 実装済み |
| 3 | 共変量の収集と欠損の確認（手動補完、分析対象の確定） | `covariates.csv`、`cell_status.csv`、`analysis_dataset.csv`、`cells_final.csv`、`excluded.csv`、`failed_extraction.csv`、`failed_prices.csv`、`state/manual_overrides.csv` | 実装済み |
| 4 | 傾向スコアの推定とマッチング | `ps_model.json`、`matched_pairs.csv`、`dropped_treated.csv`、`matching_grid.csv` | 実装済み |
| 5 | バランス診断とマッチングの確定（凍結） | `balance.csv`、`love.png`、`frozen.json` | 実装済み |
| 6 | CAR の計算（凍結後のみ） | `car.csv`、`ar_daily.csv`、`warnings.csv` | 実装済み |
| 7 | ATT の推定 | `att_summary.json`、`pairs_{窓}.csv` | 実装済み |
| 8 | 評価（頑健性・感度） | `sensitivity.csv`、`grid.csv`、`rosenbaum.csv`、`market_model.json`、`forest.png` | 実装済み |

段階番号はコードでは `core/stages.py` の名前（`stages.COVARIATES` など）で参照する。2026-11-09 に旧段階3（共変量の収集）と
旧段階4（欠損の確認と手動補完）を段階3にまとめ、以降の段階を1つずつ繰り上げた。

### 段階1
- 上場銘柄一覧から「市場・商品区分」と「33業種区分」で対象企業を抽出する。
- PDF はファイル名 `{証券コード}_{企業名}_{YYYYMMDD}.pdf` から証券コード・企業名・決算発表日を取る。
- 1頁目の表題で通期か四半期・中間かを判定する（既存の `psm_dataset.extract_pdf.detect_title_info`）。
  同一企業に複数あれば最新の通期を採用し、他は `excluded_pdfs.csv` に理由つきで残す。
- 5桁コードの銘柄（社債型種類株式など）は普通株式ではないので除外する。

### 段階2
- 照合規則は既存の `psm_dataset.ai_terms` と同じ（英字は単語境界つき、長い語を優先、改行で分断された日本語を結合）。
- ヒットごとに前後の文字（既定30字）とページ番号を保存する。ヒットID はPDF内の位置で決まり、再実行しても手動分類が引き継がれる。
- ヒット数が閾値以上で ai_flag=1。画面では、企業と何か所目かを選ぶと、その箇所の文脈とページを確認できる。
- 処置群または対照群が最小社数（既定10）未満なら「要確認」にし、確認メモを入れるまで先へ進めない。

### 段階3（前半：共変量の収集）
値は当該決算短信の**当期末**。財務値の抽出と組み立ては既存の `psm_dataset`（`extract_pdf` → `assemble.build_record`）を使い、
ここでは profit_change と、変数ごとの状態・理由コードを足す。

| 列名 | 定義 |
|---|---|
| log_total_assets | ln(総資産)。連結BSの資産合計。サマリー頁の値を本表でクロスチェック。連結がなければ単体で代替（代替値） |
| roa_ordinary | 経常利益 ÷ 総資産。経常利益がない会計基準は税引前利益で代替（代替値） |
| leverage | 負債合計 ÷ 総資産。負債合計が直接取れなければ 総資産 − 純資産合計。サマリー頁の 1−純資産÷総資産 と照合 |
| pbr | 決算発表日の前営業日の終値 ÷ BPS。BPSは開示値を優先し、自前計算（自己資本 ÷ 自己株式控除後の期末株式数）で照合。乖離率も保存 |
| profit_change | (当期経常利益 − 前期経常利益) ÷ 前期末総資産。前期値は同じ短信のサマリー頁の前期行（当期が税引前利益で代替なら前期も税引前利益） |

- 株価は yfinance の `{code}.T`（`auto_adjust=False` の Close。分割調整あり・配当調整なし）。PBR では株価日より後の分割を
  掛け戻して発表時点の実株価にする（`price_split_factor` に記録）。取得は `data/cache/prices/` にキャッシュし、リトライ・間隔あり。
  推定期間（段階6）も覆うよう、発表日の420暦日前から取得する。
- 状態: 取得済み / 代替値 / 要確認 / 欠損（段階3の後半で手動補完）。範囲外の値は捨てずに「要確認」にする。
- 理由コード: 画像PDF/抽出不能、項目なし（会計基準の違い等）、抽出失敗（表の解析不能）、値が範囲外（要確認）、前期値なし、
  株価取得失敗、BPS不一致、手動補完、代替値、**サマリーと本表の不一致**（追加）。
- 範囲チェック: 総資産>0、roa_ordinary −0.5〜0.5、leverage 0〜1.5、pbr 0〜20、profit_change −0.5〜0.5、BPS の乖離 5%以内。
- 総資産がサマリー頁と本表で食い違う企業は、同じ表から取った他の変数もまとめて「要確認」にする（表の列対応のずれを疑う）。

### 段階3（後半：欠損の確認と手動補完）
- 収集の直後に、段階2の ai_flag と手動入力を重ねて分析用データセット（`analysis_dataset.csv`）を作る。
- 状態はヒートマップ（企業×変数。色＋略字で状態・理由を表示）と企業単位の一覧で示す。企業を選ぶと、元PDFのページ画像（pypdfium2）、抽出された候補値、エラーログを並べて見られる。
- 手動入力: 値の入力（出典と理由が必須）、ai_flag の上書き（根拠必須）、分析からの除外（理由必須）、企業の追加（PDFアップロード）、取り消し。
  入力するとデータセットをその場で作り直し（段階3の再実行は不要）、段階4以降を「要再実行」にする。企業の追加は段階1から。
- 欠損が残る企業は**完全ケースとして除外**する。「要確認」の値が残る場合は、値を確認・修正するか、そのまま使うことを
  理由つきで確認するまで段階4へ進めない。

### 段階4〜8（推定）
- 4: ロジット `ai_flag ~ 共変量`（業種は入れない）。距離は線形予測子、キャリパーは線形予測子の標準偏差 × caliper_sd。
  最近傍・1対1。処置群の処理順は seed で決める。復元あり・なし × キャリパーを全部計算し、主分析は凍結時に選ぶ。
- 5: 標準化平均差（分母はマッチ前のプールした標準偏差）。凍結（`python -m core.balance --freeze`）で段階6が実行可能になる。
- 6: FF5（推定期間 [-250,-21]、有効観測数の下限 120）。株価とファクターの両方にある日だけを営業日として数える。
  窓の中で使えない日はその企業のCARを欠損にする。日次変化が±50%超の日は警告。
  長い窓（60日・90日）は、窓の末尾がファクターの最終日を過ぎる企業を計算から外すため、ペアが減ることがある。
- 7: ペアごとのCAR差の平均（復元ありは処置企業ごとに対照の平均）。t検定・Wilcoxon・処置企業単位のブートストラップ。
- 8: 窓（1日・3日・30日・60日・90日）・マッチ設定・プラセボ・Rosenbaum bounds（Γ、正規近似）・市場モデル（日経平均で代替）。
  J-Quants の APIキーが環境変数に無いため TOPIX は使えず、^N225 で代替している。

## 状態と再実行の規則

- 状態: 未実行 / 実行中 / 完了 / 要確認 / 要再実行 / 失敗（処理が例外で止まった場合。理由はログに残す）。
- 前の段階が「完了」になるまで次の段階は実行できない。段階6以降は段階5の凍結が必要。
- 段階を再実行すると、一度でも実行した下流は「要再実行」になる。設定の変更・入力ファイルの差し替え・手動入力でも、
  影響する段階から下流を「要再実行」にする（設定項目ごとの影響段階は `config.py` の `SETTING_FIRST_STAGE`）。
- 長い処理は別プロセスで実行し、進捗をファイルに書く。画面を閉じても処理と結果は残る。
- 段階7・8の結果を一度でも表示した後に上流を変えると「結果閲覧後の変更」として新しい run を作り（元の run は残す）、
  実行履歴（`state/run_history.csv`）に記録して警告バナーを出す。
- 手動入力（ヒットの分類、値の補完など）は `state/manual_overrides.csv` に追記するだけで、元データは変えない。
  取り消しも行の追記で表す。生成時は取り消されていない最新の手動入力を優先する。

## 設定の意味（`config.yaml`）

| 項目 | 意味 | 既定 |
|---|---|---|
| `market` | 対象の市場・商品区分 | プライム（内国株式） |
| `target_industries` | 対象の33業種区分 | [建設業] |
| `pdf_dir` | 決算短信PDFの既存フォルダ（`psm_construction_pilot/` からの相対パス可） | ../20260904dataset/pdf |
| `ai_keywords` | AI言及として数える語 | AI, 人工知能, 機械学習, … |
| `treatment_threshold` | 有効ヒット数がこれ以上で ai_flag=1 | 1 |
| `min_group_size` | 処置群・対照群がこれ未満なら警告 | 10 |
| `hit_context_chars` | ヒット前後に保存する文字数 | 30 |
| `covariates` | 傾向スコアの説明変数 | log_total_assets, roa_ordinary, leverage, pbr, profit_change |
| `caliper_sd` | キャリパー（ロジット傾向スコアの標準偏差の倍数） | 0.2 |
| `replacement_main` | 主分析を復元ありにするか（両方を計算して並べる。段階5で確定） | false |
| `event_windows` | CAR と ATT を出すイベントウィンドウ（営業日、発表日=0） | 1日 [0,0]、3日 [-1,1]、30日 [0,29]、60日 [0,59]、90日 [0,89] |
| `event_window_main` | 主分析のウィンドウ。発表時刻が不明で引け後発表の反応が翌営業日に出るため [-1,+1] | [-1,1] |
| `placebo_window` | プラセボ検定のウィンドウ | [-15,-11] |
| `estimation_window` | FF5 の推定期間 | [-250,-21] |
| `min_estimation_obs` | 推定期間の有効観測数の下限（未満なら CAR を欠損） | 120 |
| `include_alpha` | 異常収益率の計算で α̂ を含めるか | true |
| `stock_currency` | 株価リターンの通貨。`usd`＝ドル円レートでドル建てに換算してファクターと揃える、`jpy`＝換算しない（この項目が無い古い設定は `jpy` として読む） | usd |
| `caliper_grid` | 感度分析のキャリパー | 0.1, 0.2, 0.3 |
| `bootstrap_reps` | ブートストラップの回数 | 2000 |
| `rosenbaum_gammas` | Rosenbaum bounds の Γ | 1.0〜3.0 |
| `seed` | 乱数の種（同点処理・ブートストラップ） | 20261105 |

## フォルダ構成

```
psm_construction_pilot/
  config.py / config.yaml   設定の集約（パスは config.py、分析の設定値は config.yaml）
  core/                     各段階の処理（アプリから呼ぶ関数と単体実行CLI）
    stages.py runs.py state.py changes.py progress.py jobs.py stage_runner.py   段階・状態・run・実行の管理
    inputs.py factors.py master.py   入力ファイルの検証・差し替え
    settings.py overrides.py         設定の保存・変更履歴、手動入力ログ
    pdf_text.py page_images.py       PDFのテキスト・ページ画像（キャッシュ）
    ingest.py ai_count.py            段階1・2
    covariates.py review.py covariate_stage.py   段階3（収集、手動補完・分析対象の確定、まとめ）
    propensity.py matcher.py balance.py pairing.py   段階4・5
    fx.py                            ドル円レートの取得と、株価リターンのドル建て換算
    car.py att.py evaluate.py        段階6〜8
  app/                      Streamlit アプリ（app.py、pages/、ui/）
  data/inputs/              入力ファイル（factors/, master/。旧版は各 archive/）
  data/manual/              手動アップロード
  data/cache/               PDFテキスト・ページ画像・株価のキャッシュ
  output/run_{日時}/        設定のスナップショットと各段階の出力
  state/                    段階の状態、手動入力ログ（追記専用）、実行履歴、設定・入力の変更履歴
  tests/                    テスト
```

## 今後の検討事項

- 共変量は当該決算短信の当期末値を用いている。前期末値への切替は今後検討する。
