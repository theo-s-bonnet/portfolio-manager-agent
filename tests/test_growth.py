"""3-year growth measures: revenue CAGR (R4.3) and diluted EPS growth (score), their annual
histories and their edge cases (data guards R4.11 and R4.12)."""

import numpy as np
import pandas as pd
import pytest

from helpers import DATE, RATES, config, tv_stock, universe
from investment_agent.strategies.higgons import filter_tv
from investment_agent.strategies.higgons.filters import (
    DENOMINATOR, HISTORY_MISSING, REVENUE_N_NOT_POSITIVE, eps_growth, growth_years, history_values,
    revenue_cagr)

CFG = config()
TOL = CFG["technical"]["comparison_tolerance"]
YEARS = 3
nan = float("nan")


def tv(**changes):
    return filter_tv(universe(tv_stock(**changes)), CFG, RATES, DATE).iloc[0]


# --- pure functions ---------------------------------------------------------------------------

def test_growth_years_from_the_configuration():
    assert growth_years(CFG) == YEARS
    for wrong in (0, 2.5, True, "3"):
        with pytest.raises(ValueError, match="growth_years"):
            growth_years({"filters": {"growth_years": wrong}})


def test_history_values_last_closed_year_first():
    assert history_values([4, 3, 2, 1, 0], YEARS) == [4.0, 3.0, 2.0, 1.0]       # N-4 ignored
    assert history_values(np.array([4.0, 3.0]), YEARS) == [4.0, 3.0, None, None]
    assert history_values([4, None, nan, 1], YEARS) == [4.0, None, None, 1.0]
    for not_a_list in (None, nan, "4 | 3 | 2 | 1"):
        assert history_values(not_a_list, YEARS) == [None] * 4


def test_revenue_cagr():
    value, issue = revenue_cagr([1.331e9, 1.21e9, 1.1e9, 1.0e9], YEARS, TOL)
    assert issue is None and value == pytest.approx(10.0)
    value, _ = revenue_cagr([0.729, 0.81, 0.9, 1.0], YEARS, TOL)                 # decline
    assert value == pytest.approx(-10.0)


def test_revenue_cagr_uses_element_0_and_element_3():
    # element 0 = N (last closed fiscal year), element 3 = N-3; N-1, N-2 and N-4 do not matter
    a, _ = revenue_cagr([8.0, 1.0, 99.0, 1.0, 50.0], YEARS, TOL)
    assert a == pytest.approx(100.0)                                            # 8 ^ (1/3) = 2


def test_revenue_cagr_edge_cases():
    assert revenue_cagr([1.1, 1.0, 0.9], YEARS, TOL) == (None, HISTORY_MISSING)  # 3 fiscal years
    assert revenue_cagr([1.1, None, 0.9, 0.8], YEARS, TOL) == (None, HISTORY_MISSING)
    assert revenue_cagr(None, YEARS, TOL) == (None, HISTORY_MISSING)
    assert revenue_cagr([1.0, 0.9, 0.5, 0.0], YEARS, TOL) == (None, DENOMINATOR)  # N-3 = 0
    assert revenue_cagr([-1.0, 0.9, 0.5, -0.2], YEARS, TOL) == (None, DENOMINATOR)
    assert revenue_cagr([-5.0, 0.9, 0.5, 1.0], YEARS, TOL) == (None, REVENUE_N_NOT_POSITIVE)
    assert revenue_cagr([0.0, 0.9, 0.5, 1.0], YEARS, TOL) == (None, REVENUE_N_NOT_POSITIVE)


def test_eps_growth_slope_over_mean_absolute_eps():
    # oldest first 1, 2, 3, 4: slope 1, mean |EPS| 2.5 -> 40%
    value, issue = eps_growth([4.0, 3.0, 2.0, 1.0], YEARS, TOL)
    assert issue is None and value == pytest.approx(40.0)
    value, _ = eps_growth([1.0, 2.0, 3.0, 4.0], YEARS, TOL)
    assert value == pytest.approx(-40.0)
    assert eps_growth([2.0, 2.0, 2.0, 2.0, 9.0], YEARS, TOL) == (0.0, None)     # N-4 ignored


def test_eps_growth_turnaround_with_negative_eps():
    # oldest first -2, -1, 1, 2: slope 1.4, mean |EPS| 1.5 -> 93.33%
    value, issue = eps_growth([2.0, 1.0, -1.0, -2.0], YEARS, TOL)
    assert issue is None and value == pytest.approx(1.4 / 1.5 * 100)


def test_eps_growth_matches_an_independent_regression():
    rng = np.random.default_rng(7)
    for _ in range(50):
        eps = rng.normal(1.0, 2.0, YEARS + 1)
        oldest_first = eps[::-1]
        slope = np.polyfit(np.arange(YEARS + 1), oldest_first, 1)[0]
        expected = slope / np.mean(np.abs(oldest_first)) * 100
        assert eps_growth(list(eps), YEARS, TOL)[0] == pytest.approx(expected, rel=1e-9, abs=1e-9)


def test_eps_growth_edge_cases():
    assert eps_growth([0.0, 0.0, 0.0, 0.0], YEARS, TOL) == (None, DENOMINATOR)
    assert eps_growth([1.0, 0.5, nan, 0.2], YEARS, TOL) == (None, HISTORY_MISSING)
    assert eps_growth([1.0, 0.5], YEARS, TOL) == (None, HISTORY_MISSING)


# --- derived columns ------------------------------------------------------------------------

def test_derived_columns_for_the_detail_csv():
    row = tv()
    assert [row[f"revenue_fy_{k}"] for k in range(4)] == [1.331e9, 1.21e9, 1.1e9, 1.0e9]
    assert [row[f"eps_fy_{k}"] for k in range(4)] == [6.25, 5.5, 5.0, 4.5]
    assert row["revenue_cagr_3y"] == pytest.approx(10.0)
    assert row["eps_growth_3y"] == pytest.approx(0.575 / 5.3125 * 100)
    assert "revenue_fy_h" not in row.index and "eps_fy_h" not in row.index   # raw lists replaced


def test_missing_measure_is_nan_never_zero():
    row = tv(revenue_fy_h=None, eps_fy_h=[1.0, 2.0])
    assert pd.isna(row["revenue_cagr_3y"]) and pd.isna(row["eps_growth_3y"])
    assert pd.isna(row["revenue_fy_0"]) and pd.isna(row["eps_fy_3"])


# --- R4.3: 5-year and 3-year revenue CAGR ---------------------------------------------------

def test_flat_revenue_over_3_years_passes():
    row = tv(revenue_fy_h=[1.0e9, 0.9e9, 1.1e9, 1.0e9])
    assert row["revenue_cagr_3y"] == 0.0 and row["R4.3"] == "pass"


def test_3_year_revenue_decline_fails_with_both_values():
    row = tv(revenue_fy_h=[0.729e9, 0.81e9, 0.9e9, 1.0e9])
    assert row["R4.3"] == "fail"
    assert row["reason_R4.3"] == "5-year revenue CAGR 10% ≥ 5%; 3-year revenue CAGR -10% < 0%"


def test_5_year_failure_shows_both_values():
    row = tv(revenue_cagr_5y=3.0)
    assert row["R4.3"] == "fail"
    assert row["reason_R4.3"] == "5-year revenue CAGR 3% < 5%; 3-year revenue CAGR 10% ≥ 0%"


def test_5_year_cagr_equal_to_the_minimum():
    assert tv(revenue_cagr_5y=float(CFG["filters"]["revenue_cagr_5y_min_pct"]))["R4.3"] == "pass"


def test_negative_revenue_of_n_fails():
    row = tv(revenue_fy_h=[-5.0e6, 0.9e9, 1.1e9, 1.0e9])
    assert row["R4.3"] == "fail"
    assert row["reason_R4.3"] == "5-year revenue CAGR 10% ≥ 5%; 3-year revenue CAGR undefined: revenue N -5,000,000 ≤ 0"
    assert row["R4.12"] == "pass" and row["R4.11"] == "pass"


def test_5_year_cagr_missing():
    row = tv(revenue_cagr_5y=nan)
    assert row["R4.3"] == "missing" and row["reason_R4.3"] == "5-year revenue CAGR missing"
    row = tv(revenue_cagr_5y=nan, revenue_fy_h=[0.729e9, 0.81e9, 0.9e9, 1.0e9])
    assert row["R4.3"] == "fail"                       # a known failure wins over missing data


# --- edge cases attached to the data guards -----------------------------------------------------

def test_fewer_than_4_fiscal_years_of_revenue():
    row = tv(revenue_fy_h=[1.2e9, 1.1e9, 1.0e9])
    assert row["R4.3"] == "pass"
    assert row["reason_R4.3"] == "3-year revenue CAGR not computed: annual revenue missing (R4.11)"
    assert row["R4.11"] == "missing" and row["reason_R4.11"] == "annual revenue missing: N-3"


def test_revenue_value_missing_among_the_4():
    row = tv(revenue_fy_h=[1.2e9, None, 1.0e9, 0.9e9])
    assert row["R4.11"] == "missing" and row["reason_R4.11"] == "annual revenue missing: N-1"


def test_eps_history_missing():
    row = tv(eps_fy_h=None)
    assert row["R4.11"] == "missing"
    assert row["reason_R4.11"] == "annual diluted EPS missing: N, N-1, N-2, N-3"
    assert row["R4.12"] == "pass"


def test_roe_and_history_missing_reported_together():
    row = tv(roe_ttm=nan, shareholders_equity=nan, eps_fy_h=[1.0, 0.9, 0.8])
    assert row["reason_R4.11"] == ("ROE missing; fallback not computed: shareholders' equity / TTM net "
                                   "income missing ; annual diluted EPS missing: N-3")


def test_revenue_of_n_minus_3_not_positive():
    row = tv(revenue_fy_h=[1.0e9, 0.5e9, 0.1e9, 0.0])
    assert row["R4.3"] == "pass"
    assert row["reason_R4.3"] == "3-year revenue CAGR not computed: revenue N-3 ≤ 0 (R4.12)"
    assert row["R4.11"] == "pass"
    assert row["R4.12"] == "fail"
    assert row["reason_R4.12"] == "revenue N-3 0 ≤ 0: 3-year revenue CAGR not computed"


def test_mean_absolute_eps_zero():
    row = tv(eps_fy_h=[0.0, 0.0, 0.0, 0.0])
    assert row["R4.11"] == "pass"
    assert row["R4.12"] == "fail"
    assert row["reason_R4.12"] == "mean absolute EPS of N-3 to N is 0: EPS growth not computed"


def test_negative_eps_allowed_in_the_history():
    row = tv(eps_fy_h=[2.0, 1.0, -1.0, -2.0])
    assert row["eps_growth_3y"] == pytest.approx(1.4 / 1.5 * 100)
    assert row["R4.11"] == "pass" and row["R4.12"] == "pass"


def test_history_guards_counted_after_the_criteria():
    from helpers import fake_result

    res = fake_result([tv_stock(), tv_stock(tv_ticker="XETR:NOH", yahoo_ticker="NOH.DE", eps_fy_h=None),
                       tv_stock(tv_ticker="XETR:ZER", yahoo_ticker="ZER.DE", revenue_fy_h=[1.0, 1.0, 1.0, 0.0])])
    df = res.universe.set_index("tv_ticker")
    assert df.loc["XETR:NOH", "first_failed_rule"] == "R4.11"
    assert df.loc["XETR:ZER", "first_failed_rule"] == "R4.12"
    assert res.ranking["tv_ticker"].tolist() == ["XETR:TEST"]
