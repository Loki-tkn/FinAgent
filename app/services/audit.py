import sqlite3
from app.database import get_db_connection

def record_audit_log(
    user_role: str,
    action_type: str,
    resource: str,
    status: str,
    details: str,
    ip_address: str = "127.0.0.1"
):
    """
    Records an immutable audit event into the database.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO audit_logs (user_role, action_type, resource, status, details, ip_address)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_role, action_type, resource, status, details, ip_address))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging audit record: {e}")

def get_recent_audit_logs(limit: int = 50):
    """Fetches most recent audit logs."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, timestamp, user_role, action_type, resource, status, details, ip_address
        FROM audit_logs
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
