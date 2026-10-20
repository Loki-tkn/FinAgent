from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
import json
from app.database import get_db_connection
from app.schemas import SandboxProposalCreate, SandboxApprovalAction
from app.services.sandbox import get_all_sandbox_proposals, create_sandbox_proposal, process_sandbox_proposal
from app.security import get_current_role, require_role

router = APIRouter(prefix="/api/sandbox", tags=["Sandbox Approval Hub"])


def _get_monthly_income(user_id: int = 1) -> float:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT monthly_income FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return float(row["monthly_income"]) if row else 0.0


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
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT monthly_income, expected_return, volatility, inflation FROM users WHERE id = ?", (user_id,))
    u_row = cursor.fetchone()
    monthly_income = float(u_row["monthly_income"]) if u_row and u_row["monthly_income"] is not None else 0.0

    if monthly_income <= 0:
        conn.close()
        raise HTTPException(status_code=422, detail="Monthly income must be greater than zero to generate proposals.")

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

    # Fetch category essentiality map
    cursor.execute("SELECT name, is_essential FROM categories")
    cat_ess_map = {r["name"].lower(): bool(r["is_essential"]) for r in cursor.fetchall()}
    conn.close()

    from app.services.planner import generate_goal_plans, _nper, calculate_months_between
    from app.services.assumptions import goal_assumptions
    from app.services.quant import real_target

    plans_data = generate_goal_plans(
        target_amount=float(goal["target_amount"]),
        current_savings=float(goal["current_savings"]),
        target_date_str=goal["target_date"],
        monthly_income=monthly_income,
        user_id=user_id,
        goal_id=goal["id"],
        goal_title=goal["title"]
    )

    plans = plans_data.get("plans", [])
    balanced_plan = next((p for p in plans if p.get("plan_name") == "Balanced" or p.get("name") == "Balanced"), plans[0] if plans else None)

    adjustments = (balanced_plan.get("adjustments") or balanced_plan.get("spending_adjustments") or []) if balanced_plan else []
    if not balanced_plan or not adjustments:
        raise HTTPException(status_code=422, detail="Unable to generate spending cuts from financial plan.")

    # Discretionary cuts = adjustments whose category is non-essential (categories.is_essential=0 or "Discretionary Wants"), NEVER essentials
    disc_cuts = []
    for adj in adjustments:
        cat_name = adj.get("category", "").strip()
        is_ess = cat_ess_map.get(cat_name.lower(), True)
        if cat_name == "Discretionary Wants" or not is_ess:
            disc_cuts.append(adj)

    disc_cuts.sort(key=lambda x: x["monthly_saved"], reverse=True)
    top_3 = disc_cuts[:3]

    total_cut = round(sum(item["monthly_saved"] for item in top_3), 2)
    if total_cut <= 0:
        raise HTTPException(status_code=422, detail="No discretionary savings identified.")

    profile = {
        "expected_return": float(u_row["expected_return"]) if u_row and u_row["expected_return"] is not None else 0.05,
        "volatility": float(u_row["volatility"]) if u_row and u_row["volatility"] is not None else 0.10,
        "inflation": float(u_row["inflation"]) if u_row and u_row["inflation"] is not None else 0.03,
    }
    months_rem = calculate_months_between(goal["target_date"])
    exp_ret, _, ann_inf = goal_assumptions(goal["title"], months_rem, profile)
    adj_target = real_target(float(goal["target_amount"]), ann_inf, months_rem)
    r_monthly = exp_ret / 12.0

    curr_savings = float(goal["current_savings"])
    orig_contrib = float(goal["monthly_contribution"]) if goal["monthly_contribution"] is not None else 0.0
    if orig_contrib <= 0:
        orig_contrib = max(0.0, float(plans_data.get("current_monthly_surplus", 0.0)))

    new_contrib = orig_contrib + total_cut

    orig_nper = _nper(curr_savings, orig_contrib, adj_target, r_monthly)
    new_nper = _nper(curr_savings, new_contrib, adj_target, r_monthly)

    if orig_nper is not None and new_nper is not None and orig_nper > new_nper:
        accel = round(orig_nper - new_nper, 1)
        impact_summary = (
            f"Accelerates '{goal['title']}' by {accel:.1f} months (from {orig_nper:.1f} to {new_nper:.1f} months) "
            f"by redirecting ${total_cut:,.0f}/mo from discretionary spending."
        )
    elif new_nper is not None:
        impact_summary = (
            f"Enables completing '{goal['title']}' in {new_nper:.1f} months "
            f"by adding ${total_cut:,.0f}/mo from discretionary spending cuts."
        )
    else:
        impact_summary = f"Adds ${total_cut:,.0f}/mo towards '{goal['title']}'."

    payload = {
        "goal_id": goal["id"],
        "goal_title": goal["title"],
        "cuts": top_3,
        "total_cut": total_cut,
        "new_monthly_contribution": new_contrib
    }

    action_type = "DISCRETIONARY_CUT"
    res = create_sandbox_proposal(
        title=f"Discretionary Budget Cuts for {goal['title']}",
        action_type=action_type,
        description=f"Trim top {len(top_3)} discretionary categories to save ${total_cut:,.0f}/month.",
        impact_summary=impact_summary,
        projected_savings_delta=total_cut,
        payload=payload,
        user_id=user_id,
        user_role=role,
        monthly_income=monthly_income
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
