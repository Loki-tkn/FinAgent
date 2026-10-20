from typing import Dict, Any

def calculate_financial_health_score(
    monthly_income: float,
    total_monthly_expense: float,
    essential_expense: float,
    discretionary_expense: float,
    current_savings: float,
    emergency_fund_savings: float = None,
    emergency_target_months: int = 6
) -> Dict[str, Any]:
    """
    Computes a comprehensive 0-100 Financial Health Score based on 4 key financial pillars:
    1. Savings Ratio (30 pts)
    2. Essential Needs Adherence (25 pts)
    3. Discretionary Control (20 pts)
    4. Emergency Buffer Runway (25 pts)
    """
    if monthly_income <= 0:
        return {
            "overall_score": 0,
            "rating": "Setup Required",
            "grade": "-",
            "needs_setup": True,
            "breakdown": {
                "savings_ratio": {"score": 0, "max": 30, "detail": "Add monthly income to evaluate."},
                "essential_adherence": {"score": 0, "max": 25, "detail": "Add expense details to evaluate."},
                "discretionary_control": {"score": 0, "max": 20, "detail": "Add expense details to evaluate."},
                "emergency_buffer": {"score": 0, "max": 25, "detail": "Add savings goals to evaluate."}
            },
            "summary_recommendation": "Complete your profile setup to generate a financial health diagnostic."
        }

    monthly_savings = monthly_income - total_monthly_expense
    savings_rate = (monthly_savings / monthly_income) * 100.0  # can be negative
    savings_rate_for_score = max(0.0, savings_rate)
    essential_ratio = (essential_expense / monthly_income) * 100.0
    discretionary_ratio = (discretionary_expense / monthly_income) * 100.0

    # 1. Savings Ratio (30 pts) — scored on non-negative rate
    if savings_rate_for_score >= 30.0:
        savings_score = 30.0
    elif savings_rate_for_score >= 20.0:
        savings_score = 25.0
    elif savings_rate_for_score >= 10.0:
        savings_score = 18.0
    elif savings_rate_for_score > 0.0:
        savings_score = 10.0
    else:
        savings_score = 0.0

    # 2. Essential Spending vs Income (25 pts)
    if essential_ratio <= 50.0:
        essential_score = 25.0
    elif essential_ratio <= 60.0:
        essential_score = 20.0
    elif essential_ratio <= 75.0:
        essential_score = 12.0
    else:
        essential_score = 5.0

    # 3. Discretionary Control (20 pts)
    if discretionary_ratio <= 20.0:
        discretionary_score = 20.0
    elif discretionary_ratio <= 30.0:
        discretionary_score = 15.0
    elif discretionary_ratio <= 40.0:
        discretionary_score = 10.0
    else:
        discretionary_score = 3.0

    # 4. Emergency Buffer Runway in Months (25 pts)
    # Only use emergency_fund_savings if explicitly > 0; else use 0 (not current_savings fallback)
    eval_savings = emergency_fund_savings if (emergency_fund_savings is not None and emergency_fund_savings > 0) else 0.0
    runway_months = (eval_savings / total_monthly_expense) if total_monthly_expense > 0 else 0
    
    target_months = max(1, emergency_target_months)
    if runway_months >= target_months:
        buffer_score = 25.0
    elif runway_months >= target_months * 0.5:
        buffer_score = 20.0
    elif runway_months >= 1.0:
        buffer_score = 12.0
    else:
        buffer_score = 4.0

    total_score = round(savings_score + essential_score + discretionary_score + buffer_score)

    if total_score >= 85:
        rating = "Excellent"
        grade = "A"
        advice = "Your financial health is outstanding! You have a robust safety buffer and disciplined spending."
    elif total_score >= 70:
        rating = "Good"
        grade = "B"
        advice = "Healthy financial foundation. Trimming discretionary spending can elevate your savings rate to 30%+."
    elif total_score >= 50:
        rating = "Fair"
        grade = "C"
        advice = "Moderate financial posture. Essential and non-essential expenses consume most cash flow. Consider optimizing your budget."
    else:
        rating = "At Risk"
        grade = "D"
        advice = "High financial vulnerability. Spending exceeds safe limits or emergency buffer is low. Budget reduction recommended."

    return {
        "overall_score": total_score,
        "rating": rating,
        "grade": grade,
        "needs_setup": False,
        "provisional": False,
        "savings_rate_pct": round(savings_rate, 1),
        "runway_months": round(runway_months, 1),
        "breakdown": {
            "savings_ratio": {
                "score": savings_score,
                "max": 30,
                "value_pct": round(savings_rate, 1),
                "detail": f"Savings rate is {savings_rate:.1f}% (Benchmark: ≥20-30%)"
            },
            "essential_adherence": {
                "score": essential_score,
                "max": 25,
                "value_pct": round(essential_ratio, 1),
                "detail": f"Needs take {essential_ratio:.1f}% of income (Benchmark: ≤50%)"
            },
            "discretionary_control": {
                "score": discretionary_score,
                "max": 20,
                "value_pct": round(discretionary_ratio, 1),
                "detail": f"Wants take {discretionary_ratio:.1f}% of income (Benchmark: ≤20-30%)"
            },
            "emergency_buffer": {
                "score": buffer_score,
                "max": 25,
                "value_months": round(runway_months, 1),
                "detail": f"Emergency fund covers {runway_months:.1f} months of living expenses (Target: ≥{target_months} months)"
            }
        },
        "summary_recommendation": advice
    }
