from typing import Dict, Any
from app.database import get_db_connection

def monthly_avg_expense(user_id: int = 1, exclude_anomalies: bool = True) -> Dict[str, Any]:
    """
    Computes average monthly expenses normalized by distinct months in data.
    If months_of_history < 2, uses users.est_monthly_expense (70/30 split) with low_data=True.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT est_monthly_expense FROM users WHERE id = ?", (user_id,))
    user_row = cursor.fetchone()
    est_expense = float(user_row["est_monthly_expense"]) if user_row and user_row["est_monthly_expense"] else 0.0

    query = """
        SELECT category, is_essential, amount, date, is_anomaly
        FROM transactions
        WHERE user_id = ? AND type = 'expense'
    """
    cursor.execute(query, (user_id,))
    rows = cursor.fetchall()
    conn.close()

    distinct_months = {r["date"][:7] for r in rows}
    num_months = len(distinct_months)

    # Low data condition (< 2 months of history)
    if num_months < 2:
        if num_months == 0 and est_expense == 0.0:
            return {
                "total": 0.0,
                "essential": 0.0,
                "discretionary": 0.0,
                "by_category": {},
                "low_data": True,
                "months_of_history": 0
            }

        if est_expense > 0:
            essential_part = round(est_expense * 0.70, 2)
            discretionary_part = round(est_expense * 0.30, 2)
            return {
                "total": round(est_expense, 2),
                "essential": essential_part,
                "discretionary": discretionary_part,
                "by_category": {
                    "Essential Needs": {"monthly_amount": essential_part, "is_essential": True},
                    "Discretionary Wants": {"monthly_amount": discretionary_part, "is_essential": False}
                },
                "low_data": True,
                "months_of_history": num_months
            }

    # Calculate from actual multi-month transactions
    category_totals: Dict[str, Dict[str, Any]] = {}
    total_spent = 0.0
    essential_spent = 0.0
    discretionary_spent = 0.0

    for r in rows:
        if exclude_anomalies and r["is_anomaly"]:
            continue

        cat = r["category"]
        is_ess = bool(r["is_essential"])
        amt = float(r["amount"])

        if cat not in category_totals:
            category_totals[cat] = {"amount": 0.0, "is_essential": is_ess}
        category_totals[cat]["amount"] += amt

        total_spent += amt
        if is_ess:
            essential_spent += amt
        else:
            discretionary_spent += amt

    by_category = {}
    for cat, data in category_totals.items():
        by_category[cat] = {
            "monthly_amount": round(data["amount"] / num_months, 2),
            "is_essential": data["is_essential"]
        }

    return {
        "total": round(total_spent / num_months, 2),
        "essential": round(essential_spent / num_months, 2),
        "discretionary": round(discretionary_spent / num_months, 2),
        "by_category": by_category,
        "low_data": False,
        "months_of_history": num_months
    }
