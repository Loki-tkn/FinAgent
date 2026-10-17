from datetime import datetime
from typing import Dict, Any
from app.database import get_db_connection

def track_goal_progress(goal_id: int, user_id: int = 1) -> Dict[str, Any]:
    """
    Evaluates actual savings versus the projected milestone trajectory for a goal.
    Calculates whether the user is on track and provides catch-up suggestions.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, target_amount, current_savings, initial_savings, monthly_contribution, target_date, created_at, selected_plan, status
        FROM goals
        WHERE id = ? AND user_id = ?
    """, (goal_id, user_id))
    goal = cursor.fetchone()
    conn.close()

    if not goal:
        return {"error": "Goal not found"}

    target_amount = float(goal["target_amount"])
    current_savings = float(goal["current_savings"])
    initial_savings = float(goal["initial_savings"]) if "initial_savings" in goal.keys() and goal["initial_savings"] is not None else current_savings
    completion_pct = min(100.0, (current_savings / target_amount * 100.0)) if target_amount > 0 else 0.0

    now = datetime.now()
    try:
        created_dt = datetime.strptime(goal["created_at"][:10], "%Y-%m-%d")
    except Exception:
        created_dt = now
    
    try:
        target_dt = datetime.strptime(goal["target_date"], "%Y-%m-%d")
    except Exception:
        target_dt = now

    total_months = max(1, (target_dt.year - created_dt.year) * 12 + (target_dt.month - created_dt.month))
    months_elapsed = max(0, (now.year - created_dt.year) * 12 + (now.month - created_dt.month))
    months_remaining = max(1, (target_dt.year - now.year) * 12 + (target_dt.month - now.month))

    expected_savings_to_date = min(target_amount, initial_savings + ((target_amount - initial_savings) / total_months) * months_elapsed)

    gap = current_savings - expected_savings_to_date

    remaining_amount = max(0.0, target_amount - current_savings)
    catchup_monthly = remaining_amount / months_remaining if months_remaining > 0 else remaining_amount

    if completion_pct >= 100.0:
        status_label = "Completed"
        on_track = True
        advice = f"Congratulations! You have reached 100% of your target (${target_amount:,.0f})."
    elif gap >= 0:
        status_label = "Ahead of Schedule" if gap > (target_amount * 0.05) else "On Track"
        on_track = True
        advice = f"Great pacing! You are on track to hit your ${target_amount:,.0f} target on or before {goal['target_date']}."
    else:
        status_label = "Behind Schedule"
        on_track = False
        deficit_amt = abs(gap)
        advice = (
            f"Currently ${deficit_amt:,.0f} behind target pace. "
            f"Increase monthly contribution to ${catchup_monthly:,.0f}/month to reach the goal by {goal['target_date']}."
        )

    return {
        "goal_id": goal["id"],
        "title": goal["title"],
        "target_amount": target_amount,
        "current_savings": current_savings,
        "completion_percentage": round(completion_pct, 1),
        "target_date": goal["target_date"],
        "months_elapsed": months_elapsed,
        "months_remaining": months_remaining,
        "expected_savings_to_date": round(expected_savings_to_date, 2),
        "actual_vs_expected_gap": round(gap, 2),
        "on_track": on_track,
        "status_label": status_label,
        "catchup_monthly_needed": round(catchup_monthly, 2),
        "actionable_advice": advice
    }
