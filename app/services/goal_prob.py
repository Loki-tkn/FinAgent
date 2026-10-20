import numpy as np
from typing import Dict, Any, List, Optional
from app.services.planner import calculate_months_between
from app.services.assumptions import goal_assumptions
from app.services.quant import real_target, monte_carlo_goal


def goal_success_probability(
    goal_row: Dict[str, Any],
    profile: Dict[str, Any],
    income: float,
    monthly_surplus: float,
    surplus_history: List[float],
    is_low_data: bool,
    *,
    current_override: Optional[float] = None,
    surplus_override: Optional[float] = None,
    deposit_delta: float = 0.0,
    n: int = 10000,
    return_override: Optional[float] = None,
    vol_override: Optional[float] = None,
    inf_override: Optional[float] = None
) -> Dict[str, Any]:
    """
    Canonical shared goal-success probability function.
    """
    target_amount = float(goal_row["target_amount"])
    current_savings = float(goal_row["current_savings"]) if current_override is None else float(current_override)
    contrib = float(goal_row["monthly_contribution"]) if goal_row.get("monthly_contribution") is not None else 0.0
    surplus = float(surplus_override) if surplus_override is not None else float(monthly_surplus)

    months = calculate_months_between(goal_row["target_date"])

    g_ret, g_vol, g_inf = goal_assumptions(goal_row["title"], months, profile)
    if return_override is not None:
        g_ret = return_override
    if vol_override is not None:
        g_vol = vol_override
    if inf_override is not None:
        g_inf = inf_override

    adj_target = real_target(target_amount, g_inf, months)

    # Base deposit is contrib if > 0, else surplus. Plus deposit_delta, capped at [0, max(0, surplus)].
    base_dep = (contrib if contrib > 0 else surplus) + deposit_delta
    max_cap = max(0.0, surplus)
    deposit_mean = max(0.0, min(base_dep, max_cap))

    # Calculate dep_std according to canonical quant rules
    if is_low_data or len(surplus_history) < 2:
        est_exp = float(profile.get("est_monthly_expense", 0.0))
        mean_surplus = max(0.0, income - est_exp)
        hist_std = 0.15 * mean_surplus
    else:
        mean_surplus = float(np.mean(surplus_history))
        hist_std = float(np.std(surplus_history, ddof=1)) if len(surplus_history) > 1 else 0.15 * mean_surplus

    if contrib > 0:
        scale = min(1.0, contrib / mean_surplus) if mean_surplus > 0 else 1.0
        dep_std = max(hist_std * scale, 0.10 * contrib)
    else:
        dep_std = max(10.0, hist_std)

    mc_res = monte_carlo_goal(
        current=current_savings,
        target=adj_target,
        months=months,
        monthly_surplus_history=[],
        annual_return=g_ret,
        annual_vol=g_vol,
        n=n,
        custom_mean=deposit_mean,
        custom_std=dep_std
    )

    return {
        "prob_success": mc_res["prob_success"],
        "p10": mc_res["p10"],
        "p50": mc_res["p50"],
        "p90": mc_res["p90"],
        "paths_percentiles": mc_res["paths_percentiles"],
        "adj_target": adj_target,
        "months": months,
        "deposit_mean": deposit_mean,
        "deposit_std": dep_std
    }
