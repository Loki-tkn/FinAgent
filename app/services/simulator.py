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
    goal_title = goal_row["title"] if goal_row else None

    orig_surplus = monthly_income - total_expense
    orig_savings_rate = (orig_surplus / monthly_income * 100.0) if monthly_income > 0 else 0.0

    if goal_row and orig_surplus > 0:
        orig_remaining_goal = max(0.0, target_amount - current_savings)
        orig_months = (orig_remaining_goal / orig_surplus) if orig_remaining_goal > 0 else 0.0
    else:
        orig_months = None

    new_income = monthly_income
    new_expense = total_expense
    new_savings = current_savings

    effective_scenario = scenario_type
    if scenario_type == "recurring_expense_change" and frequency == "one_time":
        effective_scenario = "one_off_purchase"

    if effective_scenario == "one_off_purchase":
        new_savings = max(0.0, current_savings - amount)
        new_surplus = orig_surplus
        if goal_row and new_surplus > 0:
            new_remaining_goal = max(0.0, target_amount - new_savings)
            new_months = (new_remaining_goal / new_surplus) if new_remaining_goal > 0 else 0.0
            delta_months = new_months - orig_months if orig_months is not None else 0.0
        else:
            new_months = None
            delta_months = None
        
        savings_ratio = (amount / current_savings) if current_savings > 0 else 0.0
        if current_savings > 0 and savings_ratio > 0.5:
            impact_level = "SEVERE"
            pct_str = f"{min(99.0, savings_ratio*100.0):.0f}%"
            rec = f"High Risk: Spending ${amount:,.0f} consumes {pct_str} of your saved buffer" + (f" and delays '{goal_title}'." if goal_title else ".")
        elif delta_months is not None and delta_months > 1.5:
            impact_level = "MODERATE"
            rec = f"Moderate Delay: This purchase delays your goal completion by {delta_months:.1f} months."
        else:
            impact_level = "NEGLIGIBLE"
            rec = f"Safe: Your cash flow absorbs this purchase comfortably."

    elif effective_scenario == "recurring_expense_change":
        new_expense = total_expense + amount
        new_surplus = monthly_income - new_expense
        if goal_row and new_surplus > 0:
            orig_remaining_goal = max(0.0, target_amount - current_savings)
            new_months = (orig_remaining_goal / new_surplus) if orig_remaining_goal > 0 else 0.0
            delta_months = new_months - orig_months if orig_months is not None else 0.0
        else:
            new_months = None
            delta_months = None

        if new_surplus <= 0:
            impact_level = "SEVERE"
            rec = f"Dangerous: Adding ${amount:,.0f}/month eliminates your monthly surplus completely."
        elif delta_months is not None and delta_months >= 3.0:
            impact_level = "MODERATE"
            rec = f"Notable Drag: Increases goal timeline by {delta_months:.1f} months."
        else:
            impact_level = "NEGLIGIBLE"
            rec = f"Manageable: Monthly surplus remains positive at ${new_surplus:,.0f}/month."

    elif effective_scenario == "income_change":
        new_income = max(0.0, monthly_income + amount)
        new_surplus = new_income - total_expense
        if goal_row and new_surplus > 0:
            orig_remaining_goal = max(0.0, target_amount - current_savings)
            new_months = (orig_remaining_goal / new_surplus) if orig_remaining_goal > 0 else 0.0
            delta_months = new_months - orig_months if orig_months is not None else 0.0
        else:
            new_months = None
            delta_months = None

        if amount > 0:
            impact_level = "BENEFICIAL"
            rec = f"Goal Accelerated! Extra ${amount:,.0f}/month income increases your monthly surplus to ${new_surplus:,.0f}."
        else:
            impact_level = "SEVERE"
            rec = f"Income reduction of ${abs(amount):,.0f}/month reduces monthly surplus to ${new_surplus:,.0f}."

    else:
        new_surplus = orig_surplus
        new_months = orig_months
        delta_months = None
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
        "original_completion_months": round(orig_months, 1) if orig_months is not None else None,
        "new_completion_months": round(new_months, 1) if new_months is not None else None,
        "delay_or_advance_months": round(delta_months, 1) if delta_months is not None else None,
        "impact_level": impact_level,
        "recommendation": rec,
        "needs_setup": False,
        "low_data": is_low_data
    }

