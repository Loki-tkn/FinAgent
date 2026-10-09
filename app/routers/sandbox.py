from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from app.schemas import SandboxProposalCreate, SandboxApprovalAction
from app.services.sandbox import get_all_sandbox_proposals, create_sandbox_proposal, process_sandbox_proposal
from app.security import get_current_role, require_role

router = APIRouter(prefix="/api/sandbox", tags=["Sandbox Approval Hub"])

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
    res = create_sandbox_proposal(
        title=proposal.title,
        action_type=proposal.action_type,
        description=proposal.description,
        impact_summary=proposal.impact_summary,
        projected_savings_delta=proposal.projected_savings_delta,
        payload=proposal.payload,
        user_id=user_id,
        user_role=role
    )
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
        raise HTTPException(status_code=404, detail=res["error"])
    return res
