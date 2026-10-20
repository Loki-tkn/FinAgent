import pytest
import uuid
from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db

client = TestClient(app)

def setup_module():
    init_db(force_reseed=False)

def test_profile_and_custom_category_api():
    # 1. Profile GET
    prof_res = client.get("/api/profile", headers={"X-User-Role": "User"})
    assert prof_res.status_code == 200
    assert "onboarded" in prof_res.json()

    # 2. Profile PUT
    update_res = client.put("/api/profile", json={
        "full_name": "Test User",
        "monthly_income": 4000,
        "est_monthly_expense": 2500,
        "risk_profile": "moderate",
        "emergency_months": 6,
        "inflation": 0.03
    }, headers={"X-User-Role": "User"})
    assert update_res.status_code == 200

    # 3. Create Custom Category (unique name avoids collision with demo data)
    cat_name = f"Test-{uuid.uuid4().hex[:8]}"
    cat_res = client.post("/api/transactions/categories", json={
        "name": cat_name,
        "type": "expense",
        "is_essential": True
    }, headers={"X-User-Role": "User"})
    assert cat_res.status_code == 200
    assert cat_res.json()["name"] == cat_name

    # 4. Add Transaction with Custom Category (is_essential derived automatically)
    tx_res = client.post("/api/transactions", json={
        "category": cat_name,
        "amount": 80,
        "type": "expense",
        "description": "Dog food",
        "date": "2026-10-09"
    }, headers={"X-User-Role": "User"})
    assert tx_res.status_code == 200

    # 5. Non-existent category should return 422
    invalid_tx = client.post("/api/transactions", json={
        "category": "NonExistentCategory123",
        "amount": 50,
        "type": "expense",
        "description": "Invalid",
        "date": "2026-10-09"
    }, headers={"X-User-Role": "User"})
    assert invalid_tx.status_code == 422

def test_dashboard_summary_api():
    response = client.get("/api/dashboard/summary", headers={"X-User-Role": "User"})
    assert response.status_code == 200
    data = response.json()
    assert "metrics" in data
    assert "health_score" in data
    assert "cash_flow_trends" in data
    assert "income_from_transactions_avg" in data["metrics"]

def test_admin_demo_and_clear_data():
    # Load demo data
    load_res = client.post("/api/admin/load-demo-data", headers={"X-User-Role": "Admin"})
    assert load_res.status_code == 200
    assert "loaded successfully" in load_res.json()["message"]

    # Clear data
    clear_res = client.post("/api/admin/clear-data", headers={"X-User-Role": "Admin"})
    assert clear_res.status_code == 200
    assert "cleared successfully" in clear_res.json()["message"]
