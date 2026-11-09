"""推定部分（段階5〜9）のテスト。合成データで既知の値を復元できるかを確認する。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core import att, balance, car, evaluate, matcher


def test_five_factor_regression_recovers_known_alpha_and_betas():
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2020-01-01", periods=400)
    X = pd.DataFrame(rng.normal(0, 0.01, size=(len(dates), 5)), index=dates,
                     columns=["MKT", "SMB", "HML", "RMW", "CMA"])
    true_alpha, true_beta = 0.0005, np.array([1.1, 0.3, -0.2, 0.1, 0.4])
    excess = pd.Series(true_alpha + X.to_numpy() @ true_beta + rng.normal(0, 1e-4, len(dates)), index=dates)
    announce = dates[300]
    result = car.abnormal_car(excess, dates, announce, X, {"w": (-1, 1)}, include_alpha=True, min_obs=120)
    assert result["alpha"] == pytest.approx(true_alpha, abs=1e-4)
    assert np.allclose(result["betas"], true_beta, atol=0.02)
    assert result["r2"] > 0.99


def test_known_event_effect_is_detected():
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2020-01-01", periods=400)
    X = pd.DataFrame(rng.normal(0, 0.01, size=(len(dates), 5)), index=dates,
                     columns=["MKT", "SMB", "HML", "RMW", "CMA"])
    excess = pd.Series(X.to_numpy() @ np.array([1.0, 0, 0, 0, 0]) + rng.normal(0, 1e-4, len(dates)), index=dates)
    announce = dates[300]
    excess.iloc[300:303] += 0.02  # 発表日から3日間、+2%の異常収益
    result = car.abnormal_car(excess, dates, announce, X, {"w": (0, 2), "placebo": (-15, -11)},
                              include_alpha=True, min_obs=120)
    assert result["w"] == pytest.approx(0.06, abs=0.01)
    assert abs(result["placebo"]) < 0.01


def test_estimation_below_min_obs_is_nan():
    dates = pd.bdate_range("2020-01-01", periods=400)
    X = pd.DataFrame(np.random.default_rng(2).normal(size=(400, 5)), index=dates, columns=list("abcde"))
    excess = pd.Series(np.random.default_rng(3).normal(size=400), index=dates).iloc[:200]
    result = car.abnormal_car(excess, dates, dates[300], X, {"w": (-1, 1)}, include_alpha=True, min_obs=120)
    assert "w" not in result or np.isnan(result.get("w", np.nan))


def test_caliper_is_never_exceeded_and_no_reuse_without_replacement():
    lp = pd.Series(np.linspace(-2, 2, 30), index=[f"c{i}" for i in range(30)])
    treated = pd.Series(False, index=lp.index)
    treated.iloc[::3] = True
    caliper = 0.15
    pairs, dropped = matcher.match(lp, treated, replacement=False, caliper=caliper, seed=7)
    assert (pairs["distance"] <= caliper).all()
    assert pairs["control"].is_unique
    assert len(pairs) + len(dropped) == int(treated.sum())


def test_replacement_allows_reuse_of_controls():
    lp = pd.Series([0.0, 0.01, 0.02, 0.03, 5.0], index=["t1", "t2", "t3", "c1", "c2"])
    treated = pd.Series([True, True, True, False, False], index=lp.index)
    pairs, dropped = matcher.match(lp, treated, replacement=True, caliper=0.1, seed=0)
    assert len(pairs) == 3 and not dropped
    assert pairs["control"].nunique() == 1  # c1 が繰り返し使われる


def test_smd_matches_hand_computation():
    treated = pd.Series([1.0, 2.0, 3.0])
    control = pd.Series([0.0, 1.0, 2.0])
    expected = (2.0 - 1.0) / np.sqrt((treated.var(ddof=1) + control.var(ddof=1)) / 2)
    assert balance.smd(treated, control) == pytest.approx(expected)


def test_att_matches_mean_of_pair_differences_and_bootstrap_covers_it():
    diffs = np.array([0.02, -0.01, 0.03, 0.00, 0.04, 0.01])
    summary = att.summarize(diffs, reps=2000, seed=0)
    assert summary["att"] == pytest.approx(diffs.mean())
    low, high = summary["ci95"]
    assert low < diffs.mean() < high


def test_rosenbaum_gamma_one_agrees_with_one_sided_wilcoxon():
    from scipy import stats

    diffs = np.array([0.5, -0.2, 0.9, 0.3, 1.2, -0.1, 0.7, 0.4, 0.8, 0.6])
    bounds = evaluate.rosenbaum_bounds(diffs, [1.0])[0]
    exact = stats.wilcoxon(diffs, alternative="greater").pvalue
    assert bounds["p_upper"] == pytest.approx(exact, abs=0.05)
    assert bounds["p_lower"] == pytest.approx(exact, abs=0.05)


def test_rosenbaum_bounds_widen_with_gamma():
    diffs = np.array([0.5, 0.2, 0.9, 0.3, 1.2, 0.1, 0.7, 0.4, 0.8, 0.6])
    rows = evaluate.rosenbaum_bounds(diffs, [1.0, 2.0, 3.0])
    uppers = [r["p_upper"] for r in rows]
    lowers = [r["p_lower"] for r in rows]
    assert uppers == sorted(uppers) and lowers == sorted(lowers, reverse=True)
