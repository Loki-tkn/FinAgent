from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any, Optional
import json
from datetime import datetime
from app.database import get_db_connection
from app.schemas import GoalCreate, GoalPlanResponse, ProgressStatus, SelectPlanRequest, GoalUpdate, GoalDepositRequest
from app.security import get_current_role, require_role
from app.services.planner import generate_goal_plans
from app.services.tracker import track_goal_progress
from app.services.audit import record_audit_log

router = APIRouter(prefix="/api/goals", tags=["Goals & Planner"])

@router.get("")
def list_goals(role: str = Depends(get_current_role)):
    """Lists all user goals."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, user_id, title, target_amount, current_savings, initial_savings, monthly_contribution, target_date, selected_plan, status, notes, created_at
        FROM goals
        WHERE user_id = ?
        ORDER BY id ASC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("")
def create_goal(
    goal: GoalCreate,
    role: str = Depends(require_role("User"))
):
    """Creates a new financial savings goal."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM goals WHERE user_id = ? AND LOWER(title) = LOWER(?)", (user_id, goal.title.strip()))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=409, detail=f"A goal titled '{goal.title}' already exists.")
    cursor.execute("""
        INSERT INTO goals (user_id, title, target_amount, current_savings, initial_savings, monthly_contribution, target_date, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, goal.title.strip(), goal.target_amount, goal.current_savings, goal.current_savings, goal.monthly_contribution, goal.target_date, goal.notes))
    goal_id = cursor.lastrowid
    conn.commit()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="GOAL_CREATE",
        resource=f"GOAL#{goal_id}",
        status="SUCCESS",
        details=f"Created goal '{goal.title}' with target of ${goal.target_amount:,.0f} by {goal.target_date}"
    )

    return {"id": goal_id, "message": "Goal created successfully."}

@router.post("/calculate-plans", response_model=GoalPlanResponse)
def calculate_custom_plans(
    goal: GoalCreate,
    role: str = Depends(get_current_role)
):
    """
    Generates 3 customized savings plans (Conservative, Balanced, Aggressive).
    """
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT monthly_income FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()

    monthly_income = float(user["monthly_income"]) if user else 0.0

    plans = generate_goal_plans(
        target_amount=goal.target_amount,
        current_savings=goal.current_savings,
        target_date_str=goal.target_date,
        monthly_income=monthly_income,
        user_id=user_id,
        goal_title=goal.title
    )
    return plans

@router.get("/{goal_id}/plans", response_model=GoalPlanResponse)
def get_plans_for_goal(
    goal_id: int,
    role: str = Depends(get_current_role)
):
    """Generates 3 plans for an existing goal in the database."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
    goal = cursor.fetchone()
    cursor.execute("SELECT monthly_income FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()

    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    monthly_income = float(user["monthly_income"]) if user else 0.0

    return generate_goal_plans(
        target_amount=goal["target_amount"],
        current_savings=goal["current_savings"],
        target_date_str=goal["target_date"],
        monthly_income=monthly_income,
        user_id=user_id,
        goal_id=goal_id,
        goal_title=goal["title"]
    )

@router.get("/{goal_id}/progress", response_model=ProgressStatus)
def get_goal_progress(
    goal_id: int,
    role: str = Depends(get_current_role)
):
    """Retrieves milestone trajectory tracking and catch-up recommendations."""
    user_id = 1
    progress = track_goal_progress(goal_id=goal_id, user_id=user_id)
    if "error" in progress:
        raise HTTPException(status_code=404, detail=progress["error"])
    return progress

@router.post("/{goal_id}/select-plan")
def select_plan_for_goal(
    goal_id: int,
    req: SelectPlanRequest,
    role: str = Depends(require_role("User"))
):
    """Updates the goal with the chosen plan."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Goal not found")

    cursor.execute("""
        UPDATE goals
        SET selected_plan = ?, monthly_contribution = ?
        WHERE id = ? AND user_id = ?
    """, (req.plan_name, req.monthly_contribution, goal_id, user_id))
    from app.services.budget_check import contribution_budget_check
    over_budget, warning = contribution_budget_check(conn, user_id)
    conn.commit()
    conn.close()

    audit_details = f"Selected '{req.plan_name}' plan with monthly contribution of ${req.monthly_contribution:,.0f}"
    if over_budget:
        audit_details += f" | OVER BUDGET: {warning}"

    record_audit_log(
        user_role=role,
        action_type="GOAL_PLAN_SELECT",
        resource=f"GOAL#{goal_id}",
        status="SUCCESS",
        details=audit_details
    )

    resp = {"message": f"Successfully activated {req.plan_name} plan for goal #{goal_id}."}
    if over_budget:
        resp["over_budget"] = True
        resp["warning"] = warning
    return resp


@router.put("/{goal_id}", response_model=Dict[str, Any])
def update_goal(
    goal_id: int,
    data: GoalUpdate,
    role: str = Depends(require_role("User"))
):
    """Updates goal attributes. Validates budget if contribution changes."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Goal not found")

    updates = []
    params = []
    if data.title is not None:
        new_title = data.title.strip()
        cursor.execute("SELECT id FROM goals WHERE LOWER(title) = LOWER(?) AND id != ? AND user_id = ?", (new_title, goal_id, user_id))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=409, detail=f"Goal with title '{new_title}' already exists.")
        updates.append("title = ?")
        params.append(new_title)
    if data.target_amount is not None:
        updates.append("target_amount = ?")
        params.append(data.target_amount)
    if data.target_date is not None:
        updates.append("target_date = ?")
        params.append(data.target_date)
    if data.notes is not None:
        updates.append("notes = ?")
        params.append(data.notes)
    if data.monthly_contribution is not None:
        updates.append("monthly_contribution = ?")
        params.append(data.monthly_contribution)
    if data.status is not None:
        updates.append("status = ?")
        params.append(data.status)
    elif data.target_amount is not None:
        # Re-evaluate status after target_amount change
        curr_sav = float(existing["current_savings"])
        if curr_sav >= data.target_amount:
            updates.append("status = ?")
            params.append("Completed")
        elif existing["status"] == "Completed" and curr_sav < data.target_amount:
            updates.append("status = ?")
            params.append("In Progress")

    if updates:
        params.extend([goal_id, user_id])
        cursor.execute(f"UPDATE goals SET {', '.join(updates)} WHERE id = ? AND user_id = ?", params)

    over_budget = False
    warning = None
    if data.monthly_contribution is not None:
        from app.services.budget_check import contribution_budget_check
        over_budget, warning = contribution_budget_check(conn, user_id)

    conn.commit()

    cursor.execute("SELECT * FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
    updated_goal = dict(cursor.fetchone())
    conn.close()

    audit_details = f"Updated goal #{goal_id}"
    if over_budget:
        audit_details += f" | OVER BUDGET: {warning}"

    record_audit_log(
        user_role=role,
        action_type="GOAL_UPDATE",
        resource=f"GOAL#{goal_id}",
        status="SUCCESS",
        details=audit_details
    )

    if over_budget:
        updated_goal["over_budget"] = True
        updated_goal["warning"] = warning

    return updated_goal


@router.delete("/{goal_id}", response_model=Dict[str, Any])
def delete_goal(
    goal_id: int,
    role: str = Depends(require_role("User"))
):
    """Deletes a savings goal and rejects any pending proposals targeting it in one DB transaction."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Goal not found")

    cursor.execute("DELETE FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))

    cursor.execute("SELECT id, payload_json FROM sandbox_proposals WHERE user_id = ? AND status = 'PENDING'", (user_id,))
    pending_props = cursor.fetchall()
    rejected_props = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for pp in pending_props:
        try:
            p_dict = json.loads(pp["payload_json"])
            if p_dict.get("goal_id") == goal_id:
                cursor.execute("""
                    UPDATE sandbox_proposals
                    SET status = 'REJECTED', resolved_at = ?, resolved_by_role = 'System'
                    WHERE id = ?
                """, (now_str, pp["id"]))
                rejected_props.append(pp["id"])
        except (json.JSONDecodeError, TypeError):
            continue

    conn.commit()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="GOAL_DELETE",
        resource=f"GOAL#{goal_id}",
        status="SUCCESS",
        details=f"Deleted goal #{goal_id} ({existing['title']})"
    )

    for pid in rejected_props:
        record_audit_log(
            user_role="System",
            action_type="SANDBOX_REJECT",
            resource=f"PROPOSAL#{pid}",
            status="SUCCESS",
            details=f"System automatically rejected proposal #{pid} because target goal #{goal_id} was deleted."
        )

    return {"message": f"Goal #{goal_id} deleted successfully."}


@router.post("/{goal_id}/deposit", response_model=Dict[str, Any])
def deposit_to_goal(
    goal_id: int,
    req: GoalDepositRequest,
    role: str = Depends(require_role("User"))
):
    """Deposits or withdraws from goal savings. Updates goal status automatically."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
    goal = cursor.fetchone()
    if not goal:
        conn.close()
        raise HTTPException(status_code=404, detail="Goal not found")

    curr_savings = float(goal["current_savings"])
    target_amount = float(goal["target_amount"])
    new_savings = round(curr_savings + req.amount, 2)

    if new_savings < 0:
        conn.close()
        raise HTTPException(status_code=422, detail="Current savings cannot drop below zero")

    if new_savings >= target_amount:
        new_status = "Completed"
    else:
        new_status = "In Progress" if goal["status"] == "Completed" else goal["status"]

    cursor.execute("""
        UPDATE goals
        SET current_savings = ?, status = ?
        WHERE id = ? AND user_id = ?
    """, (new_savings, new_status, goal_id, user_id))
    conn.commit()

    cursor.execute("SELECT * FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
    updated_goal = dict(cursor.fetchone())
    conn.close()

    act = "DEPOSIT" if req.amount > 0 else "WITHDRAWAL"
    audit_msg = f"Goal #{goal_id} {act.lower()} of ${abs(req.amount):,.2f} (Savings: ${curr_savings:,.2f} -> ${new_savings:,.2f}, Status: {new_status})"
    if req.note:
        audit_msg += f" | Note: {req.note}"

    record_audit_log(
        user_role=role,
        action_type=f"GOAL_{act}",
        resource=f"GOAL#{goal_id}",
        status="SUCCESS",
        details=audit_msg
    )

    return updated_goal

