from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any, Optional
from app.database import get_db_connection
from app.schemas import GoalCreate, GoalPlanResponse, ProgressStatus, SelectPlanRequest
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
    conn.commit()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="GOAL_PLAN_SELECT",
        resource=f"GOAL#{goal_id}",
        status="SUCCESS",
        details=f"Selected '{req.plan_name}' plan with monthly contribution of ${req.monthly_contribution:,.0f}"
    )

    return {"message": f"Successfully activated {req.plan_name} plan for goal #{goal_id}."}

