"""
test_copilot_openrouter.py – OpenRouter LLM Integration & Fallback Verification
=================================================================================
Tests:
1. Environment detection & configuration defaults.
2. Live context snapshot generation for LLM prompt.
3. Successful OpenRouter inference execution with CoT trace recording.
4. Seamless fallback to deterministic rule-based engine when API fails or times out.
5. Compliance disclaimer and financial guardrail enforcement on LLM outputs.
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.copilot.state import ContextLane, context_engine
from app.copilot.nl_engine import (
    nl_engine,
    is_openrouter_configured,
    get_openrouter_model,
    _build_llm_context,
)
from app.copilot.guardrails import STANDARD_FINANCIAL_DISCLAIMER

client = TestClient(app)
AUTH_HEADERS_USER = {"X-Role": "User"}


def test_openrouter_unconfigured_fallback():
    """Verify system defaults to rule-based engine when OPENROUTER_API_KEY is unset."""
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "", "OPENROUTER_MODEL": ""}, clear=False):
        assert is_openrouter_configured() is False
        assert get_openrouter_model() == "anthropic/claude-3.5-sonnet"

        resp = client.post(
            "/api/copilot/chat",
            json={"message": "What is my financial health score?"},
            headers=AUTH_HEADERS_USER
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] != "openrouter_llm"
        assert "Disclaimer:" in data["reply"]


def test_llm_context_snapshot_builder():
    """Verify _build_llm_context compiles user profile, budget stats, and context lanes."""
    # Seed a CoT trace in context engine
    context_engine.record_agent_cot(
        trace={"step": "context_builder_test", "detail": "testing multi-lane context injection"},
        session_id="test-session"
    )

    context_str, sources = _build_llm_context(session_id="test-session")
    assert "User Profile" in context_str
    assert "Financial Health Score:" in context_str
    assert "Profile read error" not in context_str
    assert "Spending Baseline" in context_str
    assert isinstance(sources, list)


def test_openrouter_successful_inference():
    """Verify successful OpenRouter LLM inference, CoT trace recording, and response contract."""
    mock_choice = MagicMock()
    mock_choice.message.content = "Your financial health score is currently 78/100, which is strong."
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    mock_openai_instance = MagicMock()
    mock_openai_instance.chat.completions.create.return_value = mock_completion

    env_overrides = {
        "OPENROUTER_API_KEY": "sk-or-v1-mock-test-key",
        "OPENROUTER_MODEL": "google/gemini-2.0-flash-001"
    }

    with patch.dict("os.environ", env_overrides, clear=False), \
         patch("openai.OpenAI", return_value=mock_openai_instance):

        assert is_openrouter_configured() is True
        assert get_openrouter_model() == "google/gemini-2.0-flash-001"

        resp = client.post(
            "/api/copilot/chat",
            json={"message": "Can I afford a $1000 vacation?"},
            headers=AUTH_HEADERS_USER
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "openrouter_llm"
        assert data["confidence"] == 0.95
        assert "78/100" in data["reply"]
        assert "Disclaimer:" in data["reply"]
        assert data["disclaimer"] == STANDARD_FINANCIAL_DISCLAIMER

        # Verify OpenRouter was called with the correct model and system prompt
        mock_openai_instance.chat.completions.create.assert_called_once()
        call_kwargs = mock_openai_instance.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "google/gemini-2.0-flash-001"
        assert call_kwargs["messages"][0]["role"] == "system"
        assert "LIVE FINANCIAL & AGENT CONTEXT" in call_kwargs["messages"][0]["content"]

        # Verify CoT event was recorded in context_engine
        cot_events = context_engine.query(ContextLane.AGENT_COT, limit=5)
        llm_cots = [e for e in cot_events if e["payload"].get("step") == "openrouter_llm_inference"]
        assert len(llm_cots) >= 1
        assert llm_cots[0]["payload"]["model"] == "google/gemini-2.0-flash-001"


def test_openrouter_api_failure_graceful_fallback():
    """Verify that if OpenRouter raises an API/network exception, system falls back to rule-based."""
    mock_openai_instance = MagicMock()
    mock_openai_instance.chat.completions.create.side_effect = RuntimeError("OpenRouter 502 Bad Gateway / Timeout")

    env_overrides = {
        "OPENROUTER_API_KEY": "sk-or-v1-failing-key",
        "OPENROUTER_MODEL": "anthropic/claude-3.5-sonnet"
    }

    with patch.dict("os.environ", env_overrides, clear=False), \
         patch("openai.OpenAI", return_value=mock_openai_instance):

        # Should NOT raise 500; should fallback smoothly to rule-based engine
        resp = client.post(
            "/api/copilot/chat",
            json={"message": "What is my financial health score?"},
            headers=AUTH_HEADERS_USER
        )

        assert resp.status_code == 200
        data = resp.json()
        # Fallback executed: intent detected via rule-based classifier (not openrouter_llm)
        assert data["intent"] == "query_health"
        assert data["confidence"] > 0.0
        assert "Disclaimer:" in data["reply"]
