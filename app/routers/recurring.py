from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Dict, Any
from app.database import get_db_connection
from app.schemas import RecurringRuleCreate, RecurringRuleUpdate
from app.security import require_role
from app.services.audit import record_audit_log

router = APIRouter(prefix="/api/recurring", tags=["Recurring Transactions"])


@router.get("", response_model=List[Dict[str, Any]])
def list_recurring_rules(role: str = Depends(require_role("User"))):
    """Lists all recurring rules for the active user."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, user_id, type, category, amount, description, day_of_month, active, last_generated, created_at
        FROM recurring_rules
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
def create_recurring_rule(
    rule: RecurringRuleCreate,
    role: str = Depends(require_role("User"))
):
    """Creates a new recurring rule."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()

    # Validate category & type
    cursor.execute("SELECT name, type FROM categories WHERE LOWER(name) = LOWER(?)", (rule.category.strip(),))
    cat_row = cursor.fetchone()
    if not cat_row:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Category '{rule.category}' does not exist."
        )
    if cat_row["type"] != rule.type:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Category type '{cat_row['type']}' does not match rule type '{rule.type}'."
        )

    canonical_cat = cat_row["name"]

    cursor.execute("""
        INSERT INTO recurring_rules (user_id, type, category, amount, description, day_of_month, active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
    """, (user_id, rule.type, canonical_cat, rule.amount, rule.description, rule.day_of_month))
    rule_id = cursor.lastrowid
    conn.commit()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="RECURRING_CREATE",
        resource=f"RECURRING#{rule_id}",
        status="SUCCESS",
        details=f"Created recurring rule for {canonical_cat} (${rule.amount:,.2f}/mo on day {rule.day_of_month})"
    )

    return {
        "id": rule_id,
        "type": rule.type,
        "category": canonical_cat,
        "amount": rule.amount,
        "description": rule.description,
        "day_of_month": rule.day_of_month,
        "active": 1,
        "message": "Recurring rule created successfully."
    }


@router.put("/{rule_id}", response_model=Dict[str, Any])
def update_recurring_rule(
    rule_id: int,
    rule: RecurringRuleUpdate,
    role: str = Depends(require_role("User"))
):
    """Updates an existing recurring rule."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM recurring_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring rule not found")

    updates = []
    params = []
    if rule.amount is not None:
        updates.append("amount = ?")
        params.append(rule.amount)
    if rule.description is not None:
        updates.append("description = ?")
        params.append(rule.description)
    if rule.day_of_month is not None:
        updates.append("day_of_month = ?")
        params.append(rule.day_of_month)
    if rule.active is not None:
        updates.append("active = ?")
        params.append(1 if rule.active else 0)

    if updates:
        params.extend([rule_id, user_id])
        cursor.execute(f"UPDATE recurring_rules SET {', '.join(updates)} WHERE id = ? AND user_id = ?", params)
        conn.commit()

    cursor.execute("SELECT * FROM recurring_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
    updated = cursor.fetchone()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="RECURRING_UPDATE",
        resource=f"RECURRING#{rule_id}",
        status="SUCCESS",
        details=f"Updated recurring rule #{rule_id}"
    )

    return dict(updated)


@router.delete("/{rule_id}", response_model=Dict[str, Any])
def delete_recurring_rule(
    rule_id: int,
    role: str = Depends(require_role("User"))
):
    """Deletes a recurring rule."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM recurring_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring rule not found")

    cursor.execute("DELETE FROM recurring_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
    conn.commit()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="RECURRING_DELETE",
        resource=f"RECURRING#{rule_id}",
        status="SUCCESS",
        details=f"Deleted recurring rule #{rule_id}"
    )

    return {"message": f"Recurring rule #{rule_id} deleted successfully."}
