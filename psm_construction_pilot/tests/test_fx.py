"""株価リターンのドル建て換算とドル円レートの検証。"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from core import car, fx


def test_yen_weakening_lowers_dollar_return():
    # 株価が円で +10%、同じ日に円安で 1ドル 100円 → 110円 なら、ドルで見たリターンは 0
    assert fx.to_usd_return(0.10, 100.0, 110.0) == pytest.approx(0.0)
    # 株価が動かず円高（110円 → 100円）なら、ドルで見ると +10%
    assert fx.to_usd_return(0.0, 110.0, 100.0) == pytest.approx(0.10)


def test_daily_excess_returns_converts_before_subtracting_rf():
    dates = pd.bdate_range("2026-05-11", periods=3)
    close = pd.Series([1000.0, 1100.0, 1100.0], index=dates)
    rf = pd.Series(0.0001, index=dates)
    usdjpy = pd.Series([150.0, 165.0, 150.0], index=dates)
    excess, _ = car.daily_excess_returns(close, dates, rf, usdjpy)
    assert excess.iloc[0] == pytest.approx(1.10 * 150 / 165 - 1 - 0.0001)  # 株価+10%、円安10%
    assert excess.iloc[1] == pytest.approx(1.00 * 165 / 150 - 1 - 0.0001)  # 株価そのまま、円高
    unconverted, _ = car.daily_excess_returns(close, dates, rf)
    assert unconverted.iloc[0] == pytest.approx(0.10 - 0.0001)


def test_rate_on_missing_day_uses_previous_value():
    dates = pd.bdate_range("2026-05-11", periods=3)
    close = pd.Series([100.0, 100.0, 100.0], index=dates)
    usdjpy = pd.Series([150.0, 160.0], index=[dates[0], dates[2]])  # 2日目のレートがない
    excess, _ = car.daily_excess_returns(close, dates, pd.Series(0.0, index=dates), usdjpy)
    assert excess.loc[dates[1]] == pytest.approx(0.0)  # 前の日のレート(150)で代用 → 変化なし
    assert excess.loc[dates[2]] == pytest.approx(150 / 160 - 1)


def test_validate_flags_gaps_and_implausible_values():
    rates = pd.Series([150.0, 151.0, 1.5], index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-03-01"]))
    problems = fx.validate(rates, date(2026, 1, 5), date(2026, 3, 1))
    assert any("範囲外" in p for p in problems)
    assert any("空白" in p for p in problems)


def test_rates_are_aligned_one_label_later():
    labels = pd.to_datetime(["2026-05-11", "2026-05-12", "2026-05-13"])
    aligned = fx.align_to_tokyo(pd.Series([150.0, 151.0, 152.0], index=labels))
    assert aligned.loc["2026-05-11"] == 151.0  # 東京の 5/11 には、ラベル 5/12 のレートを使う
    assert aligned.loc["2026-05-12"] == 152.0
    assert "2026-05-13" not in aligned.index.strftime("%Y-%m-%d")
