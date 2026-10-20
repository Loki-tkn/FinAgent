import numpy as np
from fastapi import APIRouter, Depends
from typing import Dict, Any, Optional
from datetime import datetime
from app.database import get_db_connection
from app.services.health_score import calculate_financial_health_score
from app.services.metrics import monthly_avg_expense, history_series, effective_income
from app.security import get_current_role
from app.services.assumptions import goal_assumptions
from app.services.quant import (
    real_target,
    monte_carlo_goal,
    cash_flow_at_risk,
    emergency_fund_needed,
    CFAR_FALLBACK_MULT
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
    user_manual_income = float(user["monthly_income"]) if user and user["monthly_income"] is not None else 0.0
    emergency_target = int(user["emergency_months"]) if user and user["emergency_months"] else 6
    is_onboarded = bool(user["onboarded"]) if user else False
    ann_return = float(user["expected_return"]) if user and user["expected_return"] is not None else 0.05
    ann_vol = float(user["volatility"]) if user and user["volatility"] is not None else 0.10
    ann_inf = float(user["inflation"]) if user and user["inflation"] is not None else 0.03
    est_monthly_exp = float(user["est_monthly_expense"]) if user and user["est_monthly_expense"] is not None else 0.0

    cursor.execute("""
        SELECT SUM(amount) as total_inc, COUNT(DISTINCT strftime('%Y-%m', date)) as months_count
        FROM effective_transactions
        WHERE user_id = ? AND type = 'income'
    """, (user_id,))
    inc_row = cursor.fetchone()
    inc_months = max(1, inc_row["months_count"] or 1) if inc_row else 1
    inc_from_tx_avg = round((inc_row["total_inc"] / inc_months), 2) if (inc_row and inc_row["total_inc"]) else 0.0

    cursor.execute("SELECT SUM(current_savings) as total_saved, SUM(target_amount) as total_target FROM goals WHERE user_id = ?", (user_id,))
    goal_stats = cursor.fetchone()
    total_savings = goal_stats["total_saved"] or 0.0
    total_target = goal_stats["total_target"] or 0.0

    cursor.execute("SELECT current_savings FROM goals WHERE user_id = ? AND goal_type = 'emergency' LIMIT 1", (user_id,))
    em_row = cursor.fetchone()
    if not em_row:
        cursor.execute("SELECT current_savings FROM goals WHERE user_id = ? AND (LOWER(title) LIKE '%emergency%' OR LOWER(title) LIKE '%safety%') LIMIT 1", (user_id,))
        em_row = cursor.fetchone()
    emergency_savings = float(em_row["current_savings"]) if em_row and em_row["current_savings"] is not None else 0.0

    now = datetime.now()
    current_ym = now.strftime("%Y-%m")

    h_data = history_series(user_id=user_id)
    exp_history = h_data["exp_series"]
    surplus_history = h_data["surplus_series"]

    inc_info = effective_income(user_id=user_id)
    effective_inc = inc_info["value"]

    metrics = monthly_avg_expense(user_id=user_id, exclude_anomalies=True)
    essential_expense = metrics["essential"]
    discretionary_expense = metrics["discretionary"]
    total_monthly_expense = metrics["total"]
    is_low_data = metrics.get("low_data", False)

    monthly_surplus = effective_inc - total_monthly_expense
    savings_rate = (monthly_surplus / effective_inc * 100.0) if effective_inc > 0 else 0.0

    cursor.execute("""
        SELECT category, is_essential, SUM(amount) as total_amount
        FROM effective_transactions
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
        FROM effective_transactions
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
            "net_savings": r["total_income"] - r["total_expense"],
            "partial": r["month"] == current_ym
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

    cursor.execute("SELECT COUNT(*) FROM transactions WHERE user_id = ?", (user_id,))
    has_transactions = cursor.fetchone()[0] > 0

    cursor.execute("SELECT COUNT(*) FROM transactions WHERE user_id = ? AND is_anomaly = 1", (user_id,))
    anomaly_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM sandbox_proposals WHERE user_id = ? AND status = 'PENDING'", (user_id,))
    pending_proposals_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT b.budget_limit, COALESCE(SUM(t.amount), 0.0) as spent
        FROM monthly_budgets b
        LEFT JOIN effective_transactions t ON t.user_id = b.user_id
                                 AND LOWER(t.category) = LOWER(b.category)
                                 AND t.type = 'expense'
                                 AND strftime('%Y-%m', t.date) = b.month_year
        WHERE b.user_id = ? AND b.month_year = ?
        GROUP BY b.id, b.budget_limit
    """, (user_id, current_ym))
    b_rows = cursor.fetchall()
    budget_alerts = 0
    for r in b_rows:
        limit = float(r["budget_limit"])
        spent = float(r["spent"])
        if limit > 0 and (spent > limit or (spent / limit) >= 0.8):
            budget_alerts += 1

    conn.close()

    needs_setup = (effective_inc <= 0)

    # Compute Quant Summary for Primary Goal
    quant_data = None
    if not needs_setup:
        if len(exp_history) >= 3:
            cfar = cash_flow_at_risk(exp_history, conf=0.95)
            em_need = emergency_fund_needed(exp_history, conf=0.95, months_cover=emergency_target)
        else:
            base_exp = est_monthly_exp if est_monthly_exp > 0 else (exp_history[-1] if exp_history else total_monthly_expense)
            cfar = round(base_exp * CFAR_FALLBACK_MULT, 2)
            em_need = round(emergency_target * cfar, 2)

        # Primary Goal = first In Progress goal
        goal_prob = None
        primary_goal = next((g for g in goals if g.get("status") == "In Progress"), goals[0] if goals else None)
        if primary_goal:
            try:
                g_target = float(primary_goal["target_amount"])
                g_current = float(primary_goal["current_savings"])
                g_contrib = float(primary_goal["monthly_contribution"]) if primary_goal.get("monthly_contribution") is not None else 0.0
                g_dt = datetime.strptime(primary_goal["target_date"], "%Y-%m-%d")
                g_months = max(1, (g_dt.year - now.year) * 12 + (g_dt.month - now.month))

                g_ret, g_vol, _ = goal_assumptions(primary_goal["title"], g_months, {"expected_return": ann_return, "volatility": ann_vol, "inflation": ann_inf})

                if is_low_data or len(surplus_history) < 2:
                    mean_surplus = monthly_surplus
                    hist_std = 0.15 * mean_surplus if mean_surplus > 0 else 100.0
                else:
                    mean_surplus = float(np.mean(surplus_history))
                    hist_std = float(np.std(surplus_history, ddof=1)) if len(surplus_history) > 1 else 0.15 * mean_surplus

                if g_contrib > 0:
                    dep_mean = g_contrib
                    scale = min(1.0, g_contrib / mean_surplus) if mean_surplus > 0 else 1.0
                    dep_std = max(hist_std * scale, 0.10 * g_contrib)
                else:
                    dep_mean = max(0.0, mean_surplus)
                    dep_std = max(10.0, hist_std)

                adj_g_target = real_target(g_target, ann_inf, g_months)
                mc = monte_carlo_goal(
                    g_current, adj_g_target, g_months, [], g_ret, g_vol, n=10000,
                    custom_mean=dep_mean, custom_std=dep_std
                )
                goal_prob = mc["prob_success"]
            except Exception:
                goal_prob = None

        quant_data = {
            "goal_success_prob": goal_prob,
            "cash_flow_at_risk": cfar,
            "emergency_fund_needed": em_need
        }

    health = calculate_financial_health_score(
        monthly_income=effective_inc,
        total_monthly_expense=total_monthly_expense,
        essential_expense=essential_expense,
        discretionary_expense=discretionary_expense,
        current_savings=total_savings,
        emergency_fund_savings=emergency_savings,
        emergency_target_months=emergency_target
    )
    if not has_transactions:
        health["provisional"] = True

    return {
        "user": {
            "name": user["full_name"] if user else "New User",
            "active_role": role,
            "monthly_income": user_manual_income,
            "onboarded": is_onboarded
        },
        "needs_setup": needs_setup,
        "low_data": is_low_data,
        "metrics": {
            "monthly_income": effective_inc,
            "income_from_transactions_avg": inc_from_tx_avg,
            "income_auto": inc_info["auto"],
            "income_manual": inc_info["manual"],
            "income_mode": inc_info["mode"],
            "total_monthly_expense": total_monthly_expense,
            "expense_auto": metrics.get("auto_total", total_monthly_expense),
            "expense_manual": metrics.get("manual_total"),
            "expense_mode": metrics.get("mode", "auto"),
            "blend_weight": inc_info.get("weight", 0.5),
            "essential_expense": essential_expense,
            "discretionary_expense": discretionary_expense,
            "monthly_surplus": monthly_surplus,
            "savings_rate_pct": round(savings_rate, 1),
            "total_accumulated_savings": total_savings,
            "total_savings_target": total_target,
            "overall_progress_pct": round((total_savings / total_target * 100.0), 1) if total_target > 0 else 0.0,
            "anomaly_count": anomaly_count,
            "pending_proposals_count": pending_proposals_count,
            "budget_alerts": budget_alerts
        },
        "quant": quant_data,
        "health_score": health,
        "category_breakdown": category_breakdown,
        "cash_flow_trends": cash_flow_trends,
        "goals": goals
    }

