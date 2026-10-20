import numpy as np
from typing import List, Dict, Any, Tuple, Optional

def required_monthly_payment(fv: float, pv: float, months: int, annual_return: float) -> float:
    """Annuity PMT formula for goal accumulation. Handles r=0 gracefully."""
    if months <= 0:
        return max(0.0, fv - pv)
    r = annual_return / 12.0
    if abs(r) < 1e-9:
        return max(0.0, (fv - pv) / months)
    compounded = (1.0 + r) ** months
    pmt = (fv - pv * compounded) * r / (compounded - 1.0)
    return max(0.0, float(pmt))

def real_target(amount: float, annual_inflation: float, months: int) -> float:
    """Calculates inflation-adjusted future purchasing target amount."""
    if months <= 0 or annual_inflation <= 0:
        return float(amount)
    return float(amount * ((1.0 + annual_inflation) ** (months / 12.0)))

def robust_anomalies(amounts: List[float]) -> List[float]:
    """Calculates modified Z-scores using median/MAD on log(amount). Flag threshold >3.5."""
    n = len(amounts)
    if n == 0:
        return []
    
    arr = np.array(amounts, dtype=float)
    if n < 5:
        med = float(np.median(arr)) if n > 0 else 1.0
        return [float((x / med - 1.0) * 2.0 if med > 0 else 0.0) for x in arr]
    
    log_vals = np.log(np.maximum(1e-6, arr))
    med_log = float(np.median(log_vals))
    mad_log = max(0.15, float(np.median(np.abs(log_vals - med_log))))

    mod_z = 0.6745 * (log_vals - med_log) / mad_log
    return [float(z) for z in mod_z]

def ewma_forecast(series: List[float], alpha: float = 0.3) -> Tuple[float, float, float]:
    """Computes EWMA forecast, lower95, and upper95 bounds from residual variance."""
    if not series:
        return 0.0, 0.0, 0.0
    arr = np.array(series, dtype=float)
    if len(arr) == 1:
        return float(arr[0]), float(arr[0] * 0.8), float(arr[0] * 1.2)
    
    ewma = arr[0]
    residuals = []
    for x in arr[1:]:
        residuals.append(x - ewma)
        ewma = alpha * x + (1.0 - alpha) * ewma
        
    std_res = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else float(np.std(arr) * 0.2)
    std_res = max(std_res, ewma * 0.05)
    forecast = float(ewma)
    lower = max(0.0, forecast - 1.96 * std_res)
    upper = forecast + 1.96 * std_res
    return forecast, float(lower), float(upper)

def monte_carlo_goal(
    current: float,
    target: float,
    months: int,
    monthly_surplus_history: List[float],
    annual_return: float,
    annual_vol: float,
    n: int = 10000,
    seed: int = 42,
    custom_mean: Optional[float] = None,
    custom_std: Optional[float] = None
) -> Dict[str, Any]:
    """Vectorized Monte Carlo goal simulation. Clamps wealth at >=0."""
    if months <= 0:
        p = 1.0 if current >= target else 0.0
        return {
            "prob_success": p,
            "p10": float(current),
            "p50": float(current),
            "p90": float(current),
            "paths_percentiles": [{"month": 0, "p10": float(current), "p50": float(current), "p90": float(current)}]
        }
    
    rng = np.random.default_rng(seed)
    if custom_mean is not None and custom_std is not None:
        surplus_mean = float(custom_mean)
        surplus_std = float(custom_std)
    else:
        surplus_arr = np.array(monthly_surplus_history, dtype=float) if monthly_surplus_history else np.array([500.0])
        surplus_mean = float(np.mean(surplus_arr)) if len(surplus_arr) > 0 else 500.0
        surplus_std = float(np.std(surplus_arr, ddof=1)) if len(surplus_arr) > 1 else max(10.0, surplus_mean * 0.15)
    
    mu_m = annual_return / 12.0
    sigma_m = max(1e-4, annual_vol / np.sqrt(12.0))
    
    # Generate random increments: N x M
    r_sim = rng.normal(mu_m, sigma_m, size=(n, months))
    s_sim = rng.normal(surplus_mean, surplus_std, size=(n, months))
    
    wealth = np.full(n, current, dtype=float)
    paths_percentiles = [{"month": 0, "p10": float(current), "p50": float(current), "p90": float(current)}]
    
    for m in range(months):
        wealth = wealth * (1.0 + r_sim[:, m]) + s_sim[:, m]
        wealth = np.maximum(0.0, wealth)
        p10, p50, p90 = np.percentile(wealth, [10, 50, 90])
        paths_percentiles.append({
            "month": m + 1,
            "p10": round(float(p10), 2),
            "p50": round(float(p50), 2),
            "p90": round(float(p90), 2)
        })
        
    prob_success = float(np.mean(wealth >= target))
    p10 = float(np.percentile(wealth, 10))
    p50 = float(np.percentile(wealth, 50))
    p90 = float(np.percentile(wealth, 90))
    
    return {
        "prob_success": round(prob_success, 4),
        "p10": round(p10, 2),
        "p50": round(p50, 2),
        "p90": round(p90, 2),
        "paths_percentiles": paths_percentiles
    }

CFAR_FALLBACK_MULT = 1.25

def cash_flow_at_risk(expense_history: List[float], conf: float = 0.95) -> float:
    """Calculates the empirical value/percentile of monthly living expenses at given confidence."""
    if not expense_history:
        return 0.0
    arr = np.array(expense_history, dtype=float)
    if len(arr) < 6:
        mean_val = float(np.mean(arr))
        std_val = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
        return round(mean_val + 1.65 * std_val, 2)
    return round(float(np.percentile(arr, conf * 100.0)), 2)

def emergency_fund_needed(expense_history: List[float], conf: float = 0.95, months_cover: int = 6) -> float:
    """Recommended emergency fund size scaled to cash flow at risk."""
    cfar = cash_flow_at_risk(expense_history, conf=conf)
    return round(float(max(1, months_cover) * cfar), 2)

def loan_vs_cash(
    price: float,
    down_payment: float,
    annual_rate: float,
    months: int,
    opportunity_return: float
) -> Dict[str, Any]:
    """Compares NPV cost of financing vs paying full cash discounted at opportunity return."""
    down_payment = min(price, max(0.0, down_payment))
    loan_amount = price - down_payment
    r_loan = annual_rate / 12.0
    r_opp = opportunity_return / 12.0
    
    if loan_amount <= 0 or months <= 0:
        return {
            "monthly_payment": 0.0,
            "total_interest": 0.0,
            "npv_cost_loan": round(price, 2),
            "npv_cost_cash": round(price, 2),
            "better": "cash"
        }
        
    if abs(r_loan) < 1e-9:
        monthly_payment = loan_amount / months
    else:
        comp = (1.0 + r_loan) ** months
        monthly_payment = loan_amount * (r_loan * comp) / (comp - 1.0)
        
    total_paid = down_payment + (monthly_payment * months)
    total_interest = max(0.0, total_paid - price)
    
    # NPV of loan payments discounted at opportunity cost
    if abs(r_opp) < 1e-9:
        npv_loan = total_paid
    else:
        discount_factors = 1.0 / ((1.0 + r_opp) ** np.arange(1, months + 1))
        npv_loan = down_payment + float(np.sum(monthly_payment * discount_factors))
        
    npv_cash = price
    better = "loan" if npv_loan < npv_cash else "cash"
    
    return {
        "monthly_payment": round(float(monthly_payment), 2),
        "total_interest": round(float(total_interest), 2),
        "npv_cost_loan": round(float(npv_loan), 2),
        "npv_cost_cash": round(float(npv_cash), 2),
        "better": better
    }
