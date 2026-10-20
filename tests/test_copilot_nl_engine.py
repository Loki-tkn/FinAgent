"""
test_copilot_nl_engine.py – Comprehensive Unit & Integration Tests for CopilotNLEngine
====================================================================================
Tests cover:
1. Intent classification accuracy across all 10 intent categories & edge cases.
2. Entity extraction (_extract_event_id).
3. Individual intent handlers (health, goals, budget, anomalies, risk, CoT, kill-switch, etc.).
4. Data fetchers, including _fetch_health_score calculation.
5. CopilotNLEngine.explain_event() deep-dive explanation generation.
6. CopilotNLEngine.process() end-to-end pipeline (recording prompt, CoT generation, confidence scoring).
"""

from unittest.mock import MagicMock, patch
import pytest

from app.copilot.nl_engine import (
    nl_engine,
    _classify_intent,
    _extract_event_id,
    _fetch_health_score,
    _handle_help,
    _handle_unknown,
    _handle_kill_switch,
    _handle_query_context,
    _handle_query_health,
    _handle_query_goal,
    _handle_query_budget,
    _handle_query_anomaly,
    _handle_query_risk,
    _handle_query_cot,
    _handle_explain_decision,
)
from app.copilot.state import ContextLane, context_engine


# ===========================================================================
# 1. Intent Classification & Entity Extraction
# ===========================================================================

@pytest.mark.parametrize("query,expected_intent", [
    ("What is my financial health score?", "query_health"),
    ("How are my savings goals tracking?", "query_goal"),
    ("Are there any spending anomalies or unusual spikes?", "query_anomaly"),
    ("Show me the monthly budget breakdown", "query_budget"),
    ("What are my current risk metrics and drawdown?", "query_risk"),
    ("Show me the agent reasoning chain of thought traces", "query_cot"),
    ("Why did the agent make this decision?", "explain_decision"),
    ("Emergency kill switch halt agent!", "kill_switch"),
    ("Show me current context snapshot and state", "query_context"),
    ("Help me what can you do?", "help"),
    ("gibberish random 1234 xyz", "unknown"),
    ("", "unknown"),
])
def test_intent_classification(query, expected_intent):
    intent, confidence = _classify_intent(query)
    assert intent == expected_intent
    if expected_intent == "unknown":
        assert confidence == 0.0
    else:
        assert confidence > 0.0


def test_extract_event_id():
    assert _extract_event_id("explain event 42") == 42
    assert _extract_event_id("what happened in id 105?") == 105
    assert _extract_event_id("tell me about #77") == 77
    assert _extract_event_id("explain decision without any number") is None


# ===========================================================================
# 2. Intent Handlers
# ===========================================================================

def test_handle_help():
    reply, sources, conf = _handle_help("help", "session-1")
    assert "Copilot Capabilities" in reply
    assert conf == 1.0
    assert sources == []


def test_handle_unknown():
    reply, sources, conf = _handle_unknown("unknown", "session-1")
    assert "didn't understand your query" in reply
    assert conf == 0.20


def test_handle_kill_switch():
    reply, sources, conf = _handle_kill_switch("kill switch", "session-test")
    assert "POST /api/copilot/kill-switch" in reply
    assert "session-test" in reply
    assert conf == 0.80


def test_handle_query_context():
    reply, sources, conf = _handle_query_context("snapshot", "session-1")
    assert "Copilot Context Snapshot" in reply
    assert "Kill Switch:" in reply
    assert conf == 0.90


def test_handle_query_health_and_fetch_health():
    health = _fetch_health_score(user_id=1)
    assert isinstance(health, dict)
    assert "overall_score" in health
    assert "rating" in health

    reply, sources, conf = _handle_query_health("health score", "session-1")
    assert "Financial Health Score" in reply or "Profile setup required" in reply
    assert conf >= 0.70


def test_handle_query_goal():
    reply, sources, conf = _handle_query_goal("goals", "session-1")
    assert isinstance(reply, str)
    assert conf >= 0.85


def test_handle_query_budget():
    reply, sources, conf = _handle_query_budget("budget", "session-1")
    assert isinstance(reply, str)
    assert conf >= 0.70


def test_handle_query_anomaly():
    reply, sources, conf = _handle_query_anomaly("anomalies", "session-1")
    assert isinstance(reply, str)
    assert conf >= 0.80


def test_handle_query_risk():
    context_engine.record_risk_metric(
        metrics={"portfolio_risk": "low", "var_95": 120.5},
        session_id="risk-session"
    )
    reply, sources, conf = _handle_query_risk("risk metrics", "risk-session")
    assert "Risk Metrics" in reply
    assert len(sources) > 0
    assert conf == 0.85


def test_handle_query_cot():
    context_engine.record_agent_cot(
        trace={"step": "evaluate_risk", "confidence": 0.92, "action": "allow"},
        session_id="cot-session"
    )
    reply, sources, conf = _handle_query_cot("cot traces", "cot-session")
    assert "Agent Reasoning Traces" in reply
    assert len(sources) > 0
    assert conf == 0.90


def test_handle_explain_decision():
    context_engine.record_agent_cot(
        trace={"step": "flag_anomaly", "confidence": 0.88, "decision": "hold_tx"},
        session_id="explain-session"
    )
    reply, sources, conf = _handle_explain_decision("why did agent decide?", "explain-session")
    assert "Agent Reasoning Trace" in reply or "Flagged Transaction" in reply
    assert conf >= 0.70


# ===========================================================================
# 3. explain_event & process() End-to-End
# ===========================================================================

def test_explain_event_not_found():
    res = nl_engine.explain_event(999999)
    assert res["found"] is False
    assert "not found" in res["explanation"].lower()


def test_nl_engine_process_end_to_end():
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}, clear=False):
        resp = nl_engine.process(message="What is my financial health score?", session_id="test-e2e")
        assert resp.reply is not None
        assert resp.intent == "query_health"
        assert resp.confidence > 0.5
        assert resp.session_id == "test-e2e"

        # Verify CoT trace was recorded in context engine
        cots = context_engine.query(ContextLane.AGENT_COT, limit=5, session_id="test-e2e")
        assert len(cots) > 0
        assert any(c["payload"].get("step") == "nl_query_dispatch" for c in cots)
