"""業績予想の読み取りと、予想の資料の選び方。入力は実際の決算短信・開示から取り出した行。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from core import forecast

QUARTERLY_JGAAP = [  # 鹿島建設 2026年3月期 第3四半期決算短信のサマリー頁
    "(注) 直近に公表されている配当予想からの修正の有無 : 無",
    "3.2026年3月期の連結業績予想(2025年4月1日~2026年3月31日)",
    "(%表示は対前期増減率)",
    "売上高 営業利益 経常利益 親会社株主に帰属",
    "する当期純利益",
    "1株当たり",
    "当期純利益",
    "百万円 % 百万円 % 百万円 % 百万円 % 円 銭",
    "通 期 3,030,000 4.1 228,000 50.1 226,000 40.7 170,000 35.1 364.11",
    "(注) 直近に公表されている業績予想からの修正の有無 : 有",
]
QUARTERLY_IFRS = [  # トヨタ自動車 2026年3月期 第3四半期決算短信
    "3.2026年3月期の連結業績予想(2025年4月1日~2026年3月31日)",
    "(%表示は、対前期増減率)",
    "営業収益 営業利益 税引前利益 親会社の所有者に",
    "帰属する当期利益",
    "基本的1株当たり",
    "親会社の所有者に",
    "帰属する当期利益",
    "百万円 % 百万円 % 百万円 % 百万円 % 円 銭",
    "通期 50,000,000 4.1 3,800,000 △20.8 5,020,000 △21.7 3,570,000 △25.1 273.91",
]
QUARTERLY_REFERENCE_ONLY = [  # エクシオグループ：表を載せず修正の開示を参照
    "3.2026年3月期の連結業績予想(2025年4月1日~2026年3月31日)",
    "(注)直近に公表されている業績予想からの修正の有無:有",
    "連結業績予想の修正については、本日(2026年2月6日)公表いたしました「通期業績予想の修正に関するお知らせ」を",
]
REVISION = [  # エクシオグループ 通期業績予想の修正に関するお知らせ
    "通期業績予想の修正に関するお知らせ",
    "1.2026 年 3 月期 通期連結業績予想数値の修正(2025 年4月1日~2026 年3月 31 日)",
    "売上高 営業利益 経常利益",
    "親会社株主",
    "に帰属する",
    "当期純利益",
    "1株当たり",
    "当期純利益",
    "百万円 百万円 百万円 百万円 円 銭",
    "前回発表予想(A) 710,000 47,000 48,000 30,000 146.33",
    "今回発表予想(B) 760,000 53,000 54,000 32,000 155.86",
    "増減額(B-A) 50,000 6,000 6,000 2,000",
]


def test_quarterly_full_year_row_jgaap():
    result = forecast.parse_quarterly([QUARTERLY_JGAAP])
    assert result["period"] == "2026年3月期" and result["scope"] == "連結"
    assert result["values"]["ordinary_profit"] == pytest.approx(226_000e6)
    assert result["values"]["operating_profit"] == pytest.approx(228_000e6)


def test_quarterly_ifrs_has_pretax_profit():
    values = forecast.parse_quarterly([QUARTERLY_IFRS])["values"]
    assert values["pretax_profit"] == pytest.approx(5_020_000e6)
    assert "ordinary_profit" not in values


def test_quarterly_without_table_is_not_found():
    assert forecast.parse_quarterly([QUARTERLY_REFERENCE_ONLY]) is None


def test_revision_uses_new_forecast_row():
    result = forecast.parse_revision([REVISION])
    assert result["period"] == "2026年3月期"
    assert result["values"]["ordinary_profit"] == pytest.approx(54_000e6)  # (A) ではなく (B)


def test_range_forecast_uses_midpoint():
    lines = QUARTERLY_JGAAP[:-2] + ["通 期 3,030,000 4.1 228,000 50.1 220,000～230,000 40.7 170,000 35.1 364.11"]
    assert forecast.parse_quarterly([lines])["values"]["ordinary_profit"] == pytest.approx(225_000e6)


@dataclass
class _Disclosure:
    category: str
    title: str
    disclosed_on: date


def test_select_documents_takes_latest_quarter_and_later_revisions():
    disclosures = [
        _Disclosure("四半期", "2026年3月期 第2四半期（中間期）決算短信〔日本基準〕(連結)", date(2025, 11, 11)),
        _Disclosure("業績", "業績予想及び配当予想の修正に関するお知らせ", date(2025, 11, 11)),
        _Disclosure("四半期", "2026年3月期 第3四半期決算短信〔日本基準〕(連結)", date(2026, 2, 12)),
        _Disclosure("四半期", "2025年度(2026年３月期)第３四半期決算補足資料", date(2026, 2, 12)),
        _Disclosure("業績", "業績予想の修正に関するお知らせ", date(2026, 2, 12)),
        _Disclosure("業績", "剰余金の配当に関するお知らせ", date(2026, 3, 2)),
        _Disclosure("短信", "2026年3月期決算短信〔日本基準〕(連結)", date(2026, 5, 14)),
        _Disclosure("四半期", "2027年3月期 第1四半期決算短信〔日本基準〕(連結)", date(2026, 8, 5)),
    ]
    chosen = forecast.select_documents(disclosures, date(2026, 5, 14))
    assert [(d.disclosed_on, d.category) for d in chosen] == [(date(2026, 2, 12), "業績"), (date(2026, 2, 12), "四半期")]


def test_table_printed_before_heading_is_still_found():
    # ショーボンドHD：PDFの文字の並びで、表が見出しより前に出てくる
    lines = [
        "売上高 営業利益 経常利益 親会社株主に帰属",
        "する当期純利益",
        "百万円 % 百万円 % 百万円 % 百万円 % 円 銭",
        "通期 91,000 0.3 21,000 1.0 21,500 1.7 15,300 1.6 75.40",
        "3.2026年6月期の連結業績予想(2025年7月1日~2026年6月30日)",
        "(注)1. 直近に公表されている業績予想からの修正の有無:無",
    ]
    result = forecast.parse_quarterly([lines])
    assert result["period"] == "2026年6月期"
    assert result["values"]["ordinary_profit"] == pytest.approx(21_500e6)


def test_revision_with_cells_on_separate_lines():
    # 極洋 業績予想の修正：表の各セルが1行ずつ並ぶ
    lines = [
        "業績予想の修正に関するお知らせ",
        "1.2026 年3月期通期連結業績予想数値の修正(2025 年 4 月 1 日~2026 年 3 月 31 日)",
        "売上高 営業利益 経常利益 親会社株主に帰属",
        "する当期純利益",
        "1 株当たり",
        "当期純利益",
        "前回発表予想(A)",
        "百万円", "350,000", "百万円", "12,500", "百万円", "12,000", "百万円", "8,500", "円 銭", "653.10",
        "今回発表予想(B)",
        "350,000", "11,100", "10,300", "7,600", "584.04",
        "増減額(B-A)", "－", "△1,400", "△1,700", "△900",
    ]
    result = forecast.parse_revision([lines])
    assert result["period"] == "2026年3月期"
    assert result["values"]["operating_profit"] == pytest.approx(11_100e6)
    assert result["values"]["ordinary_profit"] == pytest.approx(10_300e6)
