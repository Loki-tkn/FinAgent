import pytest
from app.services.health_score import calculate_financial_health_score
from app.services.planner import generate_goal_plans
from app.services.simulator import simulate_financial_decision
from app.services.anomaly import detect_spending_anomalies
from app.database import init_db

def setup_module():
    """Ensure clean test database is initialized."""
    init_db(force_reseed=True)

def test_financial_health_score_calculation():
    score_excellent = calculate_financial_health_score(
        monthly_income=5000,
        total_monthly_expense=2500,
        essential_expense=1800,
        discretionary_expense=700,
        current_savings=20000,
        emergency_fund_savings=18000
    )
    assert score_excellent["overall_score"] >= 85
    assert score_excellent["rating"] == "Excellent"
    assert score_excellent["grade"] == "A"
    assert score_excellent["savings_rate_pct"] == 50.0

    score_risk = calculate_financial_health_score(
        monthly_income=2500,
        total_monthly_expense=2800,
        essential_expense=2300,
        discretionary_expense=500,
        current_savings=500,
        emergency_fund_savings=500
    )
    assert score_risk["overall_score"] <= 50
    assert score_risk["rating"] == "At Risk"

def test_goal_planner_three_tiers():
    res = generate_goal_plans(
        target_amount=18000,
        current_savings=6500,
        target_date_str="2027-10-01",
        monthly_income=5000,
        user_id=1,
        goal_title="Test Emergency Fund"
    )
    assert "plans" in res
    assert len(res["plans"]) == 3
    plan_names = [p["plan_name"] for p in res["plans"]]
    assert "Conservative" in plan_names
    assert "Balanced" in plan_names
    assert "Aggressive" in plan_names

    aggr_plan = next(p for p in res["plans"] if p["plan_name"] == "Aggressive")
    assert aggr_plan["feasibility"] in ["Easy", "Moderate", "Challenging", "Extreme"]

def test_spending_anomaly_detection():
    res = detect_spending_anomalies(user_id=1, threshold_z=2.0)
    assert res["total_scanned"] > 0
    assert res["anomalies_detected"] >= 2
    
    ipad_anomaly = next((a for a in res["anomalies"] if "iPad" in a["description"] or a["amount"] >= 1000), None)
    assert ipad_anomaly is not None
    assert ipad_anomaly["z_score"] >= 2.0
    assert ipad_anomaly["severity"] in ["HIGH", "MEDIUM"]

def test_what_if_simulation_zero_savings_and_purchase_delay():
    # Test with current_savings = 0
    sim_zero = simulate_financial_decision(
        scenario_type="one_off_purchase",
        amount=1000,
        description="Test purchase with 0 savings",
        user_id=1
    )
    assert sim_zero["scenario_type"] == "one_off_purchase"
    assert sim_zero["impact_level"] in ["SEVERE", "MODERATE", "NEGLIGIBLE"]

    # Test negative amount for income change
    sim_neg = simulate_financial_decision(
        scenario_type="income_change",
        amount=-500,
        description="Salary pay cut",
        user_id=1
    )
    assert sim_neg["delay_or_advance_months"] > 0
    assert sim_neg["impact_level"] == "SEVERE"
