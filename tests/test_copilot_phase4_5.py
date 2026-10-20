"""
test_copilot_phase4_5.py – Phase 4 & 5 Verification Suite
==========================================================
Tests:
1. PHASE 4: WebSocket Real-Time Event Streaming (/api/copilot/ws)
   • Connection handshake
   • Live event streaming from CopilotEventBus
   • Lane filtering via query params
   • Bidirectional actions: ping, chat, kill_switch, subscribe_lane
   • Clean disconnect & subscriber teardown (zero memory/subscriber leaks)

2. PHASE 5: Financial Guardrails & Safety Checks (app/copilot/guardrails.py)
   • Safe parameter bounds validation and auto-clamping
   • Rejection of dangerous/negative parameter overrides
   • AI reasoning safety filters (mitigating absolutist promises)
   • Mandatory regulatory financial disclaimer attachment
   • HTTP endpoint guardrails integration (/risk-override and /chat)
"""

import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.copilot.state import ContextLane, ContextSeverity, context_engine
from app.copilot.bus import event_bus, ContextEvent
from app.copilot.guardrails import (
    STANDARD_FINANCIAL_DISCLAIMER,
    attach_financial_disclaimer,
    validate_risk_override,
    sanitize_ai_reasoning,
)

client = TestClient(app)
AUTH_HEADERS_USER = {"X-Role": "User"}


# ===========================================================================
# 1. Phase 4: WebSocket Streaming Tests
# ===========================================================================

def test_websocket_handshake_and_ping():
    """Verify WebSocket handshake and ping-pong bidirectional action."""
    with client.websocket_connect("/api/copilot/ws?session_id=test-handshake") as ws:
        # Initial connect handshake
        conn_msg = ws.receive_json()
        assert conn_msg["type"] == "connected"
        assert conn_msg["session_id"] == "test-handshake"
        assert conn_msg["subscribed_lanes"] == "all"

        # Bidirectional ping
        ws.send_json({"action": "ping"})
        pong_msg = ws.receive_json()
        assert pong_msg["type"] == "pong"
        assert "timestamp" in pong_msg


def test_websocket_event_streaming():
    """Verify published events on CopilotEventBus stream to connected WebSocket client in real-time."""
    initial_sub_count = event_bus.subscriber_count()

    with client.websocket_connect("/api/copilot/ws?session_id=test-stream") as ws:
        # Handshake
        conn_msg = ws.receive_json()
        assert conn_msg["type"] == "connected"
        assert event_bus.subscriber_count() == initial_sub_count + 1

        # Publish an AGENT_COT event onto the bus
        event_bus.publish_agent_cot(
            trace={"step": "market_scan", "ticker": "ETH-USD", "score": 8.5},
            session_id="test-stream",
        )

        # Receive streamed event
        event_msg = ws.receive_json()
        assert event_msg["type"] == "event"
        assert event_msg["lane"] == "agent_cot"
        assert event_msg["payload"]["step"] == "market_scan"
        assert event_msg["payload"]["ticker"] == "ETH-USD"

    # Verify unsubscription cleanup on socket close
    assert event_bus.subscriber_count() == initial_sub_count


def test_websocket_lane_filtering():
    """Verify ?lanes= filter forwards only requested lanes."""
    with client.websocket_connect("/api/copilot/ws?lanes=risk_metric,exec_log") as ws:
        conn_msg = ws.receive_json()
        assert conn_msg["type"] == "connected"
        assert set(conn_msg["subscribed_lanes"]) == {"risk_metric", "exec_log"}

        # Publish AGENT_COT (should be ignored by filter)
        event_bus.publish_agent_cot(
            trace={"step": "ignored_cot"},
            session_id="filter-test",
        )

        # Publish RISK_METRIC (should be forwarded)
        event_bus.publish_risk_metric(
            metrics={"cfar_95": 1250.0},
            session_id="filter-test",
        )

        msg = ws.receive_json()
        assert msg["type"] == "event"
        assert msg["lane"] == "risk_metric"
        assert msg["payload"]["cfar_95"] == 1250.0


def test_websocket_chat_and_kill_switch_actions():
    """Verify chat queries and kill-switch activations over WebSocket."""
    with client.websocket_connect("/api/copilot/ws?session_id=test-actions") as ws:
        ws.receive_json()  # Handshake

        # Chat over WebSocket
        ws.send_json({"action": "chat", "message": "What is my financial health score?"})
        chat_msg = ws.receive_json()
        assert chat_msg["type"] == "chat_reply"
        assert "data" in chat_msg
        assert ("health" in chat_msg["data"]["intent"]) or (chat_msg["data"]["intent"] == "openrouter_llm")
        assert "Disclaimer:" in chat_msg["data"]["reply"]

        # Kill-switch over WebSocket
        ws.send_json({"action": "kill_switch", "reason": "Test WS emergency stop"})
        ks_msg = ws.receive_json()
        assert ks_msg["type"] == "kill_switch_activated"
        assert ks_msg["reason"] == "Test WS emergency stop"


# ===========================================================================
# 2. Phase 5: Financial Guardrails & Safety Checks Tests
# ===========================================================================

def test_guardrails_safe_bounds_validation():
    """Verify valid risk overrides pass without modification."""
    valid_overrides = {
        "expected_return": 0.08,
        "volatility": 0.15,
        "inflation": 0.03,
        "emergency_months": 6,
        "leverage": 1.5,
    }
    result = validate_risk_override(valid_overrides)
    assert result.passed is True
    assert len(result.violations) == 0
    assert len(result.warnings) == 0
    assert result.sanitized_payload == valid_overrides


def test_guardrails_out_of_bounds_clamping():
    """Verify parameters exceeding ceiling are clamped to safe boundaries with warnings."""
    breaching_overrides = {
        "expected_return": 0.50,   # Ceiling is 0.30
        "leverage": 5.0,           # Ceiling is 3.0
        "emergency_months": 48.0,  # Ceiling is 36.0
    }
    result = validate_risk_override(breaching_overrides, clamp_out_of_bounds=True)
    assert len(result.warnings) >= 3
    assert result.sanitized_payload["expected_return"] == 0.30
    assert result.sanitized_payload["leverage"] == 3.0
    assert result.sanitized_payload["emergency_months"] == 36.0


def test_guardrails_critical_negative_rejection():
    """Verify negative/reckless risk parameters trigger critical violations."""
    dangerous_overrides = {
        "expected_return": -0.05,  # Negative return limit
        "volatility": -0.10,       # Negative volatility
        "leverage": 20.0,          # Extreme leverage (> 2x ceiling)
    }
    result = validate_risk_override(dangerous_overrides)
    assert result.has_critical_violations is True
    assert result.passed is False


def test_ai_reasoning_safety_sanitization():
    """Verify dangerous absolutist guarantees are sanitized into probabilistic terms."""
    unsafe_text = "This strategy offers a guaranteed profit of 25% with 100% safe execution and risk-free returns."
    sanitized = sanitize_ai_reasoning(unsafe_text)

    assert "guaranteed profit" not in sanitized.lower()
    assert "100% safe" not in sanitized.lower()
    assert "risk-free" not in sanitized.lower()
    assert "projected potential returns" in sanitized.lower()


def test_financial_disclaimer_attachment():
    """Verify compliance disclaimer is appended and not duplicated."""
    raw_text = "Based on EWMA forecast, monthly expense is expected to remain around $2,400."
    with_disclaimer = attach_financial_disclaimer(raw_text)
    assert "Disclaimer:" in with_disclaimer

    # Second attachment should not duplicate
    double_attached = attach_financial_disclaimer(with_disclaimer)
    assert double_attached.count("Disclaimer:") == 1


def test_risk_override_endpoint_guardrail_enforcement():
    """Verify POST /api/copilot/risk-override integrates guardrail checks."""
    # 1. Critical dangerous inputs rejected with 422
    resp_reject = client.post(
        "/api/copilot/risk-override",
        json={
            "overrides": {"expected_return": -0.20, "leverage": 15.0},
            "session_id": "guardrail-test"
        },
        headers=AUTH_HEADERS_USER
    )
    assert resp_reject.status_code == 422
    assert "rejected by safety guardrails" in resp_reject.json()["detail"]

    # 2. Moderate breach is clamped safely and returns warnings + disclaimer
    resp_clamp = client.post(
        "/api/copilot/risk-override",
        json={
            "overrides": {"expected_return": 0.35, "emergency_months": 40},
            "session_id": "guardrail-test"
        },
        headers=AUTH_HEADERS_USER
    )
    assert resp_clamp.status_code == 200
    data = resp_clamp.json()
    assert data["overrides"]["expected_return"] == 0.30
    assert data["overrides"]["emergency_months"] == 36.0
    assert len(data["warnings"]) >= 2
    assert data["disclaimer"] is not None


def test_chat_endpoint_includes_compliance_disclaimer():
    """Verify POST /api/copilot/chat replies contain compliance disclaimers."""
    resp = client.post(
        "/api/copilot/chat",
        json={"message": "What is my spending breakdown this month?"},
        headers=AUTH_HEADERS_USER
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "Disclaimer:" in data["reply"]
    assert data["disclaimer"] == STANDARD_FINANCIAL_DISCLAIMER
