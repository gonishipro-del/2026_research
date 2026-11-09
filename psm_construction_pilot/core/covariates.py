"""段階3: 共変量の収集（値は当該決算短信の当期末）。

財務値の抽出と値の組み立ては既存の psm_dataset（extract_pdf → assemble.build_record）を使う。
サマリー頁を優先し本表でクロスチェック、経常利益がなければ税引前利益で代替、負債合計が
なければ 総資産−純資産、BPS は開示値優先で自前計算と照合、といった規則はそちらに従う。
ここでは earnings_surprise（利益サプライズ）と、変数ごとの状態・理由コードを足す。

| 列名 | 定義 |
| log_total_assets | ln(総資産) |
| roa_ordinary | 経常利益 ÷ 総資産（経常利益がなければ税引前利益で代替＝代替値） |
| leverage | 負債合計 ÷ 総資産（負債合計がなければ 総資産−純資産合計） |
| pbr | 発表日の前営業日の終値 ÷ BPS |
| earnings_surprise | (当期経常利益 − 発表直前の会社予想の経常利益) ÷ 前期末総資産 |

利益サプライズの予想は、データセットの予想の資料（直前の四半期短信・業績予想の修正）から core.forecast で読む。
IFRS など経常利益がない会社は、実績・予想とも税引前利益で揃える（代替値）。予想に税引前利益もなければ営業利益で揃える。
予想が取れない会社は、前期の実績を予想の代わりに使う（代替値）。前期末総資産はサマリー頁の前期行。

範囲外の値は捨てずに「要確認」にする。手動補完は段階3の後半（core.review）で行う。

出力（output/run_*/stage3_covariates/）:
  covariates.csv         企業ごとの共変量・中間値・変数ごとの状態
  cell_status.csv        企業×変数の状態（縦持ち。ヒートマップ用）
  failed_extraction.csv  画像PDF等でテキスト・表が取れなかった企業（手動補完の対象）
  failed_prices.csv      株価を取得できなかった企業
  summary.json           件数

単体実行: python -m core.covariates [--run RUN_ID]
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from pathlib import Path

import pandas as pd

import config
from core import datasets, forecast, ingest, prices, runs, stage_runner, stages
from core.pdf_text import page_text
from core.progress import Progress
from psm_dataset import config as psm_config
from psm_dataset import summary_parser
from psm_dataset.assemble import build_record
from psm_dataset.extract_pdf import extract_pdf
from psm_dataset.parse_filename import parse_filename

VARIABLES = ["log_total_assets", "roa_ordinary", "leverage", "pbr", "earnings_surprise"]

# 状態
OK, SUBSTITUTE, REVIEW, MISSING, MANUAL = "取得済み", "代替値", "要確認", "欠損", "手動補完"
# 理由コード
R_IMAGE = "画像PDF/抽出不能"
R_NO_ITEM = "項目なし（会計基準の違い等）"
R_PARSE = "抽出失敗（表の解析不能）"
R_RANGE = "値が範囲外（要確認）"
R_NO_PRIOR = "前期値なし"
R_PRICE = "株価取得失敗"
R_BPS = "BPS不一致"
R_MANUAL = "手動補完"
R_SUBST = "代替値"
R_XCHECK = "サマリーと本表の不一致"
REASON_CODES = [R_IMAGE, R_NO_ITEM, R_PARSE, R_RANGE, R_NO_PRIOR, R_PRICE, R_BPS, R_MANUAL, R_SUBST, R_XCHECK]

# 範囲チェック（該当したら「要確認」。値は捨てない）
RANGES = {
    "roa_ordinary": (-0.5, 0.5),
    "leverage": psm_config.LEVERAGE_RANGE,
    "pbr": (0.0, 20.0),
    "earnings_surprise": (-0.2, 0.2),
}
LEVERAGE_IDENTITY_TOLERANCE = 0.01  # leverage と 1−純資産÷総資産 の許容差
EXTRACT_CACHE_DIR = config.CACHE_DIR / "extract"
_NON_JGAAP = ("IFRS", "米国基準", "US GAAP", "USGAAP")


# --- 抽出 ---------------------------------------------------------------------

def _extract_cached(path: Path) -> dict:
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    cache = EXTRACT_CACHE_DIR / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    result = extract_pdf(path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    return result


def prior_period_values(pages: list[list[str]]) -> dict:
    """サマリー頁の前期行（当期行の次の行）から、前期の経常利益・税引前利益・総資産を取る。

    既存の summary_parser は当期行だけを読むので、当期行を除いた行の並びで同じ解析を行う。
    表の選び方（連結を優先）も parse_summary と同じ。
    """
    lines = [line for page in pages[:psm_config.SUMMARY_PAGES] for line in page]
    tables = summary_parser._find_tables(lines)
    result: dict = {"prior_period": "", "prior_ordinary_profit": None, "prior_pretax_profit": None,
                    "prior_total_assets": None}
    for kind in ("pl", "bs"):
        candidates = [table for table in tables if table.kind == kind]
        if not candidates:
            continue
        current = next((t for t in candidates if t.scope == "consolidated"), candidates[0])
        index = current.unit_index
        rest = lines[:index + 1] + lines[index + 2:]
        if index + 1 >= len(rest) or not summary_parser._PERIOD_ROW.match(rest[index + 1]):
            continue
        prior = summary_parser._build_table(rest, index)
        if prior is None or prior.period >= current.period:
            continue
        result["prior_period"] = prior.period
        if kind == "pl":
            result["prior_ordinary_profit"] = prior.values.get("ordinary_profit")
            result["prior_pretax_profit"] = prior.values.get("pretax_profit")
        else:
            result["prior_total_assets"] = prior.values.get("total_assets")
    return result


def page_hints(pages: list[list[str]]) -> dict:
    """手動補完の画面で開くページ（1始まり）: サマリー、貸借対照表、損益計算書。"""
    def first(pattern: str, start: int = 1) -> int | None:
        compiled = re.compile(pattern)
        for number, lines in enumerate(pages[start:], start=start + 1):
            if any(compiled.match(line) for line in lines):
                return number
        return None
    return {"summary_page": 1,
            "bs_page": first(r"^\s*(?:資産合計|資産の部合計)\s*[\d△▲\-]"),
            "pl_page": first(r"^\s*(?:経常利益|税金等調整前当期純|税引前)")}


# --- 変数ごとの状態 -----------------------------------------------------------

def _cell(value, status: str, reason: str = "", detail: str = "") -> dict:
    return {"value": value, "status": status, "reason_code": reason, "detail": detail}


def _with_range(variable: str, cell: dict) -> dict:
    low, high = RANGES.get(variable, (-math.inf, math.inf))
    value = cell["value"]
    if value is not None and cell["status"] in (OK, SUBSTITUTE) and not low <= value <= high:
        return _cell(value, REVIEW, R_RANGE, f"{value:.4g} が {low}〜{high} の範囲外。{cell['detail']}".strip("。"))
    return cell


def derive_cells(record: dict, extraction: dict, prior: dict, price: prices.PriceResult | None,
                 expected: dict | None = None) -> dict:
    """build_record の結果と備考から、5変数の値・状態・理由コードを決める。"""
    notes = record.get("備考") or ""
    if extraction.get("status") != "ok":
        reason_text = extraction.get("fail_reason", "")
        reason = R_PARSE if reason_text.startswith("テキストはある") else R_IMAGE
        return {variable: _cell(None, MISSING, reason, reason_text) for variable in VARIABLES}

    standard = record.get("accounting_standard") or ""
    missing_item = R_NO_ITEM if standard in _NON_JGAAP else R_PARSE
    single = record.get("statement_scope") == "non_consolidated"
    cells: dict = {}

    total_assets = record.get("total_assets")
    if total_assets is None:
        cells["log_total_assets"] = _cell(None, MISSING, R_PARSE, "総資産を抽出できず")
    elif total_assets <= 0:
        cells["log_total_assets"] = _cell(None, REVIEW, R_RANGE, f"総資産が0以下（{total_assets:,.0f}）")
    elif "不一致】総資産" in notes:
        cells["log_total_assets"] = _cell(math.log(total_assets), REVIEW, R_XCHECK, _note(notes, "不一致】総資産"))
    else:
        cells["log_total_assets"] = _cell(math.log(total_assets), OK)
    ta_missing = cells["log_total_assets"]["value"] is None

    roa = record.get("roa_ordinary")
    pretax_basis = "税引前利益で代替" in notes
    if ta_missing:
        cells["roa_ordinary"] = _cell(None, MISSING, cells["log_total_assets"]["reason_code"], "総資産がない")
    elif roa is None:
        cells["roa_ordinary"] = _cell(None, MISSING, missing_item, f"経常利益・税引前利益とも取得できず（{standard or '基準不明'}）")
    elif pretax_basis:
        cells["roa_ordinary"] = _cell(roa, SUBSTITUTE, R_SUBST, f"経常利益がないため税引前利益で代替（{standard}）")
    elif "不一致】経常利益" in notes:
        cells["roa_ordinary"] = _cell(roa, REVIEW, R_XCHECK, _note(notes, "不一致】経常利益"))
    else:
        cells["roa_ordinary"] = _cell(roa, OK)

    leverage = record.get("leverage")
    if ta_missing:
        cells["leverage"] = _cell(None, MISSING, cells["log_total_assets"]["reason_code"], "総資産がない")
    elif leverage is None:
        cells["leverage"] = _cell(None, MISSING, R_PARSE, "負債合計・純資産合計とも取得できず")
    else:
        detail = "負債合計がないため 総資産−純資産合計 で算出" if "総資産−純資産で算出" in notes else ""
        cells["leverage"] = _cell(leverage, OK, "", detail)
        # 総資産 = 負債 + 純資産 なので、サマリー頁の純資産から求めた値と一致するはず。
        # 本表の単位の読み違い（百万円を円とみなす等）はここで検出する
        summary_net_assets = (extraction.get("summary") or {}).get("net_assets")
        if summary_net_assets is not None and total_assets:
            identity = 1 - summary_net_assets / total_assets
            if abs(identity - leverage) > LEVERAGE_IDENTITY_TOLERANCE:
                cells["leverage"] = _cell(leverage, REVIEW, R_XCHECK,
                                          f"本表の負債合計による値 {leverage:.4g} と、サマリー頁の 1−純資産÷総資産 = "
                                          f"{identity:.4f} が不一致")

    bps, pbr = record.get("bps"), record.get("pbr")
    deviation = record.get("bps_deviation")
    if price is None or price.failed:
        cells["pbr"] = _cell(None, MISSING, R_PRICE, price.note if price else "株価なし")
    elif bps is None:
        cells["pbr"] = _cell(None, MISSING, missing_item, "BPS（開示値・自前計算とも）を取得できず")
    elif pbr is None:
        cells["pbr"] = _cell(None, REVIEW, R_RANGE, f"BPSが0以下（{bps:,.2f}）")
    elif deviation is not None and abs(deviation) > psm_config.BPS_DEVIATION_TOLERANCE:
        cells["pbr"] = _cell(pbr, REVIEW, R_BPS,
                             f"BPS 開示{record['bps_disclosed']:,.2f} / 自前{record['bps_calc']:,.2f}（乖離{deviation:+.1%}）")
    elif record.get("bps_disclosed") is None:
        cells["pbr"] = _cell(pbr, SUBSTITUTE, R_SUBST, "BPSの開示値がなく自前計算（自己資本÷期末株式数）")
    else:
        detail = price.note if price.note else ""
        cells["pbr"] = _cell(pbr, OK, "", detail)

    cells["earnings_surprise"] = _earnings_surprise(record, prior, expected or {}, pretax_basis, ta_missing)

    if cells["log_total_assets"]["reason_code"] == R_XCHECK:
        # サマリー頁の表の列対応がずれている可能性（IFRS の財政状態表など）。同じ表から取った
        # BPS・前期末総資産や、総資産を分母に使う変数も疑わしいので、まとめて確認に回す
        for variable in VARIABLES[1:]:
            if cells[variable]["status"] in (OK, SUBSTITUTE):
                cells[variable] = _cell(cells[variable]["value"], REVIEW, R_XCHECK,
                                        "総資産がサマリー頁と本表で不一致のため要確認。" + cells[variable]["detail"])

    for variable in VARIABLES:
        cells[variable] = _with_range(variable, cells[variable])
        if single and cells[variable]["status"] in (OK, SUBSTITUTE):
            detail = cells[variable]["detail"]
            cells[variable] = _cell(cells[variable]["value"], SUBSTITUTE, R_SUBST,
                                    "連結財務諸表がないため単体の値。" + detail if detail else "連結財務諸表がないため単体の値")
    return cells


def _earnings_surprise(record: dict, prior: dict, expected: dict, pretax_basis: bool, ta_missing: bool) -> dict:
    """(実績 − 発表直前の会社予想) ÷ 前期末総資産。

    expected は {"forecast": 予想 or None, "notes": [...], "actual_operating": 当期の営業利益}。
    予想がなければ前期の実績で代替する。
    """
    roa, total_assets = record.get("roa_ordinary"), record.get("total_assets")
    if ta_missing or roa is None:
        return _cell(None, MISSING, R_PARSE, "当期の経常利益または総資産がない")
    prior_assets = prior.get("prior_total_assets")
    if prior_assets is None:
        return _cell(None, MISSING, R_NO_PRIOR, "サマリー頁の前期行から前期末総資産を取得できず")
    if prior_assets <= 0:
        return _cell(None, REVIEW, R_RANGE, f"前期末総資産が0以下（{prior_assets:,.0f}）")

    item, label = ("pretax_profit", "税引前利益") if pretax_basis else ("ordinary_profit", "経常利益")
    actual = roa * total_assets
    found = expected.get("forecast")
    values = (found or {}).get("values", {})
    predicted = values.get(item)
    if found and predicted is None and values.get("operating_profit") is not None \
            and expected.get("actual_operating") is not None:
        item, label = "operating_profit", "営業利益"
        actual, predicted = expected["actual_operating"], values["operating_profit"]
    if found and predicted is not None:
        value = (actual - predicted) / prior_assets
        detail = (f"{label}: (実績 {actual / 1e6:,.0f} − 予想 {predicted / 1e6:,.0f}) ÷ 前期末総資産 "
                  f"{prior_assets / 1e6:,.0f}（百万円。予想は {found['date']} の{found['kind']}）")
        if item != "ordinary_profit":
            return _cell(value, SUBSTITUTE, R_SUBST, f"経常利益がないため{label}で揃えた。" + detail)
        return _cell(value, OK, "", detail)

    # 予想が取れない：前期の実績を予想の代わりに使う
    prior_profit = prior.get("prior_pretax_profit") if pretax_basis else prior.get("prior_ordinary_profit")
    reasons = "；".join(expected.get("notes") or []) or "予想の資料がない"
    if prior_profit is None:
        return _cell(None, MISSING, R_NO_PRIOR, f"予想がなく、前期の{label}もない（{reasons}）")
    value = (actual - prior_profit) / prior_assets
    return _cell(value, SUBSTITUTE, R_SUBST,
                 f"予想がないため前期の実績で代替（{reasons}）。{label}: (実績 {actual / 1e6:,.0f} − 前期 "
                 f"{prior_profit / 1e6:,.0f}) ÷ {prior_assets / 1e6:,.0f}（百万円）")


def _note(notes: str, needle: str) -> str:
    return next((part for part in notes.split("；") if needle in part), "")


# --- 段階の実行 ---------------------------------------------------------------

def actual_period_and_operating(pages: list[list[str]]) -> tuple[str, float | None]:
    """本決算短信のサマリー頁の経営成績表から、期（例: 2026年3月期）と営業利益を取る。"""
    lines = [line for page in pages[:psm_config.SUMMARY_PAGES] for line in page]
    table = summary_parser.parse_summary(lines).tables.get("pl")
    if table is None:
        return "", None
    return table.period, table.values.get("operating_profit")


def run(run_id: str, progress: Progress) -> stage_runner.StageResult:
    settings = runs.run_config(run_id)
    documents = datasets.forecast_documents(settings["dataset"])
    if not documents:
        progress.log(f"データセット {settings['dataset']} には予想の資料がない。利益サプライズは前期の実績で代替する")
    targets = ingest.load_targets(run_id)
    included = targets[targets["included"] == "1"].reset_index(drop=True)
    total = len(included) * 2  # 抽出と株価の2工程
    progress.log(f"対象: {len(included)}社")

    extracted: dict[str, tuple] = {}
    failed_rows = []
    for index, company in included.iterrows():
        code, name = company["code"], company["name"]
        progress.update(index, total, f"抽出 {code} {name}")
        path = Path(company["pdf_path"])
        try:
            extraction = _extract_cached(path)
            pages = page_text(path).pages
            prior = prior_period_values(pages) if extraction.get("status") == "ok" else {}
            hints = page_hints(pages)
            period, operating = actual_period_and_operating(pages)
            found, notes = forecast.latest_forecast(documents.get(code, []), period)
            expected = {"forecast": found, "notes": notes, "actual_operating": operating, "period": period}
        except Exception as error:  # noqa: BLE001  1社の失敗で全体を止めない
            progress.error(code, name, R_PARSE, f"{type(error).__name__}: {error}")
            extraction = {"status": "failed", "fail_reason": f"テキストはあるが抽出中に例外: {error}"}
            prior, hints = {}, {"summary_page": 1, "bs_page": None, "pl_page": None}
            expected = {"forecast": None, "notes": [f"例外: {error}"], "actual_operating": None, "period": ""}
        if extraction.get("status") != "ok":
            reason = extraction.get("fail_reason", "")
            code_label = R_PARSE if reason.startswith("テキストはある") else R_IMAGE
            failed_rows.append({"code": code, "name": name, "file": path.name, "reason_code": code_label,
                                "detail": reason, "pages": extraction.get("page_count", "")})
            progress.error(code, name, code_label, reason)
        extracted[code] = (company, path, extraction, prior, hints, expected)

    rows, cell_rows, price_failures = [], [], []
    for index, (code, (company, path, extraction, prior, hints, expected)) in enumerate(extracted.items()):
        name = company["name"]
        progress.update(len(included) + index, total, f"株価 {code} {name}")
        announce = date.fromisoformat(company["announce_date"])
        series = prices.load_series(code, announce)
        price = prices.price_before_announce(series, announce)
        if price.failed:
            price_failures.append({"code": code, "name": name, "ticker": f"{code}.T", "detail": price.note})
            progress.error(code, name, R_PRICE, price.note)

        info = parse_filename(path)
        record = build_record(info, (company["industry33_code"], company["industry33"]), extraction, price)
        cells = derive_cells(record, extraction, prior, price, expected)
        found = expected["forecast"] or {}
        row = {
            "code": code, "name": name, "announce_date": company["announce_date"], "file": path.name,
            "pdf_path": str(path), "accounting_standard": record.get("accounting_standard") or "",
            "statement_scope": record.get("statement_scope") or "", "total_assets": record.get("total_assets"),
            "bps": record.get("bps"), "bps_disclosed": record.get("bps_disclosed"), "bps_calc": record.get("bps_calc"),
            "bps_deviation": record.get("bps_deviation"), "price_pre_announce": record.get("price_pre_announce"),
            "price_date": record.get("price_date"), "price_split_factor": record.get("price_split_factor"),
            **{key: prior.get(key) for key in ("prior_period", "prior_ordinary_profit", "prior_pretax_profit",
                                               "prior_total_assets")},
            "actual_period": expected["period"], "actual_operating_profit": expected["actual_operating"],
            "forecast_source": found.get("kind", ""), "forecast_date": found.get("date", ""),
            "forecast_title": found.get("title", ""), "forecast_pdf": found.get("path", ""),
            "forecast_ordinary_profit": (found.get("values") or {}).get("ordinary_profit"),
            "forecast_pretax_profit": (found.get("values") or {}).get("pretax_profit"),
            "forecast_operating_profit": (found.get("values") or {}).get("operating_profit"),
            "forecast_notes": "；".join(expected["notes"]),
            **hints, "notes": record.get("備考") or "",
        }
        for variable, cell in cells.items():
            row[variable] = cell["value"]
            row[f"{variable}_status"] = cell["status"]
            row[f"{variable}_reason"] = cell["reason_code"]
            row[f"{variable}_detail"] = cell["detail"]
            cell_rows.append({"code": code, "name": name, "variable": variable, **cell})
        rows.append(row)
    progress.update(total, total, "完了", force=True)

    out = runs.stage_dir(run_id, stages.COVARIATES)
    table = pd.DataFrame(rows)
    table.to_csv(out / "covariates.csv", index=False, encoding="utf-8-sig")
    cells_table = pd.DataFrame(cell_rows)
    cells_table.to_csv(out / "cell_status.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(failed_rows, columns=["code", "name", "file", "reason_code", "detail", "pages"]).to_csv(
        out / "failed_extraction.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(price_failures, columns=["code", "name", "ticker", "detail"]).to_csv(
        out / "failed_prices.csv", index=False, encoding="utf-8-sig")

    status_counts = cells_table.groupby(["variable", "status"]).size().unstack(fill_value=0)
    summary = {
        "companies": len(rows),
        "failed_extraction": len(failed_rows),
        "failed_prices": len(price_failures),
        "cells_by_status": cells_table["status"].value_counts().to_dict(),
        "cells_by_reason": cells_table.loc[cells_table["reason_code"] != "", "reason_code"].value_counts().to_dict(),
        "by_variable": {variable: status_counts.loc[variable].to_dict() for variable in status_counts.index},
        "complete_companies": int((table[[f"{v}_status" for v in VARIABLES]] != MISSING).all(axis=1).sum()),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["forecast_found"] = int((table["forecast_source"] != "").sum())
    missing = summary["cells_by_status"].get(MISSING, 0)
    review = summary["cells_by_status"].get(REVIEW, 0)
    message = (f"{len(rows)}社 / 予想あり{summary['forecast_found']}社 / 欠損{missing}セル / 要確認{review}セル / "
               f"抽出失敗{len(failed_rows)}社 / 株価失敗{len(price_failures)}社")
    return stage_runner.StageResult(stages.DONE, message)


def load_covariates(run_id: str) -> pd.DataFrame:
    return pd.read_csv(runs.stage_dir(run_id, stages.COVARIATES) / "covariates.csv", dtype=str, encoding="utf-8-sig",
                       keep_default_na=False)


def load_cells(run_id: str) -> pd.DataFrame:
    return pd.read_csv(runs.stage_dir(run_id, stages.COVARIATES) / "cell_status.csv", dtype=str, encoding="utf-8-sig",
                       keep_default_na=False)


if __name__ == "__main__":
    raise SystemExit(stage_runner.cli(stages.COVARIATES))
