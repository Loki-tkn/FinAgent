import calendar
from datetime import datetime, date
from app.database import get_db_connection
from app.services.anomaly import detect_spending_anomalies


def materialize_recurring(user_id: int = 1) -> int:
    """Creates missing monthly transactions up to today for active recurring rules.
    Idempotent, sets source='recurring', updates last_generated.
    Runs one anomaly scan only if rows were created.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, user_id, type, category, amount, description, day_of_month, last_generated, created_at
        FROM recurring_rules
        WHERE user_id = ? AND active = 1
    """, (user_id,))
    rules = cursor.fetchall()

    if not rules:
        conn.close()
        return 0

    today = date.today()
    rows_created = 0

    # Cache category is_essential mapping
    cursor.execute("SELECT name, is_essential FROM categories")
    cat_map = {r["name"].lower(): bool(r["is_essential"]) for r in cursor.fetchall()}

    for rule in rules:
        rule_id = rule["id"]
        # Determine start year and month
        if rule["last_generated"]:
            try:
                lg_dt = datetime.strptime(rule["last_generated"][:7], "%Y-%m").date()
                # Start from next month of last_generated
                start_year = lg_dt.year
                start_month = lg_dt.month
            except Exception:
                start_year = today.year
                start_month = today.month
        elif rule["created_at"]:
            try:
                c_dt = datetime.strptime(str(rule["created_at"])[:10], "%Y-%m-%d").date()
                start_year = c_dt.year
                start_month = c_dt.month
            except Exception:
                start_year = today.year
                start_month = today.month
        else:
            start_year = today.year
            start_month = today.month

        # Iterate months from (start_year, start_month) up to (today.year, today.month)
        curr_y = start_year
        curr_m = start_month
        last_gen_date = rule["last_generated"]

        while (curr_y < today.year) or (curr_y == today.year and curr_m <= today.month):
            max_day = calendar.monthrange(curr_y, curr_m)[1]
            day = min(int(rule["day_of_month"]), max_day)
            target_date = date(curr_y, curr_m, day)

            if target_date <= today:
                date_str = target_date.strftime("%Y-%m-%d")
                # Idempotency check: look for same user, date, amount, description, source='recurring'
                cursor.execute("""
                    SELECT id FROM transactions
                    WHERE user_id = ? AND date = ? AND amount = ? AND description = ? AND source = 'recurring'
                """, (user_id, date_str, float(rule["amount"]), rule["description"]))
                existing = cursor.fetchone()

                if not existing:
                    is_ess = int(cat_map.get(rule["category"].lower(), True if rule["type"] == "expense" else False))
                    cursor.execute("""
                        INSERT INTO transactions (user_id, date, amount, category, description, type, is_essential, is_anomaly, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'recurring')
                    """, (user_id, date_str, float(rule["amount"]), rule["category"], rule["description"], rule["type"], is_ess))
                    rows_created += 1

                last_gen_date = date_str

            # Advance one month
            if curr_m == 12:
                curr_y += 1
                curr_m = 1
            else:
                curr_m += 1

        if last_gen_date != rule["last_generated"]:
            cursor.execute("UPDATE recurring_rules SET last_generated = ? WHERE id = ?", (last_gen_date, rule_id))

    conn.commit()
    conn.close()

    if rows_created > 0:
        detect_spending_anomalies(user_id=user_id, threshold_z=3.5)

    return rows_created
