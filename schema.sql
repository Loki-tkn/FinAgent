-- AI Personal Financial Agent Database Schema (SQLite)
-- All financial records in this system represent simulated demo data.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'User', -- 'Viewer', 'User', 'Admin'
    monthly_income REAL NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL, -- 'income' or 'expense'
    is_essential BOOLEAN NOT NULL DEFAULT 1, -- 1: Essential (rent, food, utility), 0: Discretionary (entertainment, shopping)
    icon TEXT DEFAULT 'tag'
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    category TEXT NOT NULL,
    amount REAL NOT NULL,
    type TEXT NOT NULL, -- 'income' or 'expense'
    description TEXT NOT NULL,
    date TEXT NOT NULL, -- YYYY-MM-DD
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
    target_date TEXT NOT NULL, -- YYYY-MM-DD
    selected_plan TEXT DEFAULT 'Balanced', -- 'Conservative', 'Balanced', 'Aggressive'
    status TEXT NOT NULL DEFAULT 'In Progress', -- 'In Progress', 'Completed', 'Paused'
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS sandbox_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    action_type TEXT NOT NULL, -- 'BUDGET_REALLOCATION', 'EXPENSE_REDUCTION', 'ONE_OFF_PURCHASE', 'GOAL_ADJUSTMENT'
    description TEXT NOT NULL,
    impact_summary TEXT NOT NULL,
    projected_savings_delta REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'APPROVED', 'REJECTED', 'APPLIED'
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
    status TEXT NOT NULL, -- 'SUCCESS', 'FAILED', 'APPROVED', 'REJECTED', 'UNAUTHORIZED'
    details TEXT NOT NULL,
    ip_address TEXT DEFAULT '127.0.0.1'
);

CREATE TABLE IF NOT EXISTS monthly_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    category TEXT NOT NULL,
    budget_limit REAL NOT NULL,
    month_year TEXT NOT NULL, -- YYYY-MM
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
