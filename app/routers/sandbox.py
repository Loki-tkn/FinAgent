from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
import json
from app.database import get_db_connection
from app.schemas import SandboxProposalCreate, SandboxApprovalAction
from app.services.sandbox import get_all_sandbox_proposals, create_sandbox_proposal, process_sandbox_proposal
from app.security import get_current_role, require_role
from app.services.metrics import effective_income

router = APIRouter(prefix="/api/sandbox", tags=["Sandbox Approval Hub"])


def _get_monthly_income(user_id: int = 1) -> float:
    return effective_income(user_id)["value"]


@router.get("/proposals")
def list_proposals(role: str = Depends(get_current_role)):
    """Lists all pending and resolved sandbox proposals."""
    user_id = 1
    return get_all_sandbox_proposals(user_id=user_id)


@router.post("/proposals")
def create_proposal(
    proposal: SandboxProposalCreate,
    role: str = Depends(require_role("User"))
):
    """Stages a new simulated budget action in the sandbox."""
    user_id = 1
    monthly_income = _get_monthly_income(user_id)
    res = create_sandbox_proposal(
        title=proposal.title,
        action_type=proposal.action_type,
        description=proposal.description,
        impact_summary=proposal.impact_summary,
        projected_savings_delta=proposal.projected_savings_delta,
        payload=proposal.payload,
        user_id=user_id,
        user_role=role,
        monthly_income=monthly_income
    )
    if "error" in res:
        raise HTTPException(status_code=422, detail=res["error"])
    return res


@router.post("/generate")
def generate_proposal(role: str = Depends(require_role("User"))):
    """Generates a pending proposal from planner Balanced plan's top 3 discretionary cuts."""
    user_id = 1
    monthly_income = effective_income(user_id)["value"]

    if monthly_income <= 0:
        raise HTTPException(status_code=422, detail="Monthly income must be greater than zero to generate proposals.")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, target_amount, current_savings, monthly_contribution, target_date
        FROM goals
        WHERE user_id = ? AND status = 'In Progress'
        ORDER BY id ASC
        LIMIT 1
    """, (user_id,))
    goal = cursor.fetchone()

    if not goal:
        conn.close()
        raise HTTPException(status_code=422, detail="No active In Progress goal found.")

    # Check if ANY PENDING proposal targets this goal
    cursor.execute("""
        SELECT payload_json FROM sandbox_proposals
        WHERE user_id = ? AND status = 'PENDING'
    """, (user_id,))
    pending_rows = cursor.fetchall()
    for pr in pending_rows:
        try:
            p_data = json.loads(pr["payload_json"])
            if p_data.get("goal_id") == goal["id"]:
                conn.close()
                raise HTTPException(status_code=409, detail=f"A pending proposal targeting goal '{goal['title']}' already exists.")
        except (json.JSONDecodeError, TypeError):
            continue

    from app.services.sandbox import build_discretionary_proposal
    try:
        p_dict = build_discretionary_proposal(conn, user_id=user_id)
    except ValueError as e:
        conn.close()
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        conn.close()

    res = create_sandbox_proposal(
        title=p_dict["title"],
        action_type=p_dict["action_type"],
        description=p_dict["description"],
        impact_summary=p_dict["impact_summary"],
        projected_savings_delta=p_dict["projected_savings_delta"],
        payload=p_dict["payload"],
        user_id=user_id,
        user_role=role,
        monthly_income=p_dict["monthly_income"]
    )

    if "error" in res:
        err_type = res.get("error_type")
        if err_type == "conflict":
            raise HTTPException(status_code=409, detail=res["error"])
        raise HTTPException(status_code=422, detail=res["error"])

    return res


@router.post("/proposals/{proposal_id}/action")
def take_approval_action(
    proposal_id: int,
    action_data: SandboxApprovalAction,
    role: str = Depends(require_role("User"))
):
    """Approve or Reject a staged financial proposal."""
    res = process_sandbox_proposal(
        proposal_id=proposal_id,
        action=action_data.action,
        user_role=role,
        feedback=action_data.feedback
    )
    if "error" in res:
        err_type = res.get("error_type", "not_found")
        if err_type in ["invalid_action", "invalid_payload"]:
            raise HTTPException(status_code=422, detail=res["error"])
        elif err_type == "already_resolved":
            raise HTTPException(status_code=409, detail=res["error"])
        else:
            raise HTTPException(status_code=404, detail=res["error"])
    return res
