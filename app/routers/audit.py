from fastapi import APIRouter, Depends
from typing import List
from app.schemas import AuditLogResponse
from app.services.audit import get_recent_audit_logs
from app.security import get_current_role

router = APIRouter(prefix="/api/audit", tags=["Audit Logs"])

@router.get("/logs", response_model=List[AuditLogResponse])
def fetch_audit_logs(
    limit: int = 100,
    role: str = Depends(get_current_role)
):
    """
    Fetches the immutable audit trail log of all financial simulations,
    proposals, approvals, and system state changes.
    """
    return get_recent_audit_logs(limit=limit)
