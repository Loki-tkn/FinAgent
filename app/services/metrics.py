from typing import Dict, Any
from datetime import datetime
from app.database import get_db_connection


def effective_income(user_id: int = 1) -> Dict[str, Any]:
    """
    Computes effective income based on user profile settings and effective_transactions.
    auto = avg monthly income over completed months from effective_transactions (same completed-month rule as monthly_avg_expense; fallback all months).
    value: auto mode -> auto (if auto==0 fall back to manual); manual -> monthly_income; blend -> w*manual+(1-w)*auto.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT monthly_income, income_mode, blend_weight FROM users WHERE id = ?", (user_id,))
    u_row = cursor.fetchone()

    manual = float(u_row["monthly_income"]) if u_row and u_row["monthly_income"] is not None else 0.0
    mode = u_row["income_mode"] if u_row and u_row["income_mode"] else "manual"
    weight = float(u_row["blend_weight"]) if u_row and u_row["blend_weight"] is not None else 0.5

    query = """
        SELECT amount, date
        FROM effective_transactions
        WHERE user_id = ? AND type = 'income'
    """
    cursor.execute(query, (user_id,))
    rows = cursor.fetchall()
    conn.close()

    current_ym = datetime.now().strftime("%Y-%m")
    all_months = {r["date"][:7] for r in rows}
    completed_months = {m for m in all_months if m < current_ym}

    if len(completed_months) >= 2:
        use_months = completed_months
        use_rows = [r for r in rows if r["date"][:7] < current_ym]
    else:
        use_months = all_months
        use_rows = list(rows)

    num_months = len(use_months)
    total_income = sum(float(r["amount"]) for r in use_rows)
    auto = round(total_income / num_months, 2) if num_months > 0 else 0.0

    if mode == "auto":
        val = auto if auto > 0 else manual
    elif mode == "blend":
        val = round(weight * manual + (1.0 - weight) * auto, 2)
    else:  # manual
        val = manual

    return {
        "value": val,
        "auto": auto,
        "manual": manual,
        "mode": mode,
        "weight": weight
    }


def monthly_avg_expense(user_id: int = 1, exclude_anomalies: bool = True) -> Dict[str, Any]:
    """
    Computes average monthly expenses normalized by distinct completed months.
    Excludes the current calendar month when >= 2 completed months exist.
    If months_of_history < 2, uses users.est_monthly_expense (70/30 split) with low_data=True.
    Supports auto, manual, and blend modes.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT est_monthly_expense, expense_mode, expense_manual, blend_weight FROM users WHERE id = ?", (user_id,))
    user_row = cursor.fetchone()
    est_expense = float(user_row["est_monthly_expense"]) if user_row and user_row["est_monthly_expense"] else 0.0
    expense_mode = user_row["expense_mode"] if user_row and user_row["expense_mode"] else "auto"
    expense_manual = float(user_row["expense_manual"]) if user_row and user_row["expense_manual"] is not None else None
    blend_weight = float(user_row["blend_weight"]) if user_row and user_row["blend_weight"] is not None else 0.5

    query = """
        SELECT category, is_essential, amount, date, is_anomaly
        FROM effective_transactions
        WHERE user_id = ? AND type = 'expense'
    """
    cursor.execute(query, (user_id,))
    rows = cursor.fetchall()
    conn.close()

    current_ym = datetime.now().strftime("%Y-%m")
    all_months = {r["date"][:7] for r in rows}
    completed_months = {m for m in all_months if m < current_ym}

    # Use completed months if >= 2, else fall back to all months
    if len(completed_months) >= 2:
        use_months = completed_months
        use_rows = [r for r in rows if r["date"][:7] < current_ym]
    else:
        use_months = all_months
        use_rows = list(rows)

    num_months = len(use_months)

    # Low data condition (< 2 months of history)
    if num_months < 2:
        if num_months == 0 and est_expense == 0.0:
            auto_result = {
                "total": 0.0,
                "essential": 0.0,
                "discretionary": 0.0,
                "by_category": {},
                "low_data": True,
                "months_of_history": 0
            }
        elif est_expense > 0:
            essential_part = round(est_expense * 0.70, 2)
            discretionary_part = round(est_expense * 0.30, 2)
            auto_result = {
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
        else:
            auto_result = {
                "total": 0.0,
                "essential": 0.0,
                "discretionary": 0.0,
                "by_category": {},
                "low_data": True,
                "months_of_history": num_months
            }
    else:
        # Calculate from actual multi-month transactions
        category_totals: Dict[str, Dict[str, Any]] = {}
        total_spent = 0.0
        essential_spent = 0.0
        discretionary_spent = 0.0

        for r in use_rows:
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

        auto_result = {
            "total": round(total_spent / num_months, 2),
            "essential": round(essential_spent / num_months, 2),
            "discretionary": round(discretionary_spent / num_months, 2),
            "by_category": by_category,
            "low_data": False,
            "months_of_history": num_months
        }

    auto_total = auto_result["total"]
    mode = expense_mode
    manual_total = expense_manual

    if mode != "auto" and expense_manual is not None:
        if mode == "manual":
            final_total = expense_manual
        elif mode == "blend":
            final_total = round(blend_weight * expense_manual + (1.0 - blend_weight) * auto_total, 2)
        else:
            final_total = auto_total

        overridden = True
        low_data = False
        if auto_total > 0 and auto_result.get("by_category"):
            scale = final_total / auto_total
            essential = round(auto_result["essential"] * scale, 2)
            discretionary = round(final_total - essential, 2)
            by_category = {}
            for cat, data in auto_result["by_category"].items():
                by_category[cat] = {
                    "monthly_amount": round(data["monthly_amount"] * scale, 2),
                    "is_essential": data["is_essential"]
                }
        else:
            essential = round(final_total * 0.70, 2)
            discretionary = round(final_total * 0.30, 2)
            by_category = {
                "Essential Needs": {"monthly_amount": essential, "is_essential": True},
                "Discretionary Wants": {"monthly_amount": discretionary, "is_essential": False}
            }

        return {
            "total": final_total,
            "essential": essential,
            "discretionary": discretionary,
            "by_category": by_category,
            "low_data": low_data,
            "months_of_history": auto_result["months_of_history"],
            "auto_total": auto_total,
            "manual_total": manual_total,
            "mode": mode,
            "overridden": overridden
        }
    else:
        auto_result["auto_total"] = auto_total
        auto_result["manual_total"] = manual_total
        auto_result["mode"] = mode
        auto_result["overridden"] = False
        return auto_result


def history_series(user_id: int = 1) -> Dict[str, Any]:
    """
    Extracts expense series and surplus series from effective_transactions.
    Uses completed months only when >= 3 completed exist, else all months.
    low_data = len(exp_series) < 3.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT strftime('%Y-%m', date) as m,
               SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) as exp,
               SUM(CASE WHEN type='income' THEN amount ELSE -amount END) as net
        FROM effective_transactions
        WHERE user_id = ?
        GROUP BY strftime('%Y-%m', date)
        ORDER BY m ASC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()

    current_ym = datetime.now().strftime("%Y-%m")
    completed = [r for r in rows if r["m"] < current_ym]
    chosen = completed if len(completed) >= 3 else list(rows)

    exp_series = [float(r["exp"]) for r in chosen]
    surplus_series = [float(r["net"]) for r in chosen]
    months_used = len(chosen)
    low_data = len(exp_series) < 3

    return {
        "exp_series": exp_series,
        "surplus_series": surplus_series,
        "months_used": months_used,
        "low_data": low_data
    }
