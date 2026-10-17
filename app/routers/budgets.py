from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.database import get_db_connection
from app.schemas import BudgetUpsert
from app.security import require_role
from app.services.audit import record_audit_log

router = APIRouter(prefix="/api/budgets", tags=["Budgets"])


@router.get("", response_model=List[Dict[str, Any]])
def get_budgets(
    month: Optional[str] = Query(default=None, description="Month in YYYY-MM format"),
    role: str = Depends(require_role("User"))
):
    """Returns budget status per expense category for given month (defaults to current month)."""
    user_id = 1
    if not month:
        month = datetime.now().strftime("%Y-%m")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get all expense categories
    cursor.execute("SELECT name FROM categories WHERE type = 'expense' ORDER BY name ASC")
    categories = [r["name"] for r in cursor.fetchall()]

    # Get budgets for month
    cursor.execute("""
        SELECT id, category, budget_limit
        FROM monthly_budgets
        WHERE user_id = ? AND month_year = ?
    """, (user_id, month))
    budgets_map = {r["category"].lower(): {"id": r["id"], "limit": float(r["budget_limit"])} for r in cursor.fetchall()}

    # Get spent per category in month
    cursor.execute("""
        SELECT category, SUM(amount) as spent
        FROM transactions
        WHERE user_id = ? AND type = 'expense' AND strftime('%Y-%m', date) = ?
        GROUP BY category
    """, (user_id, month))
    spent_map = {r["category"].lower(): float(r["spent"]) for r in cursor.fetchall()}

    conn.close()

    result = []
    for cat in categories:
        b_info = budgets_map.get(cat.lower())
        b_id = b_info["id"] if b_info else None
        limit = b_info["limit"] if b_info else None
        spent = round(spent_map.get(cat.lower(), 0.0), 2)

        if limit is not None and limit > 0:
            pct = round((spent / limit) * 100.0, 1)
            if spent > limit:
                stat = "over"
            elif pct >= 80.0:
                stat = "warning"
            else:
                stat = "ok"
        else:
            pct = None
            stat = "ok"

        result.append({
            "id": b_id,
            "category": cat,
            "month_year": month,
            "limit": limit,
            "spent": spent,
            "pct": pct,
            "status": stat
        })

    return result


@router.put("", response_model=Dict[str, Any])
def upsert_budget(
    data: BudgetUpsert,
    role: str = Depends(require_role("User"))
):
    """Upserts a monthly budget limit for a category and month."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()

    # Validate category exists and is expense
    cursor.execute("SELECT name, type FROM categories WHERE LOWER(name) = LOWER(?)", (data.category.strip(),))
    cat_row = cursor.fetchone()
    if not cat_row:
        conn.close()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Category '{data.category}' does not exist.")
    if cat_row["type"] != "expense":
        conn.close()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Budgets can only be set for expense categories.")

    canonical_cat = cat_row["name"]

    cursor.execute("""
        INSERT INTO monthly_budgets (user_id, category, month_year, budget_limit)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, category, month_year)
        DO UPDATE SET budget_limit = excluded.budget_limit
    """, (user_id, canonical_cat, data.month_year, data.budget_limit))
    conn.commit()

    cursor.execute("""
        SELECT id, user_id, category, month_year, budget_limit
        FROM monthly_budgets
        WHERE user_id = ? AND category = ? AND month_year = ?
    """, (user_id, canonical_cat, data.month_year))
    row = cursor.fetchone()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="BUDGET_UPSERT",
        resource=f"BUDGET#{row['id']}",
        status="SUCCESS",
        details=f"Upserted budget limit of ${data.budget_limit:,.2f} for {canonical_cat} ({data.month_year})"
    )

    return dict(row)


@router.delete("/{budget_id}", response_model=Dict[str, Any])
def delete_budget(
    budget_id: int,
    role: str = Depends(require_role("User"))
):
    """Deletes a monthly budget entry."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, category, month_year FROM monthly_budgets WHERE id = ? AND user_id = ?", (budget_id, user_id))
    b_row = cursor.fetchone()
    if not b_row:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")

    cursor.execute("DELETE FROM monthly_budgets WHERE id = ? AND user_id = ?", (budget_id, user_id))
    conn.commit()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="BUDGET_DELETE",
        resource=f"BUDGET#{budget_id}",
        status="SUCCESS",
        details=f"Deleted budget for {b_row['category']} ({b_row['month_year']})"
    )

    return {"message": "Budget deleted successfully."}
