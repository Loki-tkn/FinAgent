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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,
        title TEXT NOT NULL,
        target_amount REAL NOT NULL,
        current_savings REAL NOT NULL DEFAULT 0,
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

    # Ensure default categories exist
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

    if force_reseed or os.getenv("SEED_DEMO") == "1":
        clear_user_data(conn)
        seed_demo_data(conn)

    conn.close()

def clear_user_data(conn):
    """Deletes all data for user 1 and resets profile to blank state."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM transactions WHERE user_id = 1")
    cursor.execute("DELETE FROM goals WHERE user_id = 1")
    cursor.execute("DELETE FROM sandbox_proposals WHERE user_id = 1")
    cursor.execute("DELETE FROM monthly_budgets WHERE user_id = 1")
    
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

    # Generate 12 months history
    base_date = datetime.now()
    transactions = []

    for m in range(12, 0, -1):
        month_offset_days = (m - 1) * 30
        ref_date = base_date - timedelta(days=month_offset_days)
        
        salary_date = (ref_date.replace(day=1)).strftime("%Y-%m-%d")
        transactions.append((1, 'Salary', 4200.0, 'income', 'Primary Monthly Salary', salary_date, 1, 0, None))

        freelance_day = min(15, (base_date - timedelta(days=month_offset_days)).day)
        freelance_date = (ref_date.replace(day=max(1, freelance_day))).strftime("%Y-%m-%d")
        if datetime.strptime(freelance_date, "%Y-%m-%d") > base_date:
            freelance_date = base_date.strftime("%Y-%m-%d")
        transactions.append((1, 'Freelance / Bonus', 800.0, 'income', 'Consulting & Freelance Work', freelance_date, 1, 0, None))

        rent_date = (ref_date.replace(day=2)).strftime("%Y-%m-%d")
        transactions.append((1, 'Rent & Housing', 1400.0, 'expense', 'Monthly Apartment Rent', rent_date, 1, 0, None))

        util_amt = round(160.0 * (1 + rng.uniform(-0.10, 0.10)), 2)
        util_date = (ref_date.replace(day=6)).strftime("%Y-%m-%d")
        transactions.append((1, 'Utilities & Bills', util_amt, 'expense', 'Electricity, Water & Internet', util_date, 1, 0, None))

        trans_amt = round(120.0 * (1 + rng.uniform(-0.10, 0.10)), 2)
        trans_date = (ref_date.replace(day=12)).strftime("%Y-%m-%d")
        transactions.append((1, 'Transportation & Fuel', trans_amt, 'expense', 'Metro Pass & Fuel', trans_date, 1, 0, None))

        gym_date = (ref_date.replace(day=3)).strftime("%Y-%m-%d")
        transactions.append((1, 'Health & Fitness', 40.0, 'expense', 'Gym Membership', gym_date, 1, 0, None))
        sub_date = (ref_date.replace(day=8)).strftime("%Y-%m-%d")
        transactions.append((1, 'Online Subscriptions', 25.0, 'expense', 'Streaming & Music Services', sub_date, 0, 0, None))

        for week in range(4):
            day_num = min(28, 4 + week * 7)
            g_date = (ref_date.replace(day=day_num)).strftime("%Y-%m-%d")
            g_amt = round(200.0 * (1 + rng.uniform(-0.10, 0.10)), 2)
            transactions.append((1, 'Groceries & Food', g_amt, 'expense', f'Weekly Grocery Restock (Week {week+1})', g_date, 1, 0, None))

        for d_idx in range(rng.randint(2, 3)):
            d_day = min(28, 7 + d_idx * 9)
            d_date = (ref_date.replace(day=d_day)).strftime("%Y-%m-%d")
            d_amt = round(rng.uniform(60.0, 90.0), 2)
            transactions.append((1, 'Dining & Cafes', d_amt, 'expense', 'Restaurant & Coffee', d_date, 0, 0, None))

        ent_day = min(28, 14)
        ent_date = (ref_date.replace(day=ent_day)).strftime("%Y-%m-%d")
        ent_amt = round(rng.uniform(70.0, 120.0), 2)
        transactions.append((1, 'Entertainment & Leisure', ent_amt, 'expense', 'Cinema & Weekend Activities', ent_date, 0, 0, None))

        shop_day = min(28, 18)
        shop_date = (ref_date.replace(day=shop_day)).strftime("%Y-%m-%d")
        shop_amt = round(rng.uniform(60.0, 110.0), 2)
        transactions.append((1, 'Shopping & Gadgets', shop_amt, 'expense', 'Clothing & Household Supplies', shop_date, 0, 0, None))

    # 2 Anomalies in latest month
    ipad_date = (base_date - timedelta(days=12)).strftime("%Y-%m-%d")
    transactions.append((1, 'Shopping & Gadgets', 1200.0, 'expense', 'Apple iPad Air M2 (Impulse Purchase)', ipad_date, 0, 1, 'Z-Score: +3.85 | Single expense $1,200 is 10x higher than typical category average'))
    
    party_date = (base_date - timedelta(days=5)).strftime("%Y-%m-%d")
    transactions.append((1, 'Dining & Cafes', 350.0, 'expense', 'Rooftop VIP Lounge Party', party_date, 0, 1, 'Z-Score: +2.95 | Single dining bill exceeds 4.5x typical restaurant expense'))

    cursor.executemany("""
        INSERT INTO transactions (user_id, category, amount, type, description, date, is_essential, is_anomaly, anomaly_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, transactions)

    target_dt1 = (base_date + timedelta(days=365)).strftime("%Y-%m-%d")
    target_dt2 = (base_date + timedelta(days=240)).strftime("%Y-%m-%d")
    cursor.execute("""
        INSERT INTO goals (user_id, title, target_amount, current_savings, monthly_contribution, target_date, selected_plan, status, notes)
        VALUES 
        (1, 'Emergency Fund (6 Months Expenses)', 18000, 6500, 800, ?, 'Balanced', 'In Progress', 'High priority safety net for unexpected healthcare or career transitions.'),
        (1, 'Japan Autumn Travel Trip 2027', 4000, 1200, 400, ?, 'Conservative', 'In Progress', 'Flight tickets, hotel accommodation in Tokyo & Kyoto.')
    """, (target_dt1, target_dt2))

    proposal_payload = json.dumps({
        "goal_id": 1,
        "category_adjustments": {
            "Dining & Cafes": {"current": 450, "proposed": 200, "delta": -250},
            "Shopping & Gadgets": {"current": 400, "proposed": 100, "delta": -300},
            "Entertainment & Leisure": {"current": 200, "proposed": 100, "delta": -100}
        },
        "total_monthly_savings_increase": 650,
        "goal_acceleration_months": 2.5
    })

    cursor.execute("""
        INSERT INTO sandbox_proposals (user_id, title, action_type, description, impact_summary, projected_savings_delta, status, payload_json)
        VALUES
        (1, 'Discretionary Spending Optimization Plan', 'BUDGET_REALLOCATION',
         'AI Agent detected $650/month in non-essential dining and shopping. Propose reallocating this to Emergency Fund goal.',
         '+$650/month savings boost | Accelerates Emergency Fund target date by 2.5 months',
         650, 'PENDING', ?)
    """, (proposal_payload,))

    cursor.execute("""
        INSERT INTO audit_logs (timestamp, user_role, action_type, resource, status, details, ip_address) VALUES
        (datetime('now'), 'Admin', 'DATABASE_SEED', 'SYSTEM', 'SUCCESS', 'Loaded demo USD dataset with 12 months history', '127.0.0.1')
    """)

    conn.commit()
