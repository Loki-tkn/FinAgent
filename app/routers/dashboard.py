from fastapi import APIRouter, Depends
from typing import Dict, Any, Optional
from datetime import datetime
from app.database import get_db_connection
from app.services.health_score import calculate_financial_health_score
from app.services.metrics import monthly_avg_expense
from app.security import get_current_role
from app.services.quant import (
    real_target,
    monte_carlo_goal,
    cash_flow_at_risk,
    emergency_fund_needed
)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

@router.get("/summary")
def get_dashboard_summary(role: str = Depends(get_current_role)) -> Dict[str, Any]:
    """
    Returns high-level financial metrics, health score, category breakdown,
    cash flow trend data, and quantitative analytics for the main dashboard.
    """
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, full_name, role, monthly_income, onboarded, emergency_months, expected_return, volatility, inflation, est_monthly_expense FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    monthly_income = float(user["monthly_income"]) if user and user["monthly_income"] is not None else 0.0
    emergency_target = int(user["emergency_months"]) if user and user["emergency_months"] else 6
    is_onboarded = bool(user["onboarded"]) if user else False
    ann_return = float(user["expected_return"]) if user and user["expected_return"] is not None else 0.05
    ann_vol = float(user["volatility"]) if user and user["volatility"] is not None else 0.10
    ann_inf = float(user["inflation"]) if user and user["inflation"] is not None else 0.03
    est_monthly_exp = float(user["est_monthly_expense"]) if user and user["est_monthly_expense"] is not None else 0.0

    cursor.execute("""
        SELECT SUM(amount) as total_inc, COUNT(DISTINCT strftime('%Y-%m', date)) as months_count
        FROM transactions
        WHERE user_id = ? AND type = 'income'
    """, (user_id,))
    inc_row = cursor.fetchone()
    inc_months = max(1, inc_row["months_count"] or 1) if inc_row else 1
    inc_from_tx_avg = round((inc_row["total_inc"] / inc_months), 2) if (inc_row and inc_row["total_inc"]) else 0.0

    cursor.execute("SELECT SUM(current_savings) as total_saved, SUM(target_amount) as total_target FROM goals WHERE user_id = ?", (user_id,))
    goal_stats = cursor.fetchone()
    total_savings = goal_stats["total_saved"] or 0.0
    total_target = goal_stats["total_target"] or 0.0

    cursor.execute("SELECT current_savings FROM goals WHERE user_id = ? AND (title LIKE '%Emergency%' OR title LIKE '%Safety%') LIMIT 1", (user_id,))
    em_row = cursor.fetchone()
    emergency_savings = em_row["current_savings"] if em_row else total_savings

    # Monthly expense history
    cursor.execute("""
        SELECT strftime('%Y-%m', date) as m, SUM(amount) as exp
        FROM transactions
        WHERE user_id = ? AND type = 'expense' AND is_anomaly = 0
        GROUP BY strftime('%Y-%m', date)
        ORDER BY m ASC
    """, (user_id,))
    exp_history = [float(r["exp"]) for r in cursor.fetchall()]

    # Monthly net surplus history
    cursor.execute("""
        SELECT strftime('%Y-%m', date) as m,
               SUM(CASE WHEN type='income' THEN amount ELSE -amount END) as net
        FROM transactions
        WHERE user_id = ?
        GROUP BY strftime('%Y-%m', date)
        ORDER BY m ASC
    """, (user_id,))
    surplus_history = [float(r["net"]) for r in cursor.fetchall()]

    metrics = monthly_avg_expense(user_id=user_id, exclude_anomalies=True)
    essential_expense = metrics["essential"]
    discretionary_expense = metrics["discretionary"]
    total_monthly_expense = metrics["total"]
    is_low_data = metrics.get("low_data", False)

    monthly_surplus = max(0.0, monthly_income - total_monthly_expense)
    savings_rate = (monthly_surplus / monthly_income * 100.0) if monthly_income > 0 else 0.0

    cursor.execute("""
        SELECT category, is_essential, SUM(amount) as total_amount
        FROM transactions
        WHERE user_id = ? AND type = 'expense'
        GROUP BY category, is_essential
        ORDER BY total_amount DESC
    """, (user_id,))
    cat_rows = cursor.fetchall()
    category_breakdown = [
        {
            "category": r["category"],
            "amount": r["total_amount"],
            "is_essential": bool(r["is_essential"])
        }
        for r in cat_rows
    ]

    cursor.execute("""
        SELECT strftime('%Y-%m', date) as month,
               SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) as total_income,
               SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) as total_expense
        FROM transactions
        WHERE user_id = ?
        GROUP BY strftime('%Y-%m', date)
        ORDER BY month ASC
    """, (user_id,))
    trend_rows = cursor.fetchall()
    cash_flow_trends = [
        {
            "month": r["month"],
            "income": r["total_income"],
            "expense": r["total_expense"],
            "net_savings": r["total_income"] - r["total_expense"]
        }
        for r in trend_rows
    ]

    cursor.execute("""
        SELECT id, title, target_amount, current_savings, monthly_contribution, target_date, selected_plan, status
        FROM goals
        WHERE user_id = ?
        ORDER BY id ASC
    """, (user_id,))
    goals = [dict(g) for g in cursor.fetchall()]

    cursor.execute("SELECT COUNT(*) FROM transactions WHERE user_id = ? AND is_anomaly = 1", (user_id,))
    anomaly_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM sandbox_proposals WHERE user_id = ? AND status = 'PENDING'", (user_id,))
    pending_proposals_count = cursor.fetchone()[0]

    conn.close()

    needs_setup = (monthly_income <= 0)

    # Compute Quant Summary for Primary Goal
    quant_data = None
    if not needs_setup:
        # Emergency Fund & CFaR
        if len(exp_history) >= 3:
            cfar = cash_flow_at_risk(exp_history, conf=0.95)
            em_need = emergency_fund_needed(exp_history, conf=0.95, months_cover=emergency_target)
        else:
            base_exp = est_monthly_exp if est_monthly_exp > 0 else (exp_history[-1] if exp_history else total_monthly_expense)
            cfar = round(base_exp * 1.20, 2)
            em_need = round(emergency_target * cfar, 2)

        # Primary Goal Success Prob
        goal_prob = None
        if goals:
            primary_goal = goals[0]
            try:
                g_target = float(primary_goal["target_amount"])
                g_current = float(primary_goal["current_savings"])
                g_dt = datetime.strptime(primary_goal["target_date"], "%Y-%m-%d")
                now = datetime.now()
                g_months = max(1, (g_dt.year - now.year) * 12 + (g_dt.month - now.month))
                
                s_hist = surplus_history if (len(surplus_history) >= 2 and not is_low_data) else [monthly_surplus]
                adj_g_target = real_target(g_target, ann_inf, g_months)
                mc = monte_carlo_goal(g_current, adj_g_target, g_months, s_hist, ann_return, ann_vol, n=2000)
                goal_prob = mc["prob_success"]
            except Exception:
                goal_prob = 0.5

        quant_data = {
            "goal_success_prob": goal_prob,
            "cash_flow_at_risk": cfar,
            "emergency_fund_needed": em_need
        }

    health = calculate_financial_health_score(
        monthly_income=monthly_income,
        total_monthly_expense=total_monthly_expense,
        essential_expense=essential_expense,
        discretionary_expense=discretionary_expense,
        current_savings=total_savings,
        emergency_fund_savings=emergency_savings,
        emergency_target_months=emergency_target
    )

    return {
        "user": {
            "name": user["full_name"] if user else "New User",
            "active_role": role,
            "monthly_income": monthly_income,
            "onboarded": is_onboarded
        },
        "needs_setup": needs_setup,
        "low_data": is_low_data,
        "metrics": {
            "monthly_income": monthly_income,
            "income_from_transactions_avg": inc_from_tx_avg,
            "total_monthly_expense": total_monthly_expense,
            "essential_expense": essential_expense,
            "discretionary_expense": discretionary_expense,
            "monthly_surplus": monthly_surplus,
            "savings_rate_pct": round(savings_rate, 1),
            "total_accumulated_savings": total_savings,
            "total_savings_target": total_target,
            "overall_progress_pct": round((total_savings / total_target * 100.0), 1) if total_target > 0 else 0.0,
            "anomaly_count": anomaly_count,
            "pending_proposals_count": pending_proposals_count
        },
        "quant": quant_data,
        "health_score": health,
        "category_breakdown": category_breakdown,
        "cash_flow_trends": cash_flow_trends,
        "goals": goals
    }
