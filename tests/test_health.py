"""
Tests for financial health score calculation.
Scenario: income 5000, est_monthly_expense 2400, no transactions, no goals.
Expected: score = 30(savings) + 25(essential<=50%) + 20(discretionary<=20%) + 4(buffer<1mo) = 79

Low-data path: total_monthly_expense=2400 split 70/30 → essential=1680, discretionary=720
savings_rate = (5000-2400)/5000 = 52% → >=30% → score 30
essential_ratio = 1680/5000 = 33.6% → <=50% → score 25
discretionary_ratio = 720/5000 = 14.4% → <=20% → score 20
emergency_savings = 0 (no goals) → eval_savings=0 → runway=0 → score 4
total = 79
"""
import pytest
from app.services.health_score import calculate_financial_health_score


def test_health_score_no_transactions():
    """income 5000, declared expense 2400, no transactions → expects score 79."""
    # Low-data path values (70/30 split of 2400)
    essential = round(2400 * 0.70, 2)    # 1680
    discretionary = round(2400 * 0.30, 2)  # 720
    result = calculate_financial_health_score(
        monthly_income=5000,
        total_monthly_expense=2400,
        essential_expense=essential,
        discretionary_expense=discretionary,
        current_savings=0.0,
        emergency_fund_savings=0.0,
        emergency_target_months=6
    )
    assert result["overall_score"] == 79, f"Expected 79, got {result['overall_score']}. Breakdown: {result['breakdown']}"
    assert result["needs_setup"] is False
    assert result["provisional"] is False


def test_health_score_breakdown():
    essential = round(2400 * 0.70, 2)
    discretionary = round(2400 * 0.30, 2)
    result = calculate_financial_health_score(
        monthly_income=5000,
        total_monthly_expense=2400,
        essential_expense=essential,
        discretionary_expense=discretionary,
        current_savings=0.0,
        emergency_fund_savings=0.0,
        emergency_target_months=6
    )
    bd = result["breakdown"]
    assert bd["savings_ratio"]["score"] == 30.0, f"savings score: {bd['savings_ratio']['score']}"
    assert bd["essential_adherence"]["score"] == 25.0, f"essential score: {bd['essential_adherence']['score']}"
    assert bd["discretionary_control"]["score"] == 20.0, f"discretionary score: {bd['discretionary_control']['score']}"
    assert bd["emergency_buffer"]["score"] == 4.0, f"buffer score: {bd['emergency_buffer']['score']}"


def test_health_score_needs_setup():
    result = calculate_financial_health_score(
        monthly_income=0,
        total_monthly_expense=0,
        essential_expense=0,
        discretionary_expense=0,
        current_savings=0.0
    )
    assert result["needs_setup"] is True
    assert result["overall_score"] == 0


def test_health_score_negative_savings():
    """Expenses > income → negative savings_rate → score 0 for savings but no exception."""
    result = calculate_financial_health_score(
        monthly_income=3000,
        total_monthly_expense=4000,
        essential_expense=3000,
        discretionary_expense=1000,
        current_savings=0.0,
        emergency_fund_savings=0.0
    )
    assert result["savings_rate_pct"] < 0
    assert result["breakdown"]["savings_ratio"]["score"] == 0.0
