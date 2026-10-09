from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional, Dict, Any
from datetime import datetime
from app.database import get_db_connection
from app.schemas import TransactionCreate, TransactionResponse, AnomalyScanResponse, CategoryCreate
from app.security import get_current_role, require_role
from app.services.anomaly import detect_spending_anomalies
from app.services.audit import record_audit_log

router = APIRouter(prefix="/api/transactions", tags=["Transactions"])

@router.get("", response_model=List[TransactionResponse])
def list_transactions(
    type: Optional[str] = None,
    category: Optional[str] = None,
    anomaly_only: bool = False,
    role: str = Depends(get_current_role)
):
    """Lists transactions with optional filters."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM transactions WHERE user_id = ?"
    params = [user_id]

    if type:
        query += " AND type = ?"
        params.append(type)
    if category:
        query += " AND category = ?"
        params.append(category)
    if anomaly_only:
        query += " AND is_anomaly = 1"

    query += " ORDER BY date DESC, id DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(r) for r in rows]

@router.get("/categories")
def get_categories():
    """Returns list of income and expense categories."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, type, is_essential, icon FROM categories ORDER BY type ASC, name ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/categories")
def add_custom_category(
    cat: CategoryCreate,
    role: str = Depends(require_role("User"))
):
    """Creates a new custom income/expense category (User role required)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM categories WHERE LOWER(name) = LOWER(?) AND type = ?", (cat.name.strip(), cat.type))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail=f"Category '{cat.name}' of type '{cat.type}' already exists.")

    cursor.execute("""
        INSERT INTO categories (name, type, is_essential, icon)
        VALUES (?, ?, ?, 'tag')
    """, (cat.name.strip(), cat.type, 1 if cat.is_essential else 0))
    cat_id = cursor.lastrowid
    conn.commit()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="CATEGORY_CREATE",
        resource=f"CAT#{cat_id}",
        status="SUCCESS",
        details=f"Created custom {cat.type} category '{cat.name}'"
    )

    return {"id": cat_id, "name": cat.name.strip(), "message": "Category created successfully."}

@router.post("", response_model=Dict[str, Any])
def add_transaction(
    tx: TransactionCreate,
    role: str = Depends(require_role("User"))
):
    """
    Adds a new transaction. Validates category exists in categories table.
    Derives is_essential from category row (ignores client input).
    """
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()

    # Validate category exists
    cursor.execute("SELECT is_essential, type FROM categories WHERE LOWER(name) = LOWER(?)", (tx.category.strip(),))
    cat_row = cursor.fetchone()
    if not cat_row:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Category '{tx.category}' does not exist. Please select or create a valid category."
        )

    derived_is_essential = bool(cat_row["is_essential"])

    cursor.execute("""
        INSERT INTO transactions (user_id, category, amount, type, description, date, is_essential)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, tx.category, tx.amount, tx.type, tx.description, tx.date, int(derived_is_essential)))
    tx_id = cursor.lastrowid
    conn.commit()
    conn.close()

    detect_spending_anomalies(user_id=user_id)

    record_audit_log(
        user_role=role,
        action_type="TRANSACTION_CREATE",
        resource=f"TX#{tx_id}",
        status="SUCCESS",
        details=f"Created {tx.type} '{tx.description}' of ${tx.amount:,.2f} in {tx.category}"
    )

    return {"id": tx_id, "message": "Transaction recorded successfully (simulated)."}

@router.delete("/{tx_id}")
def delete_transaction(
    tx_id: int,
    role: str = Depends(require_role("User"))
):
    """Deletes a transaction."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()

    if rows_affected == 0:
        raise HTTPException(status_code=404, detail="Transaction not found")

    detect_spending_anomalies(user_id=user_id)

    record_audit_log(
        user_role=role,
        action_type="TRANSACTION_DELETE",
        resource=f"TX#{tx_id}",
        status="SUCCESS",
        details=f"Deleted transaction #{tx_id}"
    )

    return {"message": f"Transaction #{tx_id} deleted."}

@router.post("/scan-anomalies", response_model=AnomalyScanResponse)
def run_anomaly_detection_scan(
    threshold_z: float = Query(default=2.0, ge=1.0, le=5.0),
    role: str = Depends(get_current_role)
):
    """Scans all transactions using Moving Average & Z-score anomaly detector."""
    user_id = 1
    results = detect_spending_anomalies(user_id=user_id, threshold_z=threshold_z)
    
    record_audit_log(
        user_role=role,
        action_type="ANOMALY_SCAN",
        resource="TRANSACTIONS",
        status="SUCCESS",
        details=f"Scanned {results['total_scanned']} transactions. Found {results['anomalies_detected']} anomalies (Z >= {threshold_z})."
    )

    return {
        "total_scanned": results["total_scanned"],
        "anomalies_detected": results["anomalies_detected"],
        "anomalies": results["anomalies"],
        "scan_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
