import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, Dict, Any
from datetime import datetime
from app.database import get_db_connection
from app.schemas import MonteCarloGoalResponse, ForecastResponse, LoanVsCashRequest, LoanVsCashResponse
from app.security import get_current_role
from app.services.audit import record_audit_log
from app.services.assumptions import goal_assumptions
from app.services.quant import (
    required_monthly_payment,
    real_target,
    ewma_forecast,
    monte_carlo_goal,
    cash_flow_at_risk,
    emergency_fund_needed,
    loan_vs_cash,
    CFAR_FALLBACK_MULT
)

router = APIRouter(prefix="/api/quant", tags=["Quantitative Analytics"])

def get_user_profile(user_id: int = 1) -> Dict[str, Any]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else {}

@router.get("/goal/{goal_id}/monte-carlo", response_model=MonteCarloGoalResponse)
def get_goal_monte_carlo(
    goal_id: int,
    ret: Optional[float] = Query(default=None, ge=-0.5, le=1.0, alias="return"),
    vol: Optional[float] = Query(default=None, ge=0.0, le=1.0),
    inf: Optional[float] = Query(default=None, ge=0.0, le=1.0, alias="inflation"),
    role: str = Depends(get_current_role)
):
    """Runs vectorized Monte Carlo simulation for a specific savings goal."""
    user_id = 1
    prof = get_user_profile(user_id)
    monthly_income = float(prof.get("monthly_income", 0.0))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
    goal = cursor.fetchone()
    
    # Extract monthly surplus history (last 12 months)
    cursor.execute("""
        SELECT strftime('%Y-%m', date) as m,
               SUM(CASE WHEN type='income' THEN amount ELSE -amount END) as net
        FROM transactions
        WHERE user_id = ?
        GROUP BY strftime('%Y-%m', date)
        ORDER BY m ASC
    """, (user_id,))
    history_rows = cursor.fetchall()

    # Extract total expense history months count for low_data check
    cursor.execute("""
        SELECT COUNT(DISTINCT strftime('%Y-%m', date)) FROM transactions WHERE user_id = ? AND type = 'expense'
    """, (user_id,))
    exp_months_count = cursor.fetchone()[0]
    conn.close()

    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    target_amount = float(goal["target_amount"])
    current_savings = float(goal["current_savings"])
    contrib = float(goal["monthly_contribution"]) if goal["monthly_contribution"] is not None else 0.0

    # Calculate months remaining
    now = datetime.now()
    try:
        target_dt = datetime.strptime(goal["target_date"], "%Y-%m-%d")
        months_remaining = max(1, (target_dt.year - now.year) * 12 + (target_dt.month - now.month))
    except Exception:
        months_remaining = 12

    ann_return, ann_vol, ann_inf = goal_assumptions(goal["title"], months_remaining, prof)
    if ret is not None:
        ann_return = ret
    if vol is not None:
        ann_vol = vol
    if inf is not None:
        ann_inf = inf

    is_low_data = (exp_months_count < 3)

    # Drop current month from history if >= 3 completed months
    current_ym = now.strftime("%Y-%m")
    completed_rows = [r for r in history_rows if r["m"] < current_ym]
    working_rows = completed_rows if len(completed_rows) >= 3 else list(history_rows)

    if monthly_income <= 0:
        return {
            "goal_id": goal_id,
            "title": goal["title"],
            "current_savings": current_savings,
            "nominal_target": target_amount,
            "real_target": target_amount,
            "months_remaining": months_remaining,
            "expected_return": ann_return,
            "volatility": ann_vol,
            "inflation": ann_inf,
            "prob_success": 0.0,
            "p10": current_savings,
            "p50": current_savings,
            "p90": current_savings,
            "required_monthly_pmt": 0.0,
            "paths_percentiles": [],
            "needs_setup": True,
            "low_data": False
        }

    if is_low_data or len(working_rows) < 2:
        est_exp = float(prof.get("est_monthly_expense", 0.0))
        mean_surplus = max(0.0, monthly_income - est_exp)
        hist_std = 0.15 * mean_surplus
    else:
        surplus_list = [float(r["net"]) for r in working_rows]
        mean_surplus = float(np.mean(surplus_list))
        hist_std = float(np.std(surplus_list, ddof=1)) if len(surplus_list) > 1 else 0.15 * mean_surplus

    if contrib > 0:
        dep_mean = contrib
        scale = min(1.0, contrib / mean_surplus) if mean_surplus > 0 else 1.0
        dep_std = max(hist_std * scale, 0.10 * contrib)
    else:
        dep_mean = max(0.0, mean_surplus)
        dep_std = max(10.0, hist_std)

    adj_target = real_target(target_amount, ann_inf, months_remaining)
    mc_res = monte_carlo_goal(
        current=current_savings,
        target=adj_target,
        months=months_remaining,
        monthly_surplus_history=[],
        annual_return=ann_return,
        annual_vol=ann_vol,
        n=10000,
        custom_mean=dep_mean,
        custom_std=dep_std
    )
    pmt = required_monthly_payment(adj_target, current_savings, months_remaining, ann_return)

    record_audit_log(
        user_role=role,
        action_type="QUANT_MONTE_CARLO",
        resource=f"GOAL#{goal_id}",
        status="SUCCESS",
        details=f"Ran Monte Carlo for '{goal['title']}': Success Prob {mc_res['prob_success']*100:.1f}%, Target ${adj_target:,.0f}"
    )

    return {
        "goal_id": goal_id,
        "title": goal["title"],
        "current_savings": current_savings,
        "nominal_target": target_amount,
        "real_target": round(adj_target, 2),
        "months_remaining": months_remaining,
        "expected_return": ann_return,
        "volatility": ann_vol,
        "inflation": ann_inf,
        "prob_success": mc_res["prob_success"],
        "p10": mc_res["p10"],
        "p50": mc_res["p50"],
        "p90": mc_res["p90"],
        "required_monthly_pmt": round(pmt, 2),
        "paths_percentiles": mc_res["paths_percentiles"],
        "needs_setup": False,
        "low_data": is_low_data
    }

@router.get("/forecast", response_model=ForecastResponse)
def get_expense_forecast(role: str = Depends(get_current_role)):
    """Computes EWMA monthly expense forecast, Cash-Flow-at-Risk (95%), and Emergency Fund needed."""
    user_id = 1
    prof = get_user_profile(user_id)
    monthly_income = float(prof.get("monthly_income", 0.0))
    emergency_months = int(prof.get("emergency_months", 6))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT strftime('%Y-%m', date) as m, SUM(amount) as exp
        FROM transactions
        WHERE user_id = ? AND type = 'expense'
        GROUP BY strftime('%Y-%m', date)
        ORDER BY m ASC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()

    current_ym = datetime.now().strftime("%Y-%m")
    completed = [r for r in rows if r["m"] < current_ym]
    chosen_rows = completed if len(completed) >= 3 else rows
    series = [float(r["exp"]) for r in chosen_rows]
    num_hist = len(series)
    is_low_data = (num_hist < 3)

    if monthly_income <= 0:
        return {
            "ewma_monthly_expense_forecast": 0.0,
            "forecast_lower95": 0.0,
            "forecast_upper95": 0.0,
            "cash_flow_at_risk_95": 0.0,
            "emergency_fund_needed": 0.0,
            "months_covered": emergency_months,
            "historical_months_used": 0,
            "needs_setup": True,
            "low_data": False
        }

    if is_low_data:
        est_exp = float(prof.get("est_monthly_expense", 0.0))
        if est_exp <= 0 and series:
            est_exp = float(series[-1])
        forecast = est_exp
        lower = max(0.0, est_exp * 0.70)
        upper = est_exp * 1.30
        cfar = est_exp * CFAR_FALLBACK_MULT
        em_needed = emergency_months * cfar
    else:
        forecast, lower, upper = ewma_forecast(series, alpha=0.3)
        cfar = cash_flow_at_risk(series, conf=0.95)
        em_needed = emergency_fund_needed(series, conf=0.95, months_cover=emergency_months)

    record_audit_log(
        user_role=role,
        action_type="QUANT_FORECAST",
        resource="EXPENSES",
        status="SUCCESS",
        details=f"Computed EWMA forecast: ${forecast:,.0f}, CFaR-95: ${cfar:,.0f}, EmNeeded: ${em_needed:,.0f}"
    )

    return {
        "ewma_monthly_expense_forecast": round(forecast, 2),
        "forecast_lower95": round(lower, 2),
        "forecast_upper95": round(upper, 2),
        "cash_flow_at_risk_95": round(cfar, 2),
        "emergency_fund_needed": round(em_needed, 2),
        "months_covered": emergency_months,
        "historical_months_used": num_hist,
        "needs_setup": False,
        "low_data": is_low_data
    }


@router.post("/loan-vs-cash", response_model=LoanVsCashResponse)
def evaluate_loan_vs_cash(
    req: LoanVsCashRequest,
    role: str = Depends(get_current_role)
):
    """Compares NPV cost of financing vs paying full cash discounted at opportunity return."""
    user_id = 1
    prof = get_user_profile(user_id)
    opp_return = req.opportunity_return if req.opportunity_return is not None else float(prof.get("expected_return", 0.05))
    
    res = loan_vs_cash(
        price=req.price,
        down_payment=req.down_payment,
        annual_rate=req.annual_rate,
        months=req.months,
        opportunity_return=opp_return
    )

    record_audit_log(
        user_role=role,
        action_type="QUANT_LOAN_VS_CASH",
        resource="SIMULATOR",
        status="SUCCESS",
        details=f"Loan vs Cash (${req.price:,.0f}): Better option '{res['better']}' (NPV Loan: ${res['npv_cost_loan']:,.0f} vs Cash: ${res['npv_cost_cash']:,.0f})"
    )

    return res
