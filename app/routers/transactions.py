import csv
import io
import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status, File, UploadFile

from app.database import get_db_connection
from app.schemas import (
    TransactionCreate,
    TransactionResponse,
    AnomalyScanResponse,
    CategoryCreate,
    HistoryImportRequest,
    HistoryImportResponse,
    TransactionUpdate,
    CategoryUpdate,
    CSVImportResponse
)
from app.security import get_current_role, require_role
from app.services.anomaly import detect_spending_anomalies
from app.services.audit import record_audit_log
from app.services.recurring import materialize_recurring

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
    materialize_recurring(user_id=user_id)

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


@router.get("/history", response_model=List[Dict[str, Any]])
def get_history(
    months: int = Query(default=12, ge=1, le=60),
    role: str = Depends(get_current_role)
):
    """Returns stored monthly summary history rows."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT strftime('%Y-%m', date) as month, category, type, amount, description
        FROM transactions
        WHERE user_id = ? AND source = 'summary'
        ORDER BY month DESC, id ASC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()

    grouped = {}
    for r in rows:
        m = r["month"]
        if m not in grouped:
            if len(grouped) >= months:
                break
            grouped[m] = []
        grouped[m].append({
            "category": r["category"],
            "type": r["type"],
            "amount": float(r["amount"]),
            "description": r["description"]
        })

    return [{"month": m, "items": items} for m, items in grouped.items()]


@router.post("/history", response_model=HistoryImportResponse)
def import_monthly_history(
    req: HistoryImportRequest,
    role: str = Depends(require_role("User"))
):
    """Imports monthly aggregated summary rows. Rejects future months."""
    user_id = 1
    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")
    current_ym = today.strftime("%Y-%m")

    for m in req.months:
        if m.month > current_ym:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot import history for future month {m.month}"
            )

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT name, type, is_essential FROM categories")
    cat_rows = cursor.fetchall()
    cat_lookup = {r["name"].lower(): (r["name"], r["type"], bool(r["is_essential"])) for r in cat_rows}

    total_inserted = 0
    try:
        for m in req.months:
            cursor.execute("""
                DELETE FROM transactions
                WHERE user_id = ? AND source = 'summary' AND strftime('%Y-%m', date) = ?
            """, (user_id, m.month))

            target_date = min(f"{m.month}-15", today_str)

            for item in m.items:
                if item.amount <= 0:
                    continue
                cat_info = cat_lookup.get(item.category.strip().lower())
                if not cat_info:
                    conn.rollback()
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Category '{item.category}' does not exist."
                    )
                canonical_cat, cat_type, is_ess = cat_info
                if cat_type != item.type:
                    conn.rollback()
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Category '{item.category}' type mismatch (expected {cat_type}, got {item.type})."
                    )

                cursor.execute("""
                    INSERT INTO transactions (user_id, date, amount, category, description, type, is_essential, is_anomaly, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'summary')
                """, (user_id, target_date, float(item.amount), canonical_cat, "Monthly total (manual history)", item.type, int(is_ess)))
                total_inserted += 1

        conn.commit()
    except HTTPException:
        conn.close()
        raise
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

    conn.close()

    record_audit_log(
        user_role=role,
        action_type="HISTORY_IMPORT",
        resource="TRANSACTIONS",
        status="SUCCESS",
        details=f"Imported monthly history for {len(req.months)} months ({total_inserted} rows)"
    )

    return HistoryImportResponse(months_saved=len(req.months), rows_inserted=total_inserted)


@router.post("/import", response_model=CSVImportResponse)
async def import_csv_transactions(
    file: UploadFile = File(...),
    dry_run: bool = Query(default=False),
    role: str = Depends(require_role("User"))
):
    """Imports transactions from CSV (max 1MB, max 2000 rows)."""
    user_id = 1
    content = await file.read()
    if len(content) > 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="File size exceeds 1MB limit")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except Exception:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not decode CSV file")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="CSV file is empty or missing headers")

    field_map = {fn.strip().lower(): fn for fn in reader.fieldnames if fn}
    req_fields = ["date", "description", "amount", "category"]
    for rf in req_fields:
        if rf not in field_map:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Missing required CSV column: '{rf}'")

    date_col = field_map["date"]
    desc_col = field_map["description"]
    amt_col = field_map["amount"]
    cat_col = field_map["category"]
    type_col = field_map.get("type")

    rows = list(reader)
    if len(rows) > 2000:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="CSV exceeds limit of 2000 rows")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT name, type, is_essential FROM categories")
    cat_map = {r["name"].lower(): (r["name"], r["type"], bool(r["is_essential"])) for r in cursor.fetchall()}

    cursor.execute("SELECT date, amount, description FROM transactions WHERE user_id = ?", (user_id,))
    existing_set = {(r["date"], float(r["amount"]), r["description"].strip().lower()) for r in cursor.fetchall()}

    valid_rows = []
    errors = []
    duplicates_count = 0
    seen_in_batch = set()

    for idx, r in enumerate(rows, start=1):
        raw_date = (r.get(date_col) or "").strip()
        raw_desc = (r.get(desc_col) or "").strip()
        raw_amt = (r.get(amt_col) or "").strip()
        raw_cat = (r.get(cat_col) or "").strip()
        raw_type = (r.get(type_col) or "").strip().lower() if type_col else None

        if not raw_date or not raw_desc or not raw_amt or not raw_cat:
            errors.append({"row": idx, "error": "Missing required field values"})
            continue

        parsed_date = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                dt_obj = datetime.strptime(raw_date, fmt).date()
                parsed_date = dt_obj.strftime("%Y-%m-%d")
                break
            except ValueError:
                pass
        if not parsed_date:
            errors.append({"row": idx, "error": f"Invalid date format '{raw_date}' (expected YYYY-MM-DD or DD/MM/YYYY)"})
            continue

        try:
            amt_val = float(raw_amt.replace(",", ""))
        except ValueError:
            errors.append({"row": idx, "error": f"Invalid amount '{raw_amt}'"})
            continue

        if raw_type in ["income", "expense"]:
            tx_type = raw_type
            tx_amt = abs(amt_val)
        else:
            if amt_val < 0:
                tx_type = "expense"
                tx_amt = abs(amt_val)
            else:
                tx_type = "income"
                tx_amt = amt_val

        if tx_amt <= 0:
            errors.append({"row": idx, "error": "Amount must be non-zero"})
            continue

        cat_info = cat_map.get(raw_cat.lower())
        if not cat_info:
            errors.append({"row": idx, "error": f"Unknown category '{raw_cat}'"})
            continue

        canonical_cat, expected_type, is_ess = cat_info
        if expected_type != tx_type:
            errors.append({"row": idx, "error": f"Category '{canonical_cat}' is of type '{expected_type}', not '{tx_type}'"})
            continue

        dup_key = (parsed_date, tx_amt, raw_desc.lower())
        if dup_key in existing_set or dup_key in seen_in_batch:
            duplicates_count += 1
            continue

        seen_in_batch.add(dup_key)
        valid_rows.append({
            "date": parsed_date,
            "amount": tx_amt,
            "category": canonical_cat,
            "description": raw_desc,
            "type": tx_type,
            "is_essential": int(is_ess)
        })

    imported_count = 0
    if not dry_run and valid_rows:
        try:
            for vr in valid_rows:
                cursor.execute("""
                    INSERT INTO transactions (user_id, date, amount, category, description, type, is_essential, is_anomaly, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'csv')
                """, (user_id, vr["date"], vr["amount"], vr["category"], vr["description"], vr["type"], vr["is_essential"]))
            conn.commit()
            imported_count = len(valid_rows)
        except Exception as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=500, detail=f"Database error during import: {e}")

    conn.close()

    if not dry_run and imported_count > 0:
        detect_spending_anomalies(user_id=user_id, threshold_z=3.5)
        record_audit_log(
            user_role=role,
            action_type="CSV_IMPORT",
            resource="TRANSACTIONS",
            status="SUCCESS",
            details=f"Imported {imported_count} transactions from CSV"
        )

    preview = [
        {"date": r["date"], "description": r["description"], "amount": r["amount"], "category": r["category"], "type": r["type"]}
        for r in valid_rows[:5]
    ]

    return CSVImportResponse(
        total=len(rows),
        valid=len(valid_rows),
        duplicates=duplicates_count,
        errors=errors,
        preview=preview,
        imported=imported_count
    )


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
        raise HTTPException(status_code=409, detail=f"Category '{cat.name}' of type '{cat.type}' already exists.")

    try:
        cursor.execute("""
            INSERT INTO categories (name, type, is_essential, icon)
            VALUES (?, ?, ?, 'tag')
        """, (cat.name.strip(), cat.type, 1 if cat.is_essential else 0))
        cat_id = cursor.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=409, detail="Category already exists.")
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="CATEGORY_CREATE",
        resource=f"CAT#{cat_id}",
        status="SUCCESS",
        details=f"Created custom {cat.type} category '{cat.name}'"
    )

    return {"id": cat_id, "name": cat.name.strip(), "message": "Category created successfully."}


@router.put("/categories/{cat_id}", response_model=Dict[str, Any])
def update_category(
    cat_id: int,
    cat: CategoryUpdate,
    role: str = Depends(require_role("User"))
):
    """Renames or updates category essentiality with cascade to transactions and budgets."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, type, is_essential, icon FROM categories WHERE id = ?", (cat_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    old_name = existing["name"]
    new_name = cat.name.strip() if cat.name else old_name
    new_is_essential = int(cat.is_essential) if cat.is_essential is not None else existing["is_essential"]

    try:
        cursor.execute("UPDATE categories SET name = ?, is_essential = ? WHERE id = ?", (new_name, new_is_essential, cat_id))
        if new_name != old_name:
            cursor.execute("UPDATE transactions SET category = ? WHERE category = ?", (new_name, old_name))
            cursor.execute("UPDATE monthly_budgets SET category = ? WHERE category = ?", (new_name, old_name))
            cursor.execute("UPDATE recurring_rules SET category = ? WHERE category = ?", (new_name, old_name))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Category '{new_name}' already exists")
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

    cursor.execute("SELECT id, name, type, is_essential, icon FROM categories WHERE id = ?", (cat_id,))
    updated = cursor.fetchone()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="CATEGORY_UPDATE",
        resource=f"CAT#{cat_id}",
        status="SUCCESS",
        details=f"Updated category #{cat_id} ({old_name} -> {new_name})"
    )
    return dict(updated)


@router.delete("/categories/{cat_id}", response_model=Dict[str, Any])
def delete_category(
    cat_id: int,
    role: str = Depends(require_role("User"))
):
    """Deletes category if not in use by any transactions, budgets, or recurring rules."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM categories WHERE id = ?", (cat_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    cat_name = existing["name"]
    cursor.execute("SELECT COUNT(*) FROM transactions WHERE category = ?", (cat_name,))
    tx_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM monthly_budgets WHERE category = ?", (cat_name,))
    b_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM recurring_rules WHERE category = ?", (cat_name,))
    r_count = cursor.fetchone()[0]

    if tx_count > 0 or b_count > 0 or r_count > 0:
        conn.close()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cannot delete category '{cat_name}' because it is in use by transactions, budgets, or recurring rules.")

    cursor.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    conn.commit()
    conn.close()

    record_audit_log(
        user_role=role,
        action_type="CATEGORY_DELETE",
        resource=f"CAT#{cat_id}",
        status="SUCCESS",
        details=f"Deleted category #{cat_id} ({cat_name})"
    )
    return {"message": f"Category '{cat_name}' deleted."}


@router.post("", response_model=Dict[str, Any])
def add_transaction(
    tx: TransactionCreate,
    role: str = Depends(require_role("User"))
):
    """Adds a new transaction and runs anomaly detection."""
    try:
        from datetime import date as dt_date
        dt_date.fromisoformat(tx.date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid date format. Expected YYYY-MM-DD."
        )

    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT name, is_essential, type FROM categories WHERE LOWER(name) = LOWER(?)", (tx.category.strip(),))
    cat_row = cursor.fetchone()
    if not cat_row:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Category '{tx.category}' does not exist. Please select or create a valid category."
        )

    if cat_row["type"] != tx.type:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Category type '{cat_row['type']}' does not match transaction type '{tx.type}'."
        )

    canonical_cat_name = cat_row["name"]
    is_essential = bool(cat_row["is_essential"])

    cursor.execute("""
        INSERT INTO transactions (user_id, date, amount, category, description, type, is_essential, is_anomaly, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'manual')
    """, (user_id, tx.date, tx.amount, canonical_cat_name, tx.description, tx.type, 1 if is_essential else 0))
    tx_id = cursor.lastrowid
    conn.commit()
    conn.close()

    detect_spending_anomalies(user_id=user_id, threshold_z=3.5)

    record_audit_log(
        user_role=role,
        action_type="TRANSACTION_CREATE",
        resource=f"TX#{tx_id}",
        status="SUCCESS",
        details=f"Added {tx.type} of ${tx.amount:,.2f} under {canonical_cat_name} (Essential={is_essential})"
    )

    return {"id": tx_id, "message": "Transaction recorded successfully (simulated)."}


@router.put("/{tx_id}", response_model=Dict[str, Any])
def update_transaction(
    tx_id: int,
    tx: TransactionUpdate,
    role: str = Depends(require_role("User"))
):
    """Partially updates a transaction and runs anomaly detection."""
    user_id = 1
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

    new_type = tx.type or existing["type"]
    new_cat = tx.category.strip() if tx.category else existing["category"]

    cursor.execute("SELECT name, is_essential, type FROM categories WHERE LOWER(name) = LOWER(?)", (new_cat,))
    cat_row = cursor.fetchone()
    if not cat_row:
        conn.close()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Category '{new_cat}' does not exist.")
    if cat_row["type"] != new_type:
        conn.close()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Category type '{cat_row['type']}' does not match transaction type '{new_type}'.")

    canonical_cat = cat_row["name"]
    is_ess = int(cat_row["is_essential"])

    updates = ["category = ?", "is_essential = ?", "type = ?"]
    params = [canonical_cat, is_ess, new_type]

    if tx.amount is not None:
        updates.append("amount = ?")
        params.append(tx.amount)
    if tx.description is not None:
        updates.append("description = ?")
        params.append(tx.description)
    if tx.date is not None:
        updates.append("date = ?")
        params.append(tx.date)

    params.extend([tx_id, user_id])
    cursor.execute(f"UPDATE transactions SET {', '.join(updates)} WHERE id = ? AND user_id = ?", params)
    conn.commit()

    cursor.execute("SELECT * FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
    updated_row = dict(cursor.fetchone())
    conn.close()

    detect_spending_anomalies(user_id=user_id, threshold_z=3.5)

    record_audit_log(
        user_role=role,
        action_type="TRANSACTION_UPDATE",
        resource=f"TX#{tx_id}",
        status="SUCCESS",
        details=f"Updated transaction #{tx_id}"
    )

    return updated_row


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

    detect_spending_anomalies(user_id=user_id, threshold_z=3.5)

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
    threshold_z: float = Query(default=3.5, ge=1.0, le=10.0),
    role: str = Depends(get_current_role)
):
    """Scans all transactions using Moving Average & Z-score anomaly detector."""
    user_id = 1
    do_persist = (threshold_z == 3.5)
    results = detect_spending_anomalies(user_id=user_id, threshold_z=threshold_z, persist=do_persist)

    record_audit_log(
        user_role=role,
        action_type="ANOMALY_SCAN",
        resource="TRANSACTIONS",
        status="SUCCESS",
        details=f"Scanned {results['total_scanned']} transactions. Found {results['anomalies_detected']} anomalies (Z >= {threshold_z}). Persist={do_persist}."
    )

    return {
        "total_scanned": results["total_scanned"],
        "anomalies_detected": results["anomalies_detected"],
        "anomalies": results["anomalies"],
        "scan_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "eligible_categories": results.get("eligible_categories", 0),
        "skipped_categories": results.get("skipped_categories", []),
        "min_samples": results.get("min_samples", 5),
        "message": results.get("message", "")
    }
