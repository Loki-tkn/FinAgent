import math
from datetime import datetime
from typing import Dict, Any, List, Optional
from app.database import get_db_connection
from app.services.metrics import monthly_avg_expense
from app.services.quant import required_monthly_payment, real_target
from app.services.assumptions import goal_assumptions


def calculate_months_between(target_date_str: str) -> int:
    """Calculates integer months remaining until target date."""
    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
        now = datetime.now()
        months = (target_date.year - now.year) * 12 + (target_date.month - now.month)
        return max(1, months)
    except Exception:
        return 12


def _nper(pv: float, pmt: float, fv: float, r: float) -> Optional[float]:
    """Months to reach fv from pv with monthly pmt and rate r."""
    if fv <= pv:
        return 0.0
    if pmt <= 0:
        return None
    if abs(r) < 1e-9:
        return (fv - pv) / pmt
    a = pmt / r
    if fv + a <= 0 or pv + a <= 0:
        return None
    try:
        val = math.log((fv + a) / (pv + a)) / math.log(1.0 + r)
        return max(0.0, val)
    except Exception:
        return None


def generate_goal_plans(
    target_amount: float,
    current_savings: float,
    target_date_str: str,
    monthly_income: float,
    user_id: int = 1,
    goal_id: int = None,
    goal_title: str = "Savings Goal"
) -> Dict[str, Any]:
    """
    Generates 3 customized savings plans (Conservative, Balanced, Aggressive)
    with category spending cuts and dynamic feasibility analysis based on gap.
    """
    if monthly_income <= 0:
        return {
            "goal_id": goal_id,
            "title": goal_title,
            "target_amount": target_amount,
            "current_savings": current_savings,
            "target_date": target_date_str,
            "months_remaining": 0,
            "monthly_income": 0,
            "monthly_expense": 0,
            "current_monthly_surplus": 0,
            "required_monthly_savings": 0,
            "plans": [],
            "summary_advice": "Please set your monthly income in Settings / Profile to generate goal plans.",
            "needs_setup": True,
            "low_data": False
        }

    months_remaining = calculate_months_between(target_date_str)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT expected_return, volatility, inflation FROM users WHERE id = ?", (user_id,))
    u_row = cursor.fetchone()
    conn.close()

    profile = {
        "expected_return": float(u_row["expected_return"]) if u_row and u_row["expected_return"] is not None else 0.05,
        "volatility": float(u_row["volatility"]) if u_row and u_row["volatility"] is not None else 0.10,
        "inflation": float(u_row["inflation"]) if u_row and u_row["inflation"] is not None else 0.03,
    }

    exp_return, _, ann_inf = goal_assumptions(goal_title, months_remaining, profile)

    adj_target = real_target(target_amount, ann_inf, months_remaining)
    required_monthly_savings = required_monthly_payment(adj_target, current_savings, months_remaining, exp_return)

    metrics = monthly_avg_expense(user_id=user_id, exclude_anomalies=True)
    cat_expenses = metrics["by_category"]
    total_monthly_expense = metrics["total"]
    is_low_data = metrics.get("low_data", False)

    current_monthly_surplus = monthly_income - total_monthly_expense
    remaining_to_save = max(0.0, adj_target - current_savings)
    r_monthly = exp_return / 12.0

    strategies = [
        {
            "name": "Conservative",
            "non_essential_cut": 0.12,
            "essential_cut": 0.0,
            "description": "Gentle adjustments with minimal lifestyle impact. Ideal for long-term consistency."
        },
        {
            "name": "Balanced",
            "non_essential_cut": 0.28,
            "essential_cut": 0.05,
            "description": "Optimizes non-essential dining and shopping while protecting quality of life."
        },
        {
            "name": "Aggressive",
            "non_essential_cut": 0.48,
            "essential_cut": 0.10,
            "description": "Maximum savings velocity. Temporarily minimizes non-essential leisure for rapid goal attainment."
        }
    ]

    essential_allowed = {"Utilities & Bills", "Transportation & Fuel"}
    plans: List[Dict[str, Any]] = []

    for strat in strategies:
        adjustments = []
        total_cut = 0.0

        for cat, data in cat_expenses.items():
            curr_amt = data["monthly_amount"]
            is_ess = data["is_essential"]
            if is_ess and cat not in essential_allowed:
                continue
            cut_pct = strat["essential_cut"] if is_ess else strat["non_essential_cut"]

            if cut_pct > 0 and curr_amt > 10:
                saved_amt = round(curr_amt * cut_pct, 2)
                rec_amt = round(curr_amt - saved_amt, 2)
                total_cut += saved_amt

                if is_ess:
                    rationale = f"Optimize utility bills, negotiate subscriptions, and batch commute ({cut_pct*100:.0f}% savings)."
                else:
                    rationale = f"Reduce discretionary spending frequency ({cut_pct*100:.0f}% savings)."

                adjustments.append({
                    "category": cat,
                    "current_spending": round(curr_amt, 2),
                    "cut_percentage": round(cut_pct * 100, 1),
                    "recommended_spending": round(rec_amt, 2),
                    "monthly_saved": round(saved_amt, 2),
                    "rationale": rationale
                })

        new_monthly_surplus = current_monthly_surplus + total_cut
        mtc = _nper(current_savings, new_monthly_surplus, adj_target, r_monthly)
        months_to_complete = round(mtc, 1) if mtc is not None else None

        gap = required_monthly_savings - new_monthly_surplus
        surplus_or_deficit = "Surplus" if gap <= 0 else "Deficit"

        if gap <= 0:
            feasibility = "Easy"
        elif required_monthly_savings > 0 and gap <= 0.20 * required_monthly_savings:
            feasibility = "Moderate"
        elif required_monthly_savings > 0 and gap <= 0.50 * required_monthly_savings:
            feasibility = "Challenging"
        else:
            feasibility = "Extreme"

        if months_to_complete is None:
            ai_rec = f"Under the {strat['name']} plan, monthly surplus is ${new_monthly_surplus:,.0f} (insufficient to accumulate savings)."
        elif months_to_complete < months_remaining:
            ai_rec = f"Under the {strat['name']} plan, you save ${new_monthly_surplus:,.0f}/month. You will reach your goal {round(months_remaining - months_to_complete, 1)} months earlier than deadline!"
        else:
            ai_rec = f"Under the {strat['name']} plan, you save ${new_monthly_surplus:,.0f}/month. You achieve target in {months_to_complete} months."

        plans.append({
            "plan_name": strat["name"],
            "monthly_savings_target": round(new_monthly_surplus, 2),
            "months_to_complete": months_to_complete,
            "feasibility": feasibility,
            "monthly_gap": round(abs(gap), 2),
            "surplus_or_deficit": surplus_or_deficit,
            "adjustments": adjustments,
            "total_spending_cut": round(total_cut, 2),
            "ai_recommendation": ai_rec
        })

    summary_advice = (
        f"To reach '{goal_title}' (${target_amount:,.0f}) by {target_date_str} ({months_remaining} months left), "
        f"you need ${required_monthly_savings:,.0f}/month. Your baseline surplus is ${current_monthly_surplus:,.0f}/month."
    )

    return {
        "goal_id": goal_id,
        "title": goal_title,
        "target_amount": target_amount,
        "current_savings": current_savings,
        "target_date": target_date_str,
        "months_remaining": months_remaining,
        "monthly_income": monthly_income,
        "monthly_expense": round(total_monthly_expense, 2),
        "current_monthly_surplus": round(current_monthly_surplus, 2),
        "required_monthly_savings": round(required_monthly_savings, 2),
        "plans": plans,
        "summary_advice": summary_advice,
        "needs_setup": False,
        "low_data": is_low_data
    }
