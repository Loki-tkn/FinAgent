from typing import Dict, Any, Optional
from app.database import get_db_connection
from app.services.metrics import monthly_avg_expense

def simulate_financial_decision(
    scenario_type: str,
    amount: float,
    description: str = "Simulated Event",
    frequency: str = "one_time",
    goal_id: Optional[int] = None,
    user_id: int = 1
) -> Dict[str, Any]:
    """
    Simulates the financial impact of purchase decisions or income/expense shifts on savings goals.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT monthly_income FROM users WHERE id = ?", (user_id,))
    user_row = cursor.fetchone()
    monthly_income = float(user_row["monthly_income"]) if user_row and user_row["monthly_income"] else 0.0

    if goal_id:
        cursor.execute("SELECT id, title, target_amount, current_savings, monthly_contribution FROM goals WHERE id = ?", (goal_id,))
    else:
        cursor.execute("SELECT id, title, target_amount, current_savings, monthly_contribution FROM goals WHERE user_id = ? AND status = 'In Progress' ORDER BY id ASC LIMIT 1", (user_id,))
    goal_row = cursor.fetchone()
    conn.close()

    metrics = monthly_avg_expense(user_id=user_id, exclude_anomalies=True)
    total_expense = metrics["total"]
    is_low_data = metrics.get("low_data", False)

    if monthly_income <= 0:
        return {
            "scenario_type": scenario_type,
            "amount": amount,
            "description": description,
            "original_savings_rate": 0.0,
            "new_savings_rate": 0.0,
            "original_monthly_surplus": 0.0,
            "new_monthly_surplus": 0.0,
            "original_completion_months": None,
            "new_completion_months": None,
            "delay_or_advance_months": 0.0,
            "impact_level": "NEGLIGIBLE",
            "recommendation": "Please complete your profile setup (monthly income) to run simulations.",
            "needs_setup": True,
            "low_data": False
        }

    target_amount = float(goal_row["target_amount"]) if goal_row else 0.0
    current_savings = float(goal_row["current_savings"]) if goal_row else 0.0
    goal_title = goal_row["title"] if goal_row else "Savings Goal"

    orig_surplus = max(0.0, monthly_income - total_expense)
    orig_savings_rate = (orig_surplus / monthly_income * 100.0) if monthly_income > 0 else 0.0
    orig_remaining_goal = max(0.0, target_amount - current_savings)
    orig_months = (orig_remaining_goal / orig_surplus) if (orig_surplus > 0 and orig_remaining_goal > 0) else (0.0 if orig_remaining_goal == 0 else 999.0)

    new_income = monthly_income
    new_expense = total_expense
    new_savings = current_savings

    effective_scenario = scenario_type
    if scenario_type == "recurring_expense_change" and frequency == "one_time":
        effective_scenario = "one_off_purchase"

    if effective_scenario == "one_off_purchase":
        new_savings = max(0.0, current_savings - amount)
        new_remaining_goal = max(0.0, target_amount - new_savings)
        new_surplus = orig_surplus
        new_months = (new_remaining_goal / new_surplus) if (new_surplus > 0 and new_remaining_goal > 0) else (0.0 if new_remaining_goal == 0 else 999.0)
        delta_months = new_months - orig_months
        
        savings_ratio = (amount / current_savings) if current_savings > 0 else 1.0
        if savings_ratio > 0.5:
            impact_level = "SEVERE"
            pct_str = f"{savings_ratio*100:.0f}%" if current_savings > 0 else "100%"
            rec = f"High Risk: Spending ${amount:,.0f} drains {pct_str} of your saved buffer and delays '{goal_title}' by {delta_months:.1f} months."
        elif delta_months > 1.5:
            impact_level = "MODERATE"
            rec = f"Moderate Delay: This purchase delays your goal completion by {delta_months:.1f} months. Consider finding a cheaper alternative."
        else:
            impact_level = "NEGLIGIBLE"
            rec = f"Safe: Your existing surplus absorbs this purchase comfortably with {delta_months:.1f} months delay."

    elif effective_scenario == "recurring_expense_change":
        new_expense = total_expense + amount
        new_surplus = max(0.0, monthly_income - new_expense)
        new_months = (orig_remaining_goal / new_surplus) if (new_surplus > 0 and orig_remaining_goal > 0) else (0.0 if orig_remaining_goal == 0 else 999.0)
        delta_months = new_months - orig_months

        if new_surplus <= 0:
            impact_level = "SEVERE"
            rec = f"Dangerous: Adding ${amount:,.0f}/month eliminates your monthly surplus completely, making '{goal_title}' unachievable without adjustments."
        elif delta_months >= 3.0:
            impact_level = "MODERATE"
            rec = f"Notable Drag: Increases timeline by {delta_months:.1f} months. Recommend offsetting with cuts elsewhere."
        else:
            impact_level = "NEGLIGIBLE"
            rec = f"Manageable: Goal timeline extended by {delta_months:.1f} months."

    elif effective_scenario == "income_change":
        new_income = max(0.0, monthly_income + amount)
        new_surplus = max(0.0, new_income - total_expense)
        new_months = (orig_remaining_goal / new_surplus) if (new_surplus > 0 and orig_remaining_goal > 0) else (0.0 if orig_remaining_goal == 0 else 999.0)
        delta_months = new_months - orig_months

        if amount > 0:
            impact_level = "BENEFICIAL"
            rec = f"Goal Accelerated! Extra ${amount:,.0f}/month income speeds up '{goal_title}' by {abs(delta_months):.1f} months earlier!"
        else:
            impact_level = "SEVERE"
            rec = f"Income reduction of ${abs(amount):,.0f}/month slows goal completion by {delta_months:.1f} months."

    else:
        new_surplus = orig_surplus
        new_months = orig_months
        delta_months = 0.0
        impact_level = "NEGLIGIBLE"
        rec = "No material impact computed."

    new_savings_rate = (new_surplus / new_income * 100.0) if new_income > 0 else 0.0

    return {
        "scenario_type": scenario_type,
        "amount": amount,
        "description": description,
        "original_savings_rate": round(orig_savings_rate, 1),
        "new_savings_rate": round(new_savings_rate, 1),
        "original_monthly_surplus": round(orig_surplus, 2),
        "new_monthly_surplus": round(new_surplus, 2),
        "original_completion_months": round(orig_months, 1) if orig_months < 900 else None,
        "new_completion_months": round(new_months, 1) if new_months < 900 else None,
        "delay_or_advance_months": round(delta_months, 1),
        "impact_level": impact_level,
        "recommendation": rec,
        "needs_setup": False,
        "low_data": is_low_data
    }
