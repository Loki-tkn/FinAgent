"""
schemas.py – Copilot Pydantic v2 Schemas
=========================================
Request / response models for all ``/api/copilot`` endpoints.

Kept separate from ``app/schemas.py`` so no existing imports are affected.
All models use strict Pydantic v2 conventions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


# ===========================================================================
# 1. Chat — natural language query / response
# ===========================================================================

class CopilotChatRequest(BaseModel):
    """Payload for POST /api/copilot/chat."""
    message:    str  = Field(..., min_length=1, max_length=2000,
                             description="Natural-language query or command for the copilot.")
    session_id: str  = Field(default="default",
                             description="Session identifier for multi-turn context isolation.")

    model_config = {"json_schema_extra": {
        "examples": [
            {"message": "Why did the agent flag my dining spend?", "session_id": "user-1"},
            {"message": "What is my current financial health score?", "session_id": "user-1"},
            {"message": "Show me the last 5 agent reasoning traces.", "session_id": "user-1"},
        ]
    }}


class SourceReference(BaseModel):
    """A single evidence item cited in the copilot reply."""
    event_id:  Optional[int]  = None
    lane:      str
    timestamp: str
    summary:   str


class CopilotChatResponse(BaseModel):
    """Response payload for POST /api/copilot/chat."""
    reply:      str                    = Field(..., description="Human-readable answer.")
    intent:     str                    = Field(..., description="Detected intent category.")
    confidence: float                  = Field(..., ge=0.0, le=1.0,
                                               description="Rule-engine confidence (0–1).")
    sources:    List[SourceReference]  = Field(default_factory=list,
                                               description="Context events used to generate the reply.")
    session_id: str
    timestamp:  str
    disclaimer: Optional[str]          = Field(default=None, description="Regulatory financial disclaimer.")


# ===========================================================================
# 2. Risk Override
# ===========================================================================

class RiskOverrideRequest(BaseModel):
    """Payload for POST /api/copilot/risk-override."""
    overrides:   Dict[str, Any] = Field(
        ...,
        description="Key-value pairs of risk parameters to override.",
    )
    session_id:  str = Field(default="default")

    model_config = {"json_schema_extra": {
        "examples": [
            {"overrides": {"max_drawdown_pct": 5.0, "stop_loss_usd": 500}, "session_id": "user-1"}
        ]
    }}


class RiskOverrideResponse(BaseModel):
    """Response for POST /api/copilot/risk-override."""
    message:    str
    event_id:   Optional[int]
    overrides:  Dict[str, Any]
    session_id: str
    timestamp:  str
    warnings:   List[str]              = Field(default_factory=list, description="Guardrail warnings or clamping notices.")
    disclaimer: Optional[str]          = Field(default=None, description="Regulatory financial disclaimer.")


# ===========================================================================
# 3. Strategy Parameters
# ===========================================================================

class StrategyParamRequest(BaseModel):
    """Payload for POST /api/copilot/strategy-param."""
    params:     Dict[str, Any] = Field(
        ...,
        description="Strategy configuration key-value pairs.",
    )
    session_id: str = Field(default="default")

    model_config = {"json_schema_extra": {
        "examples": [
            {"params": {"rebalance_frequency": "monthly", "target_savings_rate": 0.30}, "session_id": "user-1"}
        ]
    }}


class StrategyParamResponse(BaseModel):
    message:    str
    event_id:   Optional[int]
    params:     Dict[str, Any]
    session_id: str
    timestamp:  str


# ===========================================================================
# 4. Watchlist Update
# ===========================================================================

class WatchlistUpdateRequest(BaseModel):
    """Payload for POST /api/copilot/watchlist."""
    items:      List[str] = Field(..., min_length=1,
                                  description="Category or asset names to add/remove.")
    action:     Literal["add", "remove"] = Field(default="add")
    session_id: str = Field(default="default")


class WatchlistUpdateResponse(BaseModel):
    message:    str
    event_id:   Optional[int]
    items:      List[str]
    action:     str
    session_id: str
    timestamp:  str


# ===========================================================================
# 5. Kill Switch
# ===========================================================================

class KillSwitchRequest(BaseModel):
    """Payload for POST /api/copilot/kill-switch."""
    reason:     str = Field(..., min_length=1, max_length=500,
                            description="Human-readable reason for the emergency halt.")
    session_id: str = Field(default="default")

    model_config = {"json_schema_extra": {
        "examples": [
            {"reason": "Unexpected drawdown exceeding 10% — manual halt.", "session_id": "user-1"}
        ]
    }}


class KillSwitchResponse(BaseModel):
    """Response for POST /api/copilot/kill-switch."""
    status:     Literal["HALTED"]
    message:    str
    event_id:   Optional[int]
    reason:     str
    session_id: str
    timestamp:  str


# ===========================================================================
# 6. Lane / Context Queries
# ===========================================================================

class LaneQueryResponse(BaseModel):
    """Response for GET /api/copilot/context/{lane}."""
    lane:   str
    events: List[Dict[str, Any]] = Field(default_factory=list)
    total:  int


class SnapshotResponse(BaseModel):
    """Response for GET /api/copilot/context/snapshot."""
    kill_switch_active: bool
    event_counts:       Dict[str, int]
    lanes:              Dict[str, List[Dict[str, Any]]]


# ===========================================================================
# 7. Explainability
# ===========================================================================

class ExplainResponse(BaseModel):
    """Response for GET /api/copilot/explain/{event_id}."""
    event_id:     int
    found:        bool
    explanation:  str
    event:        Optional[Dict[str, Any]] = None
    related:      List[Dict[str, Any]] = Field(default_factory=list)
    timestamp:    str


# ===========================================================================
# 8. Session Management
# ===========================================================================

class SessionClearResponse(BaseModel):
    """Response for DELETE /api/copilot/session/{session_id}."""
    session_id:    str
    events_evicted: int
    message:       str
    timestamp:     str
