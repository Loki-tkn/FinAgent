from typing import Dict, Any, Tuple


def goal_assumptions(goal_title: str, months_remaining: int, profile: Dict[str, Any]) -> Tuple[float, float, float]:
    """Returns (annual_return, annual_volatility, annual_inflation) for a goal.
    Emergency goals or short-term (<= 12 months) use conservative caps of 0.03/0.03.
    """
    prof_ret = float(profile.get("expected_return", 0.05))
    prof_vol = float(profile.get("volatility", 0.10))
    prof_inf = float(profile.get("inflation", 0.03))

    if "emergency" in goal_title.lower() or months_remaining <= 12:
        return min(prof_ret, 0.03), min(prof_vol, 0.03), prof_inf
    return prof_ret, prof_vol, prof_inf
