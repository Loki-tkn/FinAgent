from fastapi import APIRouter, Depends
from typing import Dict, Any
from app.database import get_db_connection, clear_user_data, seed_demo_data
from app.security import require_role
from app.services.audit import record_audit_log

router = APIRouter(prefix="/api/admin", tags=["Admin & System"])

@router.post("/load-demo-data")
def load_demo_data(role: str = Depends(require_role("Admin"))):
    """Clears existing user data and seeds 12 months USD demo dataset."""
    conn = get_db_connection()
    try:
        clear_user_data(conn)
        seed_demo_data(conn)
    finally:
        conn.close()

    record_audit_log(
        user_role=role,
        action_type="ADMIN_LOAD_DEMO_DATA",
        resource="DATABASE",
        status="SUCCESS",
        details="Admin loaded clean 12-month USD demo financial dataset"
    )
    return {"message": "Demo data loaded successfully."}

@router.post("/reset-demo-data")
def reset_demo_data(role: str = Depends(require_role("Admin"))):
    """Alias for load-demo-data."""
    return load_demo_data(role=role)

@router.post("/clear-data")
def clear_data(role: str = Depends(require_role("Admin"))):
    """Clears all user transactions, goals, and proposals, resetting to blank state."""
    conn = get_db_connection()
    try:
        clear_user_data(conn)
    finally:
        conn.close()

    record_audit_log(
        user_role=role,
        action_type="ADMIN_CLEAR_DATA",
        resource="DATABASE",
        status="SUCCESS",
        details="Admin cleared all user financial data"
    )
    return {"message": "All user data cleared successfully."}

@router.get("/system-status")
def get_system_status(role: str = Depends(require_role("Admin"))) -> Dict[str, Any]:
    """Returns database size, table records counts, and system status. Admin only."""
    conn = get_db_connection()
    cursor = conn.cursor()

    counts = {}
    for table in ["users", "transactions", "goals", "sandbox_proposals", "audit_logs", "categories", "monthly_budgets", "recurring_rules"]:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cursor.fetchone()[0]
        except Exception:
            counts[table] = 0

    conn.close()

    return {
        "status": "ONLINE",
        "mode": "FINTECHATHON_SIMULATION_SANDBOX",
        "ai_engine": "Robust statistics (median/MAD) + Monte Carlo",
        "table_records": counts,
        "environment": "Local FastAPI + SQLite",
        "external_api_dependencies": None
    }
