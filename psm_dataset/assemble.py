"""抽出結果（＋補完データ・株価）から、1企業1行の出力レコードを組み立てる。

値の優先順位（サマリー頁 → 本表 → 補完データ）、代替処理、備考の記録をここに集約する。
備考は「【カテゴリ】詳細」の形式で書き、report_missing.py がカテゴリ別に集計する。
"""
from __future__ import annotations

from . import config
from .industry import is_financial
from .parse_filename import FileInfo
from .prices import PriceResult
from .validate import check_record

OUTPUT_COLUMNS = [
    "code", "name", "announce_date", "industry_code", "industry_name", "is_financial",
    "roa_ordinary", "roa_netincome", "leverage",
    "bps", "price_pre_announce", "pbr", "ai_flag", "ai_count", "source_type",
    # 検証・追跡用の補助列
    "total_assets", "bps_disclosed", "bps_calc", "bps_deviation",
    "price_date", "price_split_factor", "accounting_standard", "statement_scope", "備考",
]

# 補完データ(supplement.csv)が埋める数値項目。金額は supplement 側の unit 列で円に換算する
SUPPLEMENT_AMOUNT_FIELDS = [
    "total_assets", "net_assets", "owner_equity", "liabilities",
    "ordinary_profit", "pretax_profit", "net_income_parent",
]
SUPPLEMENT_COUNT_FIELDS = ["shares_issued", "treasury_shares"]
SUPPLEMENT_RATE_FIELDS = ["bps_disclosed", "equity_ratio"]


def _first(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _rel_diff(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1.0)


def _candidates(extraction: dict, notes: list[str]) -> dict:
    """サマリー頁の値を優先し、なければ本表の値を採る。両方あれば一致を確認する。"""
    summary = extraction.get("summary", {})
    statement = extraction.get("statement", {})

    def pick(summary_key: str, statement_key: str, label: str, crosscheck: bool = True):
        sm, st = summary.get(summary_key), statement.get(statement_key)
        if crosscheck and sm is not None and st is not None and _rel_diff(sm, st) > config.CROSSCHECK_TOLERANCE:
            notes.append(f"【サマリーと本表の不一致】{label}: サマリー{sm:,.0f} / 本表{st:,.0f}（サマリー採用）")
        return _first(sm, st)

    return {
        "total_assets": pick("total_assets", "total_assets", "総資産"),
        "net_assets": pick("net_assets", "net_assets", "純資産合計"),
        "owner_equity": pick("owner_equity", "owner_equity", "自己資本"),
        "liabilities": statement.get("liabilities"),
        "nci": statement.get("nci"),
        "subscription_rights": statement.get("subscription_rights"),
        "ordinary_profit": pick("ordinary_profit", "ordinary_profit", "経常利益"),
        "pretax_profit": pick("pretax_profit", "pretax_profit", "税引前利益"),
        "net_income_parent": pick("net_income_parent", "net_income_parent", "親会社株主帰属純利益"),
        "net_income_total": _first(summary.get("net_income"), statement.get("net_income")),
        "shares_issued": extraction.get("shares_issued"),
        "treasury_shares": extraction.get("treasury_shares"),
        "bps_disclosed": summary.get("bps"),
        "equity_ratio": summary.get("equity_ratio"),
    }


def _apply_supplement(cand: dict, supplement: dict | None) -> list[str]:
    """未取得の項目だけを補完データで埋め、補完した項目名を返す。"""
    if not supplement:
        return []
    factor = {"百万円": 1e6, "千円": 1e3, "円": 1.0}.get(str(supplement.get("unit") or "百万円").strip(), 1e6)
    used = []
    for key in SUPPLEMENT_AMOUNT_FIELDS:
        value = supplement.get(key)
        if cand.get(key) is None and value is not None:
            cand[key] = value * factor
            used.append(key)
    for key in SUPPLEMENT_COUNT_FIELDS + SUPPLEMENT_RATE_FIELDS:
        value = supplement.get(key)
        if cand.get(key) is None and value is not None:
            cand[key] = value
            used.append(key)
    return used


def _scope_note(extraction: dict, notes: list[str]) -> str:
    scope = extraction.get("scope") or ("non_consolidated" if extraction.get("title_non_consolidated") else "")
    if scope == "non_consolidated":
        notes.append("【連結なし→単体】連結財務諸表がないため単体の値を使用")
    return scope or "consolidated"


def build_record(
    info: FileInfo,
    industry: tuple[str, str] | None,
    extraction: dict,
    price: PriceResult | None,
    supplement: dict | None = None,
) -> dict:
    notes: list[str] = []
    record = {column: None for column in OUTPUT_COLUMNS}
    industry_code, industry_name = industry if industry else ("", "")
    record.update(
        code=info.code,
        name=info.name,
        announce_date=info.announce_date.isoformat(),
        industry_code=industry_code,
        industry_name=industry_name,
        is_financial=is_financial(industry_name),
    )
    if industry is None:
        notes.append("【業種不明】業種コード表に該当コードなし")

    text_ok = extraction.get("status") == "ok"
    if text_ok:
        cand = _candidates(extraction, notes)
        record["accounting_standard"] = extraction.get("standard", "")
        record["statement_scope"] = _scope_note(extraction, notes)
        notes.extend(f"【抽出注意】{note}" for note in extraction.get("notes", []))
    else:
        cand = {key: None for key in (
            "total_assets", "net_assets", "owner_equity", "liabilities", "nci", "subscription_rights",
            "ordinary_profit", "pretax_profit", "net_income_parent", "net_income_total",
            "shares_issued", "treasury_shares", "bps_disclosed", "equity_ratio",
        )}
        if not supplement:
            notes.append(f"【画像PDF・要補完】{extraction.get('fail_reason', '')}")

    supplemented = _apply_supplement(cand, supplement)
    if supplement and supplement.get("scope") == "単体":
        notes.append("【連結なし→単体】補完データは単体の値")
        record["statement_scope"] = "non_consolidated"
    if supplemented:
        notes.append(f"【画像PDFから補完】{', '.join(supplemented)}")
        record["source_type"] = "image_supplement"
    elif text_ok:
        record["source_type"] = "text"
    if supplement and supplement.get("note"):
        notes.append(f"【補完メモ】{supplement['note']}")

    total_assets = cand["total_assets"]
    record["total_assets"] = total_assets
    if total_assets is not None and total_assets <= 0:
        notes.append("【妥当性】総資産が0以下")

    # 経常利益（IFRS・米国基準等では税引前利益で代替）
    ordinary = cand["ordinary_profit"]
    if ordinary is None and cand["pretax_profit"] is not None:
        ordinary = cand["pretax_profit"]
        notes.append("【経常利益なし→税引前利益で代替】roa_ordinary は税引前利益ベース")
    if ordinary is None and (text_ok or supplement):
        notes.append("【欠損】経常利益・税引前利益とも取得できず")
    net_income = cand["net_income_parent"]
    if net_income is None and cand["net_income_total"] is not None:
        net_income = cand["net_income_total"]
        notes.append("【純利益の代替】親会社帰属の記載がなく当期（純）利益で代替")

    # 負債合計（直接取れなければ 総資産−純資産合計）
    liabilities = cand["liabilities"]
    if liabilities is None and total_assets is not None and cand["net_assets"] is not None:
        liabilities = total_assets - cand["net_assets"]
        notes.append("【負債合計→総資産−純資産で算出】")
    if liabilities is None and (text_ok or supplement):
        notes.append("【欠損】負債合計を算出できず")

    # 自己資本（非支配株主持分を含む純資産合計は使わない）
    equity = cand["owner_equity"]
    if equity is None and cand["net_assets"] is not None and text_ok:
        equity = cand["net_assets"] - (cand["nci"] or 0.0) - (cand["subscription_rights"] or 0.0)
        notes.append("【自己資本→純資産から算出】純資産合計−非支配株主持分−新株予約権")
        if cand["nci"] is None:
            notes.append("【抽出注意】非支配株主持分の行がなく0扱い")
    if equity is None and (text_ok or supplement):
        notes.append("【欠損】自己資本を取得できず")

    # BPS（開示値を優先し、自前計算との乖離率を保持）
    bps_calc = None
    issued, treasury = cand["shares_issued"], cand["treasury_shares"]
    if issued is not None:
        if treasury is None:
            treasury = 0.0
            notes.append("【抽出注意】期末自己株式数が不明のため0扱い")
        outstanding = issued - treasury
        if equity is not None and outstanding > 0:
            bps_calc = equity / outstanding
    elif text_ok or supplement:
        notes.append("【欠損】期末発行済株式数を取得できず")
    bps_disclosed = cand["bps_disclosed"]
    bps = _first(bps_disclosed, bps_calc)
    if bps_disclosed is None and bps_calc is not None:
        notes.append("【BPS→自前計算】開示値なし")

    leverage = None
    if liabilities is not None and total_assets:
        leverage = liabilities / total_assets
    record.update(
        roa_ordinary=ordinary / total_assets if ordinary is not None and total_assets else None,
        roa_netincome=net_income / total_assets if net_income is not None and total_assets else None,
        leverage=leverage,
        bps=bps,
        bps_disclosed=bps_disclosed,
        bps_calc=bps_calc,
        bps_deviation=(bps_calc - bps_disclosed) / bps_disclosed if bps_calc and bps_disclosed else None,
    )

    if price is not None:
        record.update(
            price_pre_announce=price.price,
            price_date=price.price_date,
            price_split_factor=price.split_factor if price.price is not None else None,
        )
        if price.note:
            notes.append(f"【株式分割補正】{price.note}" if price.split_factor != 1.0 else f"【株価取得失敗】{price.note}")
        if price.price is not None and bps and bps > 0:
            record["pbr"] = price.price / bps

    # AI関連語（処置変数）
    if text_ok:
        record["ai_count"] = extraction.get("ai_count")
        record["ai_flag"] = int(extraction["ai_count"] > 0)
    elif supplement and supplement.get("ai_flag") is not None:
        record["ai_flag"] = int(supplement["ai_flag"])
        record["ai_count"] = supplement.get("ai_count")
    else:
        notes.append("【AI判定不可】本文を確認できず ai_flag は欠損")

    notes.extend(check_record(record, cand, equity))
    record["備考"] = "；".join(notes)
    return record
