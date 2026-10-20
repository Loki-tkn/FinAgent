from typing import Tuple, Optional
from app.services.metrics import monthly_avg_expense, effective_income


def contribution_budget_check(conn, user_id: int = 1) -> Tuple[bool, Optional[str]]:
    """
    Checks if sum(monthly_contribution of In Progress goals) > monthly_income - monthly_avg_expense.
    Returns (over_budget: bool, warning: Optional[str]).
    """
    monthly_income = effective_income(user_id)["value"]
    cursor = conn.cursor()

    cursor.execute("""
        SELECT SUM(monthly_contribution) FROM goals
        WHERE user_id = ? AND status = 'In Progress'
    """, (user_id,))
    res = cursor.fetchone()
    total_contrib = float(res[0]) if res and res[0] is not None else 0.0

    avg_exp = monthly_avg_expense(user_id=user_id, exclude_anomalies=True)["total"]
    surplus = monthly_income - avg_exp

    if monthly_income > 0 and total_contrib > surplus:
        warning = (
            f"Warning: Total goal contributions (${total_contrib:,.0f}/mo) exceed "
            f"monthly surplus (${surplus:,.0f}/mo). Review your budget."
        )
        return True, warning
    return False, None
