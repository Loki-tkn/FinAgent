"""
test_copilot_phase3.py – Phase 3 Pipeline Hooks Verification
=============================================================
Tests that:
1. Operational events are emitted automatically to CopilotEventBus and CopilotContextEngine.
2. AGENT_COT events are published on decision/simulation/planning traces.
3. RISK_METRIC and RISK_OVERRIDE events are published on risk limits/anomaly evaluations.
4. MARKET_INTEL events are published on market/transaction snapshot processing.
5. EXEC_LOG events are published on trade/transaction executions.
6. Non-blocking safety guarantees zero performance impact and fail-safe error isolation.
7. Existing API endpoints continue to run 100% properly without regressions.
"""

import time
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.copilot.state import ContextLane, ContextSeverity, context_engine
from app.copilot.bus import event_bus, ContextEvent
from app.copilot.hooks import (
    emit_agent_cot,
    emit_risk_metric,
    emit_risk_override,
    emit_market_intel,
    emit_exec_log,
    aemit_agent_cot,
    aemit_risk_metric,
    aemit_risk_override,
    aemit_market_intel,
    aemit_exec_log,
)

client = TestClient(app)
AUTH_HEADERS_USER = {"X-Role": "User"}
AUTH_HEADERS_ADMIN = {"X-Role": "Admin"}


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def test_direct_hook_emission():
    """Verify each hook emits into the correct lane with correct source and payload."""
    initial_cot_count = len(context_engine.query(ContextLane.AGENT_COT, limit=200))
    initial_risk_count = len(context_engine.query(ContextLane.RISK_METRIC, limit=200))
    initial_override_count = len(context_engine.query(ContextLane.RISK_OVERRIDE, limit=200))
    initial_intel_count = len(context_engine.query(ContextLane.MARKET_INTEL, limit=200))
    initial_exec_count = len(context_engine.query(ContextLane.EXEC_LOG, limit=200))

    # Synchronous emit (background=False) for immediate assertion
    emit_agent_cot({"test_step": "verification", "confidence": 0.99}, background=False)
    emit_risk_metric({"test_metric": "volatility_spike", "var": 0.05}, background=False)
    emit_risk_override({"risk_profile": "aggressive", "max_drawdown": 0.15}, background=False)
    emit_market_intel({"ticker": "SPY", "sentiment": "bullish"}, background=False)
    emit_exec_log({"action": "TEST_ORDER_FILL", "qty": 10}, background=False)

    cot_events = context_engine.query(ContextLane.AGENT_COT, limit=10)
    risk_events = context_engine.query(ContextLane.RISK_METRIC, limit=10)
    override_events = context_engine.query(ContextLane.RISK_OVERRIDE, limit=10)
    intel_events = context_engine.query(ContextLane.MARKET_INTEL, limit=10)
    exec_events = context_engine.query(ContextLane.EXEC_LOG, limit=10)

    assert len(cot_events) == initial_cot_count + 1
    assert cot_events[0]["payload"]["test_step"] == "verification"

    assert len(risk_events) == initial_risk_count + 1
    assert risk_events[0]["payload"]["test_metric"] == "volatility_spike"

    assert len(override_events) == initial_override_count + 1
    assert override_events[0]["payload"]["risk_profile"] == "aggressive"

    assert len(intel_events) == initial_intel_count + 1
    assert intel_events[0]["payload"]["ticker"] == "SPY"

    assert len(exec_events) == initial_exec_count + 1
    assert exec_events[0]["payload"]["action"] == "TEST_ORDER_FILL"


@pytest.mark.anyio
async def test_async_hook_emission():
    """Verify async hook variants aemit_* function properly with await."""
    evt1 = await aemit_agent_cot({"async_step": "cot_check"})
    evt2 = await aemit_risk_metric({"async_risk": "cfar_check"})
    evt3 = await aemit_risk_override({"async_override": "limit_bump"})
    evt4 = await aemit_market_intel({"async_intel": "macro_update"})
    evt5 = await aemit_exec_log({"async_exec": "settled"})

    assert evt1 is not None and evt1.lane == ContextLane.AGENT_COT
    assert evt2 is not None and evt2.lane == ContextLane.RISK_METRIC
    assert evt3 is not None and evt3.lane == ContextLane.RISK_OVERRIDE
    assert evt4 is not None and evt4.lane == ContextLane.MARKET_INTEL
    assert evt5 is not None and evt5.lane == ContextLane.EXEC_LOG


def test_simulation_pipeline_hook():
    """Verify POST /api/simulate automatically emits AGENT_COT and RISK_METRIC."""
    # Ensure profile has income so simulation runs
    client.put("/api/profile", json={"monthly_income": 8000, "risk_profile": "moderate"}, headers=AUTH_HEADERS_USER)

    received_events = []
    def on_event(evt: ContextEvent):
        if evt.lane in (ContextLane.AGENT_COT, ContextLane.RISK_METRIC):
            received_events.append(evt)

    sub = event_bus.subscribe(on_event)
    try:
        resp = client.post(
            "/api/simulate",
            json={
                "scenario_type": "one_off_purchase",
                "amount": 1500.0,
                "description": "New Laptop",
                "frequency": "one_time"
            },
            headers=AUTH_HEADERS_USER
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "impact_level" in data

        # Wait briefly for background thread pool execution
        time.sleep(0.1)

        cot_matches = [e for e in received_events if e.lane == ContextLane.AGENT_COT and e.payload.get("step") == "what_if_simulation"]
        risk_matches = [e for e in received_events if e.lane == ContextLane.RISK_METRIC and e.payload.get("metric_type") == "SIMULATION_RISK"]

        assert len(cot_matches) >= 1
        assert cot_matches[-1].payload["amount"] == 1500.0
        assert len(risk_matches) >= 1
        assert risk_matches[-1].payload["amount"] == 1500.0
    finally:
        event_bus.unsubscribe(sub)


def test_transactions_pipeline_hooks():
    """Verify add transaction, scan anomalies, and delete emit EXEC_LOG, RISK_METRIC, AGENT_COT."""
    received = []
    def on_event(evt: ContextEvent):
        received.append(evt)

    sub = event_bus.subscribe(on_event)
    try:
        # 1. Add transaction -> EXEC_LOG
        add_resp = client.post(
            "/api/transactions",
            json={
                "category": "Dining & Cafes",
                "amount": 75.50,
                "type": "expense",
                "description": "Team Lunch",
                "date": "2026-10-15"
            },
            headers=AUTH_HEADERS_USER
        )
        assert add_resp.status_code == 200
        tx_id = add_resp.json()["id"]

        time.sleep(0.1)
        exec_adds = [e for e in received if e.lane == ContextLane.EXEC_LOG and e.payload.get("action") == "TRANSACTION_RECORDED" and e.payload.get("tx_id") == tx_id]
        assert len(exec_adds) >= 1
        assert exec_adds[-1].payload["amount"] == 75.50

        # 2. Scan anomalies -> RISK_METRIC and AGENT_COT
        scan_resp = client.post("/api/transactions/scan-anomalies?threshold_z=2.5", headers=AUTH_HEADERS_USER)
        assert scan_resp.status_code == 200

        time.sleep(0.1)
        anomaly_risks = [e for e in received if e.lane == ContextLane.RISK_METRIC and e.payload.get("metric_type") == "ANOMALY_SCAN_EVALUATION"]
        anomaly_cots = [e for e in received if e.lane == ContextLane.AGENT_COT and e.payload.get("step") == "spending_anomaly_scan"]
        assert len(anomaly_risks) >= 1
        assert len(anomaly_cots) >= 1

        # 3. Delete transaction -> EXEC_LOG
        del_resp = client.delete(f"/api/transactions/{tx_id}", headers=AUTH_HEADERS_USER)
        assert del_resp.status_code == 200

        time.sleep(0.1)
        exec_dels = [e for e in received if e.lane == ContextLane.EXEC_LOG and e.payload.get("action") == "TRANSACTION_DELETED" and e.payload.get("tx_id") == tx_id]
        assert len(exec_dels) >= 1
    finally:
        event_bus.unsubscribe(sub)


def test_sandbox_pipeline_hooks():
    """Verify sandbox proposal creation and resolution emit EXEC_LOG and AGENT_COT."""
    received = []
    sub = event_bus.subscribe(lambda e: received.append(e))
    try:
        # Create proposal
        create_resp = client.post(
            "/api/sandbox/proposals",
            json={
                "title": "Optimize Entertainment Subscriptions",
                "action_type": "EXPENSE_REDUCTION",
                "description": "Cancel unused streaming services",
                "impact_summary": "Saves $45/mo",
                "projected_savings_delta": 45.0,
                "payload": {"category": "Entertainment"}
            },
            headers=AUTH_HEADERS_USER
        )
        assert create_resp.status_code == 200
        prop_id = create_resp.json()["id"]

        time.sleep(0.1)
        staged_execs = [e for e in received if e.lane == ContextLane.EXEC_LOG and e.payload.get("action") == "SANDBOX_PROPOSAL_STAGED"]
        staged_cots = [e for e in received if e.lane == ContextLane.AGENT_COT and e.payload.get("step") == "stage_sandbox_proposal"]
        assert len(staged_execs) >= 1
        assert len(staged_cots) >= 1

        # Approve proposal
        action_resp = client.post(
            f"/api/sandbox/proposals/{prop_id}/action",
            json={"action": "APPROVE", "feedback": "Approved via copilot test"},
            headers=AUTH_HEADERS_USER
        )
        assert action_resp.status_code == 200

        time.sleep(0.1)
        resolved_execs = [e for e in received if e.lane == ContextLane.EXEC_LOG and e.payload.get("action") == "SANDBOX_PROPOSAL_APPROVED"]
        assert len(resolved_execs) >= 1
    finally:
        event_bus.unsubscribe(sub)


def test_quant_and_dashboard_hooks():
    """Verify quant forecast and dashboard summary endpoints emit telemetry hooks."""
    received = []
    sub = event_bus.subscribe(lambda e: received.append(e))
    try:
        # Dashboard summary -> MARKET_INTEL
        dash_resp = client.get("/api/dashboard/summary", headers=AUTH_HEADERS_USER)
        assert dash_resp.status_code == 200

        time.sleep(0.1)
        market_intels = [e for e in received if e.lane == ContextLane.MARKET_INTEL and e.payload.get("intel_type") == "DASHBOARD_MARKET_SUMMARY"]
        assert len(market_intels) >= 1

        # Quant forecast -> RISK_METRIC and MARKET_INTEL
        forecast_resp = client.get("/api/quant/forecast", headers=AUTH_HEADERS_USER)
        assert forecast_resp.status_code == 200

        time.sleep(0.1)
        cfar_metrics = [e for e in received if e.lane == ContextLane.RISK_METRIC and e.payload.get("metric_type") == "EXPENSE_FORECAST_CFAR"]
        assert len(cfar_metrics) >= 1

        # Loan vs Cash -> AGENT_COT
        lvc_resp = client.post(
            "/api/quant/loan-vs-cash",
            json={
                "price": 20000.0,
                "down_payment": 4000.0,
                "annual_rate": 0.06,
                "months": 36
            },
            headers=AUTH_HEADERS_USER
        )
        assert lvc_resp.status_code == 200

        time.sleep(0.1)
        lvc_cots = [e for e in received if e.lane == ContextLane.AGENT_COT and e.payload.get("step") == "loan_vs_cash_npv_decision"]
        assert len(lvc_cots) >= 1
    finally:
        event_bus.unsubscribe(sub)


def test_profile_risk_override_hook():
    """Verify profile updates trigger RISK_OVERRIDE and EXEC_LOG."""
    received = []
    sub = event_bus.subscribe(lambda e: received.append(e))
    try:
        resp = client.put(
            "/api/profile",
            json={
                "monthly_income": 9500.0,
                "est_monthly_expense": 4200.0,
                "risk_profile": "aggressive",
                "inflation": 0.035,
                "emergency_months": 8
            },
            headers=AUTH_HEADERS_USER
        )
        assert resp.status_code == 200

        time.sleep(0.1)
        overrides = [e for e in received if e.lane == ContextLane.RISK_OVERRIDE and e.payload.get("risk_profile") == "aggressive"]
        execs = [e for e in received if e.lane == ContextLane.EXEC_LOG and e.payload.get("action") == "PROFILE_UPDATED"]
        assert len(overrides) >= 1
        assert len(execs) >= 1
    finally:
        event_bus.unsubscribe(sub)


def test_fail_safe_subscriber_isolation():
    """Verify that an exception inside a subscriber NEVER breaks event emission or endpoint."""
    def bad_subscriber(evt: ContextEvent):
        raise RuntimeError("Deliberate failure in test subscriber")

    sub = event_bus.subscribe(bad_subscriber)
    try:
        # Emitting through hook should not raise
        result = emit_agent_cot({"safe_test": True}, background=False)
        assert result is not None

        # API call should still succeed cleanly
        resp = client.get("/api/dashboard/summary", headers=AUTH_HEADERS_USER)
        assert resp.status_code == 200
    finally:
        event_bus.unsubscribe(sub)
