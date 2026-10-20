"""
FinAgent Copilot Module
=======================
Human-in-the-Loop (HITL) Chatbot Copilot for the FinAgent system.

Package layout:
  state.py     – CopilotContextEngine: unified in-memory + persisted state tracker
  bus.py       – CopilotEventBus: async event pub/sub backbone
  schemas.py    – Pydantic v2 schemas for all Copilot endpoints
  nl_engine.py  – CopilotNLEngine: Rule-based query & CoT explainability
  hooks.py      – Pipeline Hooks: non-blocking operational event publishers
  guardrails.py – Compliance disclaimers, risk bounds & safety checks

Phase 1: State & Event Infrastructure
Phase 2: Router, Schemas & NL Engine
Phase 3: Pipeline Hooks Integration
Phase 4: WebSocket Real-Time Event Streaming (/api/copilot/ws)
Phase 5: Financial Guardrails & Safety Checks
"""

from app.copilot.state import ContextEvent, ContextLane, ContextSeverity, context_engine
from app.copilot.bus import event_bus
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
from app.copilot.guardrails import (
    STANDARD_FINANCIAL_DISCLAIMER,
    MARKDOWN_FINANCIAL_DISCLAIMER,
    attach_financial_disclaimer,
    validate_risk_override,
    sanitize_ai_reasoning,
    GuardrailResult,
    GuardrailViolation,
)

__all__ = [
    "ContextEvent",
    "ContextLane",
    "ContextSeverity",
    "context_engine",
    "event_bus",
    "emit_agent_cot",
    "emit_risk_metric",
    "emit_risk_override",
    "emit_market_intel",
    "emit_exec_log",
    "aemit_agent_cot",
    "aemit_risk_metric",
    "aemit_risk_override",
    "aemit_market_intel",
    "aemit_exec_log",
    "STANDARD_FINANCIAL_DISCLAIMER",
    "MARKDOWN_FINANCIAL_DISCLAIMER",
    "attach_financial_disclaimer",
    "validate_risk_override",
    "sanitize_ai_reasoning",
    "GuardrailResult",
    "GuardrailViolation",
]

