import sqlite3
import os
import json
import random
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finances.db")

DEFAULT_CATEGORIES = [
    ('Salary', 'income', 1, 'wallet'),
    ('Freelance / Bonus', 'income', 1, 'briefcase'),
    ('Rent & Housing', 'expense', 1, 'home'),
    ('Groceries & Food', 'expense', 1, 'shopping-cart'),
    ('Utilities & Bills', 'expense', 1, 'zap'),
    ('Dining & Cafes', 'expense', 0, 'coffee'),
    ('Shopping & Gadgets', 'expense', 0, 'shopping-bag'),
    ('Entertainment & Leisure', 'expense', 0, 'film'),
    ('Health & Fitness', 'expense', 1, 'heart'),
    ('Transportation & Fuel', 'expense', 1, 'truck'),
    ('Online Subscriptions', 'expense', 0, 'credit-card')
]

def get_db_connection():
    """Returns a sqlite3 connection with dictionary-like row factory."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def add_months(dt, months):
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    max_days = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return dt.replace(year=year, month=month, day=min(dt.day, max_days))

def init_db(force_reseed: bool = False):
    """Initializes the database schema and performs idempotent migrations."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'User',
        monthly_income REAL NOT NULL DEFAULT 0,
        onboarded INTEGER DEFAULT 0,
        est_monthly_expense REAL DEFAULT 0,
        risk_profile TEXT DEFAULT 'moderate',
        expected_return REAL DEFAULT 0.05,
        volatility REAL DEFAULT 0.10,
        inflation REAL DEFAULT 0.03,
        emergency_months INTEGER DEFAULT 6,
        currency TEXT DEFAULT 'USD',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        type TEXT NOT NULL,
        is_essential BOOLEAN NOT NULL DEFAULT 1,
        icon TEXT DEFAULT 'tag'
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        type TEXT NOT NULL,
        description TEXT NOT NULL,
        date TEXT NOT NULL,
        is_essential BOOLEAN NOT NULL DEFAULT 1,
        is_anomaly BOOLEAN NOT NULL DEFAULT 0,
        anomaly_reason TEXT,
        source TEXT NOT NULL DEFAULT 'manual',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,
        title TEXT NOT NULL,
        target_amount REAL NOT NULL,
        current_savings REAL NOT NULL DEFAULT 0,
        initial_savings REAL NOT NULL DEFAULT 0,
        monthly_contribution REAL NOT NULL DEFAULT 0,
        target_date TEXT NOT NULL,
        selected_plan TEXT DEFAULT 'Balanced',
        status TEXT NOT NULL DEFAULT 'In Progress',
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS sandbox_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,
        title TEXT NOT NULL,
        action_type TEXT NOT NULL,
        description TEXT NOT NULL,
        impact_summary TEXT NOT NULL,
        projected_savings_delta REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'PENDING',
        payload_json TEXT NOT NULL,
        proposed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at TIMESTAMP,
        resolved_by_role TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        user_role TEXT NOT NULL,
        action_type TEXT NOT NULL,
        resource TEXT NOT NULL,
        status TEXT NOT NULL,
        details TEXT NOT NULL,
        ip_address TEXT DEFAULT '127.0.0.1'
    );

    CREATE TABLE IF NOT EXISTS monthly_budgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,
        category TEXT NOT NULL,
        budget_limit REAL NOT NULL,
        month_year TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_monthly_budgets_unique
        ON monthly_budgets(user_id, category, month_year);

    CREATE TABLE IF NOT EXISTS recurring_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,
        type TEXT NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        description TEXT NOT NULL,
        day_of_month INTEGER NOT NULL DEFAULT 1,
        active INTEGER NOT NULL DEFAULT 1,
        last_generated TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS app_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_transactions_user_date ON transactions(user_id, date);
    CREATE INDEX IF NOT EXISTS idx_transactions_user_type_cat ON transactions(user_id, type, category);

    DROP VIEW IF EXISTS effective_transactions;
    CREATE VIEW effective_transactions AS
    SELECT id, user_id, category, amount, type, description, date, is_essential, is_anomaly, anomaly_reason, source, created_at
    FROM transactions
    WHERE COALESCE(source, 'manual') != 'summary'
    UNION ALL
    SELECT
        s.id,
        s.user_id,
        s.category,
        s.amount - COALESCE((
            SELECT SUM(d.amount)
            FROM transactions d
            WHERE d.user_id = s.user_id
              AND d.type = s.type
              AND LOWER(d.category) = LOWER(s.category)
              AND substr(d.date, 1, 7) = substr(s.date, 1, 7)
              AND COALESCE(d.source, 'manual') != 'summary'
        ), 0) AS amount,
        s.type,
        s.description,
        s.date,
        s.is_essential,
        s.is_anomaly,
        s.anomaly_reason,
        s.source,
        s.created_at
    FROM transactions s
    WHERE s.source = 'summary'
      AND (s.amount - COALESCE((
            SELECT SUM(d.amount)
            FROM transactions d
            WHERE d.user_id = s.user_id
              AND d.type = s.type
              AND LOWER(d.category) = LOWER(s.category)
              AND substr(d.date, 1, 7) = substr(s.date, 1, 7)
              AND COALESCE(d.source, 'manual') != 'summary'
      ), 0)) > 0;
    """)

    # Idempotent migration for existing users table
    cursor.execute("PRAGMA table_info(users)")
    existing_cols = {row["name"] for row in cursor.fetchall()}
    
    migrations = [
        ("onboarded", "INTEGER DEFAULT 0"),
        ("est_monthly_expense", "REAL DEFAULT 0"),
        ("risk_profile", "TEXT DEFAULT 'moderate'"),
        ("expected_return", "REAL DEFAULT 0.05"),
        ("volatility", "REAL DEFAULT 0.10"),
        ("inflation", "REAL DEFAULT 0.03"),
        ("emergency_months", "INTEGER DEFAULT 6"),
        ("currency", "TEXT DEFAULT 'USD'")
    ]
    for col_name, col_def in migrations:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")

    # Idempotent migration for goals.initial_savings
    cursor.execute("PRAGMA table_info(goals)")
    goal_cols = {row["name"] for row in cursor.fetchall()}
    if "initial_savings" not in goal_cols:
        cursor.execute("ALTER TABLE goals ADD COLUMN initial_savings REAL DEFAULT 0")
        cursor.execute("UPDATE goals SET initial_savings = current_savings WHERE initial_savings = 0")

    # Idempotent migration for transactions.source
    cursor.execute("PRAGMA table_info(transactions)")
    tx_cols = {row["name"] for row in cursor.fetchall()}
    if "source" not in tx_cols:
        cursor.execute("ALTER TABLE transactions ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")

    # Ensure default categories exist only when categories table is empty
    cursor.execute("SELECT COUNT(*) FROM categories")
    if cursor.fetchone()[0] == 0:
        for cat in DEFAULT_CATEGORIES:
            cursor.execute("INSERT OR IGNORE INTO categories (name, type, is_essential, icon) VALUES (?, ?, ?, ?)", cat)

    # Ensure user 1 exists
    cursor.execute("SELECT COUNT(*) FROM users WHERE id = 1")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO users (id, username, full_name, role, monthly_income, onboarded, est_monthly_expense, risk_profile, expected_return, volatility, inflation, emergency_months, currency)
            VALUES (1, 'me', 'New User', 'User', 0, 0, 0, 'moderate', 0.05, 0.10, 0.03, 6, 'USD')
        """)

    conn.commit()

    if force_reseed:
        clear_user_data(conn)
        seed_demo_data(conn)
    elif os.getenv("SEED_DEMO") == "1":
        cursor.execute("SELECT value FROM app_meta WHERE key = 'demo_seeded'")
        row = cursor.fetchone()
        if not row or row[0] != "1":
            clear_user_data(conn)
            seed_demo_data(conn)

    conn.close()

def clear_user_data(conn):
    """Deletes all data for user 1, resets profile, and deletes custom categories."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM transactions WHERE user_id = 1")
    cursor.execute("DELETE FROM goals WHERE user_id = 1")
    cursor.execute("DELETE FROM sandbox_proposals WHERE user_id = 1")
    cursor.execute("DELETE FROM monthly_budgets WHERE user_id = 1")
    cursor.execute("DELETE FROM recurring_rules WHERE user_id = 1")
    cursor.execute("DELETE FROM sqlite_sequence WHERE name IN ('transactions','goals','sandbox_proposals','monthly_budgets','recurring_rules')")

    default_cat_names = [cat[0] for cat in DEFAULT_CATEGORIES]
    placeholders = ",".join("?" * len(default_cat_names))
    cursor.execute(f"DELETE FROM categories WHERE name NOT IN ({placeholders})", default_cat_names)
    
    cursor.execute("""
        UPDATE users
        SET username = 'me', full_name = 'New User', role = 'User',
            monthly_income = 0, onboarded = 0, est_monthly_expense = 0,
            risk_profile = 'moderate', expected_return = 0.05, volatility = 0.10,
            inflation = 0.03, emergency_months = 6, currency = 'USD'
        WHERE id = 1
    """)
    conn.commit()

def seed_demo_data(conn):
    """Seeds realistic USD demo financial data for user 1."""
    cursor = conn.cursor()
    rng = random.Random(42)

    # Update user 1 with demo profile
    cursor.execute("""
        UPDATE users
        SET username = 'demo_user', full_name = 'Alex Morgan', role = 'User',
            monthly_income = 5000, onboarded = 1, est_monthly_expense = 2750,
            risk_profile = 'moderate', expected_return = 0.05, volatility = 0.10,
            inflation = 0.03, emergency_months = 6, currency = 'USD'
        WHERE id = 1
    """)

    base_date = datetime.now()

    # 1. Insert Goals first
    target_dt1 = add_months(base_date, 12).strftime("%Y-%m-%d")
    target_dt2 = add_months(base_date, 8).strftime("%Y-%m-%d")
    cursor.execute("""
        INSERT INTO goals (user_id, title, target_amount, current_savings, initial_savings, monthly_contribution, target_date, selected_plan, status, notes)
        VALUES (1, 'Emergency Fund (6 Months Expenses)', 18000, 6500, 6500, 800, ?, 'Balanced', 'In Progress', 'High priority safety net for unexpected healthcare or career transitions.')
    """, (target_dt1,))
    first_goal_id = cursor.lastrowid

    cursor.execute("""
        INSERT INTO goals (user_id, title, target_amount, current_savings, initial_savings, monthly_contribution, target_date, selected_plan, status, notes)
        VALUES (1, 'Japan Autumn Travel Trip 2027', 4000, 1200, 1200, 400, ?, 'Conservative', 'In Progress', 'Flight tickets, hotel accommodation in Tokyo & Kyoto.')
    """, (target_dt2,))

    # 2. Generate transactions
    transactions = []
    today_str = base_date.strftime("%Y-%m-%d")

    for m in range(12, 0, -1):
        ref_date = add_months(base_date, -m + 1)
        if ref_date > base_date:
            continue
        
        salary_date = ref_date.replace(day=1).strftime("%Y-%m-%d")
        if salary_date <= today_str:
            transactions.append((1, 'Salary', 4200.0, 'income', 'Primary Monthly Salary', salary_date, 1, 0, None))

        freelance_date = ref_date.replace(day=min(15, ref_date.day)).strftime("%Y-%m-%d")
        if freelance_date <= today_str:
            transactions.append((1, 'Freelance / Bonus', 800.0, 'income', 'Consulting & Freelance Work', freelance_date, 1, 0, None))

        rent_date = ref_date.replace(day=2).strftime("%Y-%m-%d")
        if rent_date <= today_str:
            transactions.append((1, 'Rent & Housing', 1400.0, 'expense', 'Monthly Apartment Rent', rent_date, 1, 0, None))

        util_amt = round(160.0 * (1 + rng.uniform(-0.10, 0.10)), 2)
        util_date = ref_date.replace(day=6).strftime("%Y-%m-%d")
        if util_date <= today_str:
            transactions.append((1, 'Utilities & Bills', util_amt, 'expense', 'Electricity, Water & Internet', util_date, 1, 0, None))

        trans_amt = round(120.0 * (1 + rng.uniform(-0.10, 0.10)), 2)
        trans_date = ref_date.replace(day=12).strftime("%Y-%m-%d")
        if trans_date <= today_str:
            transactions.append((1, 'Transportation & Fuel', trans_amt, 'expense', 'Metro Pass & Fuel', trans_date, 1, 0, None))

        gym_date = ref_date.replace(day=3).strftime("%Y-%m-%d")
        if gym_date <= today_str:
            transactions.append((1, 'Health & Fitness', 40.0, 'expense', 'Gym Membership', gym_date, 1, 0, None))
        sub_date = ref_date.replace(day=8).strftime("%Y-%m-%d")
        if sub_date <= today_str:
            transactions.append((1, 'Online Subscriptions', 25.0, 'expense', 'Streaming & Music Services', sub_date, 0, 0, None))

        for week in range(4):
            day_num = min(28, 4 + week * 7)
            g_date = ref_date.replace(day=day_num).strftime("%Y-%m-%d")
            if g_date <= today_str:
                g_amt = round(200.0 * (1 + rng.uniform(-0.10, 0.10)), 2)
                transactions.append((1, 'Groceries & Food', g_amt, 'expense', f'Weekly Grocery Restock (Week {week+1})', g_date, 1, 0, None))

        # Dining: 4-5 visits/month at $70-$110
        for d_idx in range(rng.randint(4, 5)):
            d_day = min(28, 4 + d_idx * 5 + rng.randint(0, 2))
            d_date = ref_date.replace(day=d_day).strftime("%Y-%m-%d")
            if d_date <= today_str:
                d_amt = round(rng.uniform(70.0, 110.0), 2)
                transactions.append((1, 'Dining & Cafes', d_amt, 'expense', 'Restaurant & Coffee', d_date, 0, 0, None))

        # Entertainment: $100-$180/month (1-2 events)
        ent_tot = round(rng.uniform(100.0, 180.0), 2)
        ent_day = min(28, 14)
        ent_date = ref_date.replace(day=ent_day).strftime("%Y-%m-%d")
        if ent_date <= today_str:
            transactions.append((1, 'Entertainment & Leisure', ent_tot, 'expense', 'Cinema & Weekend Activities', ent_date, 0, 0, None))

        # Shopping: $200-$350/month (2-3 purchases)
        num_shops = rng.randint(2, 3)
        target_shop_tot = rng.uniform(200.0, 350.0)
        shop_weights = [rng.uniform(0.5, 1.5) for _ in range(num_shops)]
        sum_w = sum(shop_weights)
        for s_idx, w in enumerate(shop_weights):
            shop_day = min(28, 8 + s_idx * 8 + rng.randint(0, 3))
            shop_date = ref_date.replace(day=shop_day).strftime("%Y-%m-%d")
            if shop_date <= today_str:
                shop_amt = round(target_shop_tot * (w / sum_w), 2)
                transactions.append((1, 'Shopping & Gadgets', shop_amt, 'expense', 'Clothing & Household Supplies', shop_date, 0, 0, None))

    # 2 High-value transactions for anomaly detection
    ipad_date = (base_date - timedelta(days=12)).strftime("%Y-%m-%d")
    if ipad_date <= today_str:
        transactions.append((1, 'Shopping & Gadgets', 1200.0, 'expense', 'Apple iPad Air M2 (Impulse Purchase)', ipad_date, 0, 0, None))
    
    party_date = (base_date - timedelta(days=5)).strftime("%Y-%m-%d")
    if party_date <= today_str:
        transactions.append((1, 'Dining & Cafes', 350.0, 'expense', 'Rooftop VIP Lounge Party', party_date, 0, 0, None))

    cursor.executemany("""
        INSERT INTO transactions (user_id, category, amount, type, description, date, is_essential, is_anomaly, anomaly_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, transactions)

    conn.commit()

    # Build seed proposal with the SAME logic as POST /api/sandbox/generate
    from app.services.sandbox import build_discretionary_proposal
    try:
        p_dict = build_discretionary_proposal(conn, user_id=1)
        payload_str = json.dumps(p_dict["payload"])
        cursor.execute("""
            INSERT INTO sandbox_proposals (user_id, title, action_type, description, impact_summary, projected_savings_delta, status, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
        """, (
            1,
            p_dict["title"],
            p_dict["action_type"],
            p_dict["description"],
            p_dict["impact_summary"],
            p_dict["projected_savings_delta"],
            payload_str
        ))
    except Exception as e:
        print(f"Warning: could not build seed proposal: {e}")

    # Set demo_seeded marker in app_meta
    cursor.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('demo_seeded', '1')")

    cursor.execute("""
        INSERT INTO audit_logs (timestamp, user_role, action_type, resource, status, details, ip_address) VALUES
        (datetime('now'), 'Admin', 'DATABASE_SEED', 'SYSTEM', 'SUCCESS', 'Loaded demo USD dataset with 12 months history', '127.0.0.1')
    """)

    conn.commit()

    # Call detect_spending_anomalies via local import
    from app.services.anomaly import detect_spending_anomalies
    detect_spending_anomalies(user_id=1, threshold_z=3.5)

