import json
import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional
from app.database import get_db_connection
from app.services.audit import record_audit_log


def get_all_sandbox_proposals(user_id: int = 1) -> List[Dict[str, Any]]:
    """Retrieves all sandbox proposals with parsed JSON payloads."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, user_id, title, action_type, description, impact_summary, projected_savings_delta, status, payload_json, proposed_at, resolved_at, resolved_by_role
        FROM sandbox_proposals
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload_json"])
        except Exception:
            d["payload"] = {}
        result.append(d)
    return result


def create_sandbox_proposal(
    title: str,
    action_type: str,
    description: str,
    impact_summary: str,
    projected_savings_delta: float,
    payload: Dict[str, Any],
    user_id: int = 1,
    user_role: str = "User",
    monthly_income: float = 0.0
) -> Dict[str, Any]:
    """Creates a new staged financial proposal in Sandbox mode.
    Validates 0 < projected_savings_delta <= monthly_income.
    """
    if projected_savings_delta <= 0:
        return {"error": "projected_savings_delta must be > 0"}
    if monthly_income > 0 and projected_savings_delta > monthly_income:
        return {"error": f"projected_savings_delta ${projected_savings_delta:,.0f} exceeds monthly income ${monthly_income:,.0f}"}

    conn = get_db_connection()
    cursor = conn.cursor()
    payload_str = json.dumps(payload)
    cursor.execute("""
        INSERT INTO sandbox_proposals (user_id, title, action_type, description, impact_summary, projected_savings_delta, status, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
    """, (user_id, title, action_type, description, impact_summary, projected_savings_delta, payload_str))
    proposal_id = cursor.lastrowid
    conn.commit()
    conn.close()

    record_audit_log(
        user_role=user_role,
        action_type="SANDBOX_CREATE_PROPOSAL",
        resource=f"PROPOSAL#{proposal_id}",
        status="SUCCESS",
        details=f"Created pending proposal '{title}' (+${projected_savings_delta:,.0f}/mo)"
    )

    return {
        "id": proposal_id,
        "title": title,
        "status": "PENDING",
        "message": "Proposal staged in Sandbox. Explicit user approval required to apply."
    }


def build_discretionary_proposal(conn: sqlite3.Connection, user_id: int = 1) -> Dict[str, Any]:
    """
    Builds proposal data dictionary using Balanced plan's top 3 discretionary cuts.
    Returns dict with keys:
      goal, title, action_type, description, impact_summary, projected_savings_delta, payload, monthly_income
    or raises ValueError if prerequisites are missing.
    """
    cursor = conn.cursor()
    from app.services.metrics import effective_income
    monthly_income = effective_income(user_id)["value"]
    if monthly_income <= 0:
        raise ValueError("Monthly income must be greater than zero to generate proposals.")

    cursor.execute("""
        SELECT id, title, target_amount, current_savings, monthly_contribution, target_date
        FROM goals
        WHERE user_id = ? AND status = 'In Progress'
        ORDER BY id ASC
        LIMIT 1
    """, (user_id,))
    goal = cursor.fetchone()
    if not goal:
        raise ValueError("No active In Progress goal found.")

    cursor.execute("SELECT name, is_essential FROM categories")
    cat_ess_map = {r["name"].lower(): bool(r["is_essential"]) for r in cursor.fetchall()}

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
        raise ValueError("Unable to generate spending cuts from financial plan.")

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
        raise ValueError("No discretionary savings identified.")

    cursor.execute("SELECT expected_return, volatility, inflation FROM users WHERE id = ?", (user_id,))
    u_row = cursor.fetchone()

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
    title = f"Discretionary Budget Cuts for {goal['title']}"
    description = f"Trim top {len(top_3)} discretionary categories to save ${total_cut:,.0f}/month."

    return {
        "goal": dict(goal),
        "title": title,
        "action_type": action_type,
        "description": description,
        "impact_summary": impact_summary,
        "projected_savings_delta": total_cut,
        "payload": payload,
        "monthly_income": monthly_income
    }


def process_sandbox_proposal(
    proposal_id: int,
    action: str,
    user_role: str = "User",
    feedback: Optional[str] = None
) -> Dict[str, Any]:
    """Processes user approval or rejection of a staged proposal. Atomic with rollback."""
    act_upper = action.upper()
    if act_upper not in ["APPROVE", "REJECT"]:
        return {"error": "Invalid action. Must be 'APPROVE' or 'REJECT'.", "error_type": "invalid_action"}

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM sandbox_proposals WHERE id = ?", (proposal_id,))
        proposal = cursor.fetchone()

        if not proposal:
            return {"error": "Proposal not found", "error_type": "not_found"}

        if proposal["status"] != "PENDING":
            return {"error": f"Proposal #{proposal_id} is already resolved ({proposal['status']}).", "error_type": "already_resolved"}

        new_status = "APPROVED" if act_upper == "APPROVE" else "REJECTED"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
            UPDATE sandbox_proposals
            SET status = ?, resolved_at = ?, resolved_by_role = ?
            WHERE id = ?
        """, (new_status, now_str, user_role, proposal_id))

        over_budget = False
        warning = None

        # If approved, apply savings delta to the targeted goal
        if new_status == "APPROVED":
            try:
                payload = json.loads(proposal["payload_json"])
            except Exception:
                conn.rollback()
                return {"error": "Invalid proposal payload", "error_type": "invalid_payload"}

            savings_delta = float(proposal["projected_savings_delta"])
            target_goal_id = payload.get("goal_id")

            if target_goal_id:
                cursor.execute("""
                    UPDATE goals
                    SET monthly_contribution = monthly_contribution + ?
                    WHERE id = ? AND user_id = ?
                """, (savings_delta, target_goal_id, proposal["user_id"]))
                if cursor.rowcount == 0:
                    conn.rollback()
                    return {"error": "Target goal not found", "error_type": "not_found"}
            else:
                cursor.execute("""
                    UPDATE goals
                    SET monthly_contribution = monthly_contribution + ?
                    WHERE id = (SELECT id FROM goals WHERE user_id = ? AND status = 'In Progress' ORDER BY id ASC LIMIT 1)
                """, (savings_delta, proposal["user_id"]))
                if cursor.rowcount == 0:
                    conn.rollback()
                    return {"error": "No active goal to apply to", "error_type": "not_found"}

            # Check contribution budget
            from app.services.budget_check import contribution_budget_check
            over_budget, warning = contribution_budget_check(conn, proposal["user_id"])

        conn.commit()

    except sqlite3.Error as e:
        conn.rollback()
        return {"error": f"Database error: {e}", "error_type": "db_error"}
    finally:
        conn.close()

    audit_details = f"User {action.lower()}d proposal '{proposal['title']}'. Feedback: {feedback or 'None'}"
    if over_budget:
        audit_details += f" | OVER BUDGET: {warning}"

    record_audit_log(
        user_role=user_role,
        action_type=f"SANDBOX_{new_status}",
        resource=f"PROPOSAL#{proposal_id}",
        status=new_status,
        details=audit_details
    )

    result = {
        "id": proposal_id,
        "status": new_status,
        "message": f"Proposal #{proposal_id} was successfully {new_status.lower()}."
    }
    if over_budget:
        result["over_budget"] = True
        result["warning"] = warning
    return result
