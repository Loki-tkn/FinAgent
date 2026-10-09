from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
from app.database import get_db_connection
from app.schemas import ProfileUpdate
from app.security import get_current_role, require_role
from app.services.audit import record_audit_log

router = APIRouter(prefix="/api/profile", tags=["User Profile"])

RISK_PRESETS = {
    "conservative": {"expected_return": 0.03, "volatility": 0.04},
    "moderate": {"expected_return": 0.05, "volatility": 0.10},
    "aggressive": {"expected_return": 0.08, "volatility": 0.18}
}

@router.get("")
def get_user_profile(role: str = Depends(get_current_role)) -> Dict[str, Any]:
    """Returns profile for user 1 including onboarding status and transaction metrics."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = 1")
    user = cursor.fetchone()

    cursor.execute("SELECT COUNT(*), COUNT(DISTINCT strftime('%Y-%m', date)) FROM transactions WHERE user_id = 1")
    tx_stats = cursor.fetchone()
    tx_count = tx_stats[0] if tx_stats else 0
    months_count = tx_stats[1] if tx_stats else 0

    conn.close()

    if not user:
        raise HTTPException(status_code=404, detail="User profile not found")

    user_dict = dict(user)
    user_dict["has_transactions"] = tx_count > 0
    user_dict["months_of_history"] = months_count
    user_dict["onboarded"] = bool(user_dict.get("onboarded", 0))

    return user_dict

@router.put("")
def update_user_profile(
    profile: ProfileUpdate,
    role: str = Depends(require_role("User"))
) -> Dict[str, Any]:
    """Updates user 1 profile and sets risk return/volatility presets."""
    preset = RISK_PRESETS.get(profile.risk_profile, RISK_PRESETS["moderate"])
    exp_return = profile.expected_return if profile.expected_return is not None else preset["expected_return"]
    vol = profile.volatility if profile.volatility is not None else preset["volatility"]
    onboarded_val = 1 if profile.monthly_income > 0 else 0

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET monthly_income = ?,
            est_monthly_expense = ?,
            risk_profile = ?,
            inflation = ?,
            emergency_months = ?,
            expected_return = ?,
            volatility = ?,
            currency = ?,
            onboarded = MAX(onboarded, ?)
        WHERE id = 1
    """, (
        profile.monthly_income,
        profile.est_monthly_expense,
        profile.risk_profile,
        profile.inflation,
        profile.emergency_months,
        exp_return,
        vol,
        profile.currency or 'USD',
        onboarded_val
    ))

    if profile.full_name:
        cursor.execute("UPDATE users SET full_name = ? WHERE id = 1", (profile.full_name,))

    conn.commit()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="PROFILE_UPDATE",
        resource="USER#1",
        status="SUCCESS",
        details=f"Updated profile: Income ${profile.monthly_income:,.0f}, Est Expense ${profile.est_monthly_expense:,.0f}, Risk '{profile.risk_profile}'"
    )

    return {"message": "Profile updated successfully."}
