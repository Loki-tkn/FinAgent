from typing import Dict, Any, Optional
from datetime import datetime
import numpy as np
from app.database import get_db_connection
from app.services.metrics import monthly_avg_expense, history_series
from app.services.assumptions import goal_assumptions
from app.services.quant import real_target, monte_carlo_goal
from app.services.planner import calculate_months_between

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

    cursor.execute("SELECT monthly_income, expected_return, volatility, inflation, est_monthly_expense FROM users WHERE id = ?", (user_id,))
    user_row = cursor.fetchone()
    monthly_income = float(user_row["monthly_income"]) if user_row and user_row["monthly_income"] else 0.0
    prof = dict(user_row) if user_row else {}

    if goal_id:
        cursor.execute("SELECT id, title, target_amount, current_savings, monthly_contribution, target_date FROM goals WHERE id = ?", (goal_id,))
    else:
        cursor.execute("SELECT id, title, target_amount, current_savings, monthly_contribution, target_date FROM goals WHERE user_id = ? AND status = 'In Progress' ORDER BY id ASC LIMIT 1", (user_id,))
    g_raw = cursor.fetchone()
    goal_row = dict(g_raw) if g_raw else None
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
            "prob_success_before": None,
            "prob_success_after": None,
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
            delta_months = (new_months - orig_months) if orig_months is not None else None
        else:
            new_months = None
            delta_months = None

        if not goal_row:
            # No goal: assess by amount vs surplus
            if orig_surplus > 0 and amount > 3.0 * orig_surplus:
                impact_level = "SEVERE"
                rec = f"High Risk: Spending ${amount:,.0f} is {amount/orig_surplus:.1f}x your monthly surplus (${orig_surplus:,.0f})."
            elif orig_surplus > 0 and amount > orig_surplus:
                impact_level = "MODERATE"
                rec = f"Moderate Impact: ${amount:,.0f} exceeds your monthly surplus of ${orig_surplus:,.0f}."
            else:
                impact_level = "NEGLIGIBLE"
                rec = f"Safe: Your cash flow absorbs this purchase comfortably."
        elif current_savings > 0 and (amount / current_savings) > 0.5:
            impact_level = "SEVERE"
            pct_str = f"{min(99.0, amount/current_savings*100.0):.0f}%"
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
            delta_months = (new_months - orig_months) if orig_months is not None else None
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
            delta_months = (new_months - orig_months) if orig_months is not None else None
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

    # Monte Carlo goal success probabilities
    prob_before = None
    prob_after = None

    if goal_row:
        try:
            g_target = float(goal_row["target_amount"])
            g_current = float(goal_row["current_savings"])
            g_contrib = float(goal_row["monthly_contribution"]) if goal_row.get("monthly_contribution") is not None else 0.0
            g_months = calculate_months_between(goal_row["target_date"])

            g_ret, g_vol, g_inf = goal_assumptions(goal_row["title"], g_months, prof)
            adj_g_target = real_target(g_target, g_inf, g_months)

            h_data = history_series(user_id=user_id)
            surplus_history = h_data["surplus_series"]

            # Deposit calculation: min(goal.monthly_contribution, surplus) if contribution > 0 else surplus (floored at 0)
            def compute_dep_params(surplus_val: float):
                if g_contrib > 0:
                    dep_val = max(0.0, min(g_contrib, surplus_val))
                else:
                    dep_val = max(0.0, surplus_val)

                if is_low_data or len(surplus_history) < 2:
                    mean_surplus = surplus_val
                    hist_std = 0.15 * mean_surplus if mean_surplus > 0 else 100.0
                else:
                    mean_surplus = float(np.mean(surplus_history))
                    hist_std = float(np.std(surplus_history, ddof=1)) if len(surplus_history) > 1 else 0.15 * mean_surplus

                if g_contrib > 0:
                    scale = min(1.0, g_contrib / mean_surplus) if mean_surplus > 0 else 1.0
                    dep_std = max(hist_std * scale, 0.10 * g_contrib)
                else:
                    dep_std = max(10.0, hist_std)

                return dep_val, dep_std

            dep_mean_before, dep_std_before = compute_dep_params(orig_surplus)
            mc_before = monte_carlo_goal(
                current=g_current,
                target=adj_g_target,
                months=g_months,
                monthly_surplus_history=[],
                annual_return=g_ret,
                annual_vol=g_vol,
                n=3000,
                custom_mean=dep_mean_before,
                custom_std=dep_std_before
            )
            prob_before = round(mc_before["prob_success"], 4)

            # "After" simulation parameters
            after_savings = g_current
            if effective_scenario == "one_off_purchase":
                after_savings = max(0.0, g_current - amount)

            dep_mean_after, dep_std_after = compute_dep_params(new_surplus)
            mc_after = monte_carlo_goal(
                current=after_savings,
                target=adj_g_target,
                months=g_months,
                monthly_surplus_history=[],
                annual_return=g_ret,
                annual_vol=g_vol,
                n=3000,
                custom_mean=dep_mean_after,
                custom_std=dep_std_after
            )
            prob_after = round(mc_after["prob_success"], 4)

            drop = (prob_before - prob_after) * 100.0
            pct_before = round(prob_before * 100.0)
            pct_after = round(prob_after * 100.0)
            rec += f" Success probability {pct_before}% → {pct_after}%"

            if drop >= 25.0:
                impact_level = "SEVERE"
            elif drop >= 10.0 and impact_level != "SEVERE":
                impact_level = "MODERATE"
        except Exception:
            prob_before = None
            prob_after = None

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
        "prob_success_before": prob_before,
        "prob_success_after": prob_after,
        "needs_setup": False,
        "low_data": is_low_data
    }

