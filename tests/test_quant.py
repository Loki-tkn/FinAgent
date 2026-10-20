"""6 quick asserts for quant service functions."""
from app.services.quant import (
    required_monthly_payment, real_target, robust_anomalies,
    monte_carlo_goal, loan_vs_cash,
)

def test_pmt_zero_return():
    result = required_monthly_payment(12000.0, 0.0, 12, annual_return=0.0)
    assert abs(result - 1000.0) < 1e-6

def test_mc_prob_and_percentile_ordering():
    res = monte_carlo_goal(5000.0, 20000.0, 24, [600.0, 700.0, 650.0], 0.05, 0.10)
    assert 0.0 <= res["prob_success"] <= 1.0
    assert res["p10"] <= res["p50"] <= res["p90"]

def test_real_target_inflation_adjusted():
    assert real_target(10000.0, 0.03, 12) >= 10000.0

def test_loan_vs_cash_returns_better():
    res = loan_vs_cash(25000.0, 5000.0, 0.06, 36, 0.05)
    assert res["better"] in ("loan", "cash")
    assert "monthly_payment" in res

def test_robust_anomalies_flags_outlier():
    amounts = [100.0, 110.0, 95.0, 105.0, 98.0, 1050.0]
    z_scores = robust_anomalies(amounts)
    assert len(z_scores) == 6
    assert z_scores[-1] > 3.5

def test_mc_empty_history_no_crash():
    res = monte_carlo_goal(1000.0, 5000.0, 12, [], 0.05, 0.10)
    assert "prob_success" in res
    assert 0.0 <= res["prob_success"] <= 1.0
