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
