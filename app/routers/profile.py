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
    """Updates user 1 profile fields dynamically."""
    data = profile.model_dump(exclude_unset=True)
    if not data:
        return {"message": "No fields to update."}

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = 1")
    curr_user = cursor.fetchone()
    if not curr_user:
        conn.close()
        raise HTTPException(status_code=404, detail="User profile not found")

    if "risk_profile" in data:
        preset = RISK_PRESETS.get(data["risk_profile"], RISK_PRESETS["moderate"])
        if "expected_return" not in data:
            data["expected_return"] = preset["expected_return"]
        if "volatility" not in data:
            data["volatility"] = preset["volatility"]

    if "monthly_income" in data and data["monthly_income"] > 0:
        data["onboarded"] = 1

    set_clauses = []
    params = []
    for k, v in data.items():
        set_clauses.append(f"{k} = ?")
        params.append(v)

    params.append(1)
    query = f"UPDATE users SET {', '.join(set_clauses)} WHERE id = ?"
    cursor.execute(query, params)

    conn.commit()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="PROFILE_UPDATE",
        resource="USER#1",
        status="SUCCESS",
        details=f"Updated profile fields: {list(data.keys())}"
    )

    return {"message": "Profile updated successfully."}

