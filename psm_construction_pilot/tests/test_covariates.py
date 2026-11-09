"""共変量: 前期値の読み取り、変数ごとの状態・理由コード。"""
from __future__ import annotations

import math

import pytest

from core import covariates
from core.covariates import MISSING, OK, REVIEW, SUBSTITUTE
from core.prices import PriceResult

SUMMARY_PAGE = [
    "(1)連結経営成績 (%表示は対前期増減率)",
    "売上高 営業利益 経常利益 親会社株主に帰属する当期純利益",
    "百万円 % 百万円 % 百万円 % 百万円 %",
    "2026年3月期 3,067,275 5.3 240,780 58.5 240,420 49.6 177,334 40.9",
    "2025年3月期 2,911,816 9.3 151,882 11.5 160,663 7.0 125,817 9.4",
    "(2)連結財政状態",
    "総資産 純資産 自己資本比率 1株当たり純資産",
    "百万円 百万円 % 円 銭",
    "2026年3月期 3,624,341 1,436,220 39.0 3,036.89",
    "2025年3月期 3,454,592 1,277,988 36.4 2,672.64",
]


def test_prior_period_read_from_next_row():
    prior = covariates.prior_period_values([SUMMARY_PAGE])
    assert prior["prior_period"] == "2025年3月期"
    assert prior["prior_ordinary_profit"] == pytest.approx(160_663e6)
    assert prior["prior_total_assets"] == pytest.approx(3_454_592e6)


def _record(**overrides) -> dict:
    record = {"total_assets": 3_624_341e6, "roa_ordinary": 240_420 / 3_624_341, "leverage": 1 - 1_436_220 / 3_624_341,
              "bps": 3036.89, "bps_disclosed": 3036.89, "bps_calc": 3040.0, "bps_deviation": 0.001, "pbr": 1.5,
              "accounting_standard": "日本基準", "statement_scope": "consolidated", "備考": ""}
    record.update(overrides)
    return record


EXTRACTION = {"status": "ok", "summary": {"net_assets": 1_436_220e6}}
PRIOR = {"prior_period": "2025年3月期", "prior_ordinary_profit": 160_663e6, "prior_pretax_profit": 150_000e6,
         "prior_total_assets": 3_454_592e6}
PRICE = PriceResult(price=4555.0, price_date="2026-05-13")


FORECAST = {"forecast": {"values": {"ordinary_profit": 226_000e6, "operating_profit": 228_000e6},
                         "date": "2026-02-12", "kind": "四半期短信"}, "notes": [], "actual_operating": 240_780e6}


def test_all_ok_and_earnings_surprise_formula():
    cells = covariates.derive_cells(_record(), EXTRACTION, PRIOR, PRICE, FORECAST)
    assert {cell["status"] for cell in cells.values()} == {OK}
    assert cells["log_total_assets"]["value"] == pytest.approx(math.log(3_624_341e6))
    # (実績の経常利益 − 発表直前の予想) ÷ 前期末総資産
    assert cells["earnings_surprise"]["value"] == pytest.approx((240_420 - 226_000) / 3_454_592)


def test_missing_forecast_falls_back_to_prior_actual():
    cells = covariates.derive_cells(_record(), EXTRACTION, PRIOR, PRICE,
                                    {"forecast": None, "notes": ["予想の表が見つからない"]})
    assert cells["earnings_surprise"]["status"] == SUBSTITUTE
    assert cells["earnings_surprise"]["value"] == pytest.approx((240_420 - 160_663) / 3_454_592)
    assert "前期の実績で代替" in cells["earnings_surprise"]["detail"]


def test_operating_profit_used_when_forecast_lacks_the_item():
    expected = {"forecast": {"values": {"operating_profit": 200_000e6}, "date": "2026-02-06", "kind": "業績予想の修正"},
                "notes": [], "actual_operating": 240_780e6}
    cells = covariates.derive_cells(_record(), EXTRACTION, PRIOR, PRICE, expected)
    assert cells["earnings_surprise"]["status"] == SUBSTITUTE
    assert cells["earnings_surprise"]["value"] == pytest.approx((240_780 - 200_000) / 3_454_592)


def test_image_pdf_makes_all_missing():
    cells = covariates.derive_cells({}, {"status": "failed", "fail_reason": "画像PDFまたは文字抽出不能"}, {}, None)
    assert all(cell["status"] == MISSING and cell["reason_code"] == covariates.R_IMAGE for cell in cells.values())


def test_leverage_unit_error_is_flagged_not_dropped():
    cells = covariates.derive_cells(_record(leverage=6e-7), EXTRACTION, PRIOR, PRICE)
    assert cells["leverage"]["status"] == REVIEW
    assert cells["leverage"]["value"] == pytest.approx(6e-7)  # 値は捨てない


def test_pretax_basis_uses_pretax_forecast():
    record = _record(備考="【経常利益なし→税引前利益で代替】roa_ordinary は税引前利益ベース", accounting_standard="IFRS")
    expected = {"forecast": {"values": {"pretax_profit": 200_000e6}, "date": "2026-02-06", "kind": "四半期短信"},
                "notes": [], "actual_operating": None}
    cells = covariates.derive_cells(record, EXTRACTION, PRIOR, PRICE, expected)
    assert cells["roa_ordinary"]["status"] == SUBSTITUTE
    assert cells["earnings_surprise"]["status"] == SUBSTITUTE
    assert cells["earnings_surprise"]["value"] == pytest.approx((240_420e6 - 200_000e6) / 3_454_592e6)


def test_missing_prior_and_price_failure_reasons():
    cells = covariates.derive_cells(_record(), EXTRACTION, {"prior_period": ""}, PriceResult(failed=True, note="x"))
    assert cells["earnings_surprise"]["reason_code"] == covariates.R_NO_PRIOR
    assert cells["pbr"]["reason_code"] == covariates.R_PRICE


def test_bps_mismatch_is_review():
    cells = covariates.derive_cells(_record(bps_deviation=0.12, bps_calc=3400.0), EXTRACTION, PRIOR, PRICE)
    assert cells["pbr"]["status"] == REVIEW and cells["pbr"]["reason_code"] == covariates.R_BPS


def test_total_assets_crosscheck_propagates_to_dependent_variables():
    record = _record(備考="【サマリーと本表の不一致】総資産: サマリー645,230,000,000 / 本表2,023,100,000,000（サマリー採用）")
    cells = covariates.derive_cells(record, EXTRACTION, PRIOR, PRICE, FORECAST)
    assert all(cells[v]["status"] == REVIEW for v in covariates.VARIABLES)
    assert cells["pbr"]["value"] == pytest.approx(1.5)  # 値は残す
