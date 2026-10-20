"""
copilot.py – Copilot API Router
================================
Phase 2: All 8 ``/api/copilot`` endpoints.

All DB-bound / CPU-bound work is offloaded to a threadpool via
``asyncio.get_event_loop().run_in_executor(None, fn)`` so the FastAPI
async event loop is never blocked.

Endpoints
---------
POST   /api/copilot/chat                  – NL chat query
GET    /api/copilot/context/snapshot      – Full multi-lane state snapshot
GET    /api/copilot/context/{lane}        – Query a specific lane
POST   /api/copilot/risk-override         – Apply a risk parameter override
POST   /api/copilot/strategy-param        – Push a strategy parameter change
POST   /api/copilot/watchlist             – Add/remove watchlist items
POST   /api/copilot/kill-switch           – Emergency halt
GET    /api/copilot/explain/{event_id}    – Explain a specific copilot event
DELETE /api/copilot/session/{session_id}  – Clear in-memory session context

Backward compatibility: no existing file is modified here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from functools import partial
from typing import Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.security import get_current_role, require_role
from app.services.audit import record_audit_log
from app.copilot.guardrails import (
    attach_financial_disclaimer,
    validate_risk_override,
    sanitize_ai_reasoning,
    STANDARD_FINANCIAL_DISCLAIMER,
)

logger = logging.getLogger("finagent.copilot.router")

from app.copilot.state import ContextLane, ContextSeverity, context_engine
from app.copilot.bus import event_bus, ContextEvent
from app.copilot.nl_engine import nl_engine
from app.copilot.schemas import (
    CopilotChatRequest,
    CopilotChatResponse,
    ExplainResponse,
    KillSwitchRequest,
    KillSwitchResponse,
    LaneQueryResponse,
    RiskOverrideRequest,
    RiskOverrideResponse,
    SessionClearResponse,
    SnapshotResponse,
    StrategyParamRequest,
    StrategyParamResponse,
    WatchlistUpdateRequest,
    WatchlistUpdateResponse,
)

router = APIRouter(prefix="/api/copilot", tags=["Copilot"])

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run_sync(fn, *args, **kwargs):
    """Run a blocking function in the default threadpool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(fn, *args, **kwargs))


def _validate_lane(lane_str: str) -> ContextLane:
    """Validate and convert a lane string → ContextLane, raising 422 on failure."""
    try:
        return ContextLane(lane_str)
    except ValueError:
        valid = [l.value for l in ContextLane]
        raise HTTPException(
            status_code=422,
            detail=f"Invalid lane '{lane_str}'. Valid lanes: {valid}"
        )


# ===========================================================================
# 1. POST /api/copilot/chat
# ===========================================================================

@router.post(
    "/chat",
    response_model=CopilotChatResponse,
    summary="Natural-language query to the Copilot",
    description=(
        "Send a free-text message. The Copilot classifies the intent, retrieves "
        "relevant financial data and agent context, and returns a structured reply."
    ),
)
async def copilot_chat(
    req: CopilotChatRequest,
    role: str = Depends(get_current_role),
) -> CopilotChatResponse:
    """
    NL chat endpoint — the primary human-in-the-loop interface.

    The NL engine runs synchronously (SQLite + in-memory reads); we
    offload it to a threadpool to avoid blocking the event loop.
    """
    response: CopilotChatResponse = await _run_sync(
        nl_engine.process, req.message, req.session_id
    )

    # Phase 5: Enforce financial disclaimer and AI reasoning safety filters
    response.reply = sanitize_ai_reasoning(attach_financial_disclaimer(response.reply))
    response.disclaimer = STANDARD_FINANCIAL_DISCLAIMER

    # Async publish so all bus subscribers (kill-switch listeners, etc.) are notified
    await event_bus.apublish(ContextEvent(
        lane=ContextLane.EXEC_LOG,
        payload={
            "endpoint":   "chat",
            "intent":     response.intent,
            "confidence": response.confidence,
            "role":       role,
        },
        source="system",
        session_id=req.session_id,
    ))

    await _run_sync(
        record_audit_log,
        role,
        "COPILOT_CHAT",
        "COPILOT",
        "SUCCESS",
        f"Intent '{response.intent}' | confidence={response.confidence:.2f} | session={req.session_id}",
    )

    return response


# ===========================================================================
# 2. GET /api/copilot/context/snapshot
# ===========================================================================

@router.get(
    "/context/snapshot",
    response_model=SnapshotResponse,
    summary="Full multi-lane context state snapshot",
    description="Returns the last 10 events per lane and kill-switch status.",
)
async def get_context_snapshot(
    role: str = Depends(get_current_role),
) -> SnapshotResponse:
    snap = await _run_sync(context_engine.snapshot)
    return SnapshotResponse(
        kill_switch_active=snap["kill_switch_active"],
        event_counts=snap["event_counts"],
        lanes=snap["lanes"],
    )


# ===========================================================================
# 3. GET /api/copilot/context/{lane}
# ===========================================================================

@router.get(
    "/context/{lane}",
    response_model=LaneQueryResponse,
    summary="Query a specific context lane",
    description=(
        "Retrieve recent events from one of the 10 context lanes. "
        "Valid lanes: user_prompt, risk_override, strategy_param, watchlist, "
        "market_intel, agent_cot, risk_metric, exec_log, kill_switch, system_alert."
    ),
)
async def query_lane(
    lane: str,
    limit: int = Query(default=50, ge=1, le=500),
    session_id: Optional[str] = Query(default=None),
    from_db: bool = Query(
        default=False,
        description="If true, query the persistent DB instead of the in-memory buffer."
    ),
    role: str = Depends(get_current_role),
) -> LaneQueryResponse:
    ctx_lane = _validate_lane(lane)

    if from_db:
        events = await _run_sync(
            context_engine.query_db, ctx_lane, limit, session_id
        )
    else:
        events = await _run_sync(
            context_engine.query, ctx_lane, limit, session_id
        )

    return LaneQueryResponse(lane=lane, events=events, total=len(events))


# ===========================================================================
# 4. POST /api/copilot/risk-override
# ===========================================================================

@router.post(
    "/risk-override",
    response_model=RiskOverrideResponse,
    summary="Apply a risk parameter override",
    description=(
        "Push user-defined risk limit changes (e.g. max_drawdown, stop_loss_usd) "
        "into the RISK_OVERRIDE context lane and broadcast via the event bus."
    ),
)
async def apply_risk_override(
    req: RiskOverrideRequest,
    role: str = Depends(require_role("User")),
) -> RiskOverrideResponse:
    # Phase 5: Enforce financial safety guardrails on proposed risk limits
    guard_res = validate_risk_override(req.overrides, clamp_out_of_bounds=True)
    if guard_res.has_critical_violations:
        reasons = [v.message for v in guard_res.violations if v.severity == ContextSeverity.CRITICAL]
        raise HTTPException(
            status_code=422,
            detail=f"Risk override rejected by safety guardrails: {'; '.join(reasons)}"
        )

    final_overrides = guard_res.sanitized_payload

    evt = await event_bus.apublish(ContextEvent(
        lane=ContextLane.RISK_OVERRIDE,
        payload=final_overrides,
        severity=ContextSeverity.WARNING if guard_res.warnings else ContextSeverity.INFO,
        source="user",
        session_id=req.session_id,
    ))

    await _run_sync(
        record_audit_log,
        role,
        "COPILOT_RISK_OVERRIDE",
        "COPILOT",
        "SUCCESS",
        f"Risk override applied: {final_overrides} | session={req.session_id} | warnings={guard_res.warnings}",
    )

    return RiskOverrideResponse(
        message=(
            "Risk override approved by safety guardrails and broadcast."
            if not guard_res.warnings
            else f"Risk override clamped to safe bounds: {'; '.join(guard_res.warnings)}"
        ),
        event_id=evt.event_id,
        overrides=final_overrides,
        session_id=req.session_id,
        timestamp=_now(),
        warnings=guard_res.warnings,
        disclaimer=STANDARD_FINANCIAL_DISCLAIMER,
    )


# ===========================================================================
# 5. POST /api/copilot/strategy-param
# ===========================================================================

@router.post(
    "/strategy-param",
    response_model=StrategyParamResponse,
    summary="Push a strategy parameter change",
    description="Update agent strategy configuration (e.g. rebalance_frequency, savings_rate_target).",
)
async def push_strategy_param(
    req: StrategyParamRequest,
    role: str = Depends(require_role("User")),
) -> StrategyParamResponse:
    evt = await event_bus.apublish(ContextEvent(
        lane=ContextLane.STRATEGY_PARAM,
        payload=req.params,
        severity=ContextSeverity.INFO,
        source="user",
        session_id=req.session_id,
    ))

    await _run_sync(
        record_audit_log,
        role,
        "COPILOT_STRATEGY_PARAM",
        "COPILOT",
        "SUCCESS",
        f"Strategy params updated: {req.params} | session={req.session_id}",
    )

    return StrategyParamResponse(
        message="Strategy parameters recorded and broadcast.",
        event_id=evt.event_id,
        params=req.params,
        session_id=req.session_id,
        timestamp=_now(),
    )


# ===========================================================================
# 6. POST /api/copilot/watchlist
# ===========================================================================

@router.post(
    "/watchlist",
    response_model=WatchlistUpdateResponse,
    summary="Update the custom watchlist",
    description="Add or remove categories / assets from the user watchlist.",
)
async def update_watchlist(
    req: WatchlistUpdateRequest,
    role: str = Depends(require_role("User")),
) -> WatchlistUpdateResponse:
    evt = await event_bus.apublish(ContextEvent(
        lane=ContextLane.WATCHLIST,
        payload={"items": req.items, "action": req.action},
        severity=ContextSeverity.INFO,
        source="user",
        session_id=req.session_id,
    ))

    await _run_sync(
        record_audit_log,
        role,
        "COPILOT_WATCHLIST_UPDATE",
        "COPILOT",
        "SUCCESS",
        f"Watchlist {req.action}: {req.items} | session={req.session_id}",
    )

    return WatchlistUpdateResponse(
        message=f"Watchlist updated — {req.action}: {req.items}",
        event_id=evt.event_id,
        items=req.items,
        action=req.action,
        session_id=req.session_id,
        timestamp=_now(),
    )


# ===========================================================================
# 7. POST /api/copilot/kill-switch  🚨
# ===========================================================================

@router.post(
    "/kill-switch",
    response_model=KillSwitchResponse,
    summary="🚨 Emergency Kill Switch — halt all agent actions",
    description=(
        "Activates the emergency kill switch. Broadcasts a CRITICAL event on the "
        "KILL_SWITCH lane, which all bus subscribers must handle immediately to "
        "halt open agent tasks and sandbox proposals. Requires User role."
    ),
)
async def activate_kill_switch(
    req: KillSwitchRequest,
    role: str = Depends(require_role("User")),
) -> KillSwitchResponse:
    # Publish through the bus — the built-in kill-switch handler fires immediately
    evt = await event_bus.apublish(ContextEvent(
        lane=ContextLane.KILL_SWITCH,
        payload={"reason": req.reason, "triggered_by": role},
        severity=ContextSeverity.CRITICAL,
        source=role,
        session_id=req.session_id,
    ))

    await _run_sync(
        record_audit_log,
        role,
        "COPILOT_KILL_SWITCH",
        "COPILOT",
        "CRITICAL",
        f"EMERGENCY HALT — reason: {req.reason} | session={req.session_id}",
    )

    return KillSwitchResponse(
        status="HALTED",
        message=(
            "⚠️ Emergency kill switch activated. "
            "All registered agent subscribers have been notified to halt immediately."
        ),
        event_id=evt.event_id,
        reason=req.reason,
        session_id=req.session_id,
        timestamp=_now(),
    )


# ===========================================================================
# 8. GET /api/copilot/explain/{event_id}
# ===========================================================================

@router.get(
    "/explain/{event_id}",
    response_model=ExplainResponse,
    summary="Explain a specific copilot event",
    description=(
        "Deep-dive explanation for any copilot_events row. "
        "Returns the full payload, lane context, and temporally related events."
    ),
)
async def explain_event(
    event_id: int,
    session_id: str = Query(default="default"),
    role: str = Depends(get_current_role),
) -> ExplainResponse:
    result = await _run_sync(nl_engine.explain_event, event_id, session_id)

    if not result["found"]:
        raise HTTPException(
            status_code=404,
            detail=f"Copilot event #{event_id} not found."
        )

    return ExplainResponse(**result)


# ===========================================================================
# 9. DELETE /api/copilot/session/{session_id}
# ===========================================================================

@router.delete(
    "/session/{session_id}",
    response_model=SessionClearResponse,
    summary="Clear in-memory context for a session",
    description=(
        "Evicts all in-memory events for the given session from every lane. "
        "Persisted DB records are preserved (audit trail remains intact). "
        "Admin role required."
    ),
)
async def clear_session(
    session_id: str,
    role: str = Depends(require_role("Admin")),
) -> SessionClearResponse:
    evicted = await _run_sync(context_engine.clear_session, session_id)

    await _run_sync(
        record_audit_log,
        role,
        "COPILOT_SESSION_CLEAR",
        f"SESSION#{session_id}",
        "SUCCESS",
        f"Cleared {evicted} in-memory events for session '{session_id}'",
    )

    return SessionClearResponse(
        session_id=session_id,
        events_evicted=evicted,
        message=f"Session '{session_id}' cleared — {evicted} in-memory event(s) evicted. DB records preserved.",
        timestamp=_now(),
    )


# ===========================================================================
# 10. WebSocket /api/copilot/ws — Real-Time Bidirectional Event Streaming
# ===========================================================================

@router.websocket("/ws")
async def copilot_websocket(
    websocket: WebSocket,
    session_id: str = Query(default="default"),
    lanes: Optional[str] = Query(default=None),
) -> None:
    """
    Real-Time Bidirectional WebSocket Streaming for FinAgent Copilot.

    Features:
      • Pushes live events (AGENT_COT, RISK_METRIC, EXEC_LOG, MARKET_INTEL, etc.)
        as they occur on the CopilotEventBus.
      • Supports optional lane filtering via query parameter:
        ``/api/copilot/ws?lanes=agent_cot,risk_metric``.
      • Bidirectional actions from client:
          - {"action": "ping"} → replies {"type": "pong"}
          - {"action": "chat", "message": "..."} → replies {"type": "chat_reply", "data": ...}
          - {"action": "kill_switch", "reason": "..."} → activates emergency halt
          - {"action": "subscribe_lane", "lane": "exec_log"} → dynamically expands lane filter
      • Guaranteed clean disconnect with automatic unsubscription from CopilotEventBus.
    """
    await websocket.accept()

    # Determine initial lane subscription filter
    selected_lanes: Optional[Set[str]] = None
    if lanes:
        selected_lanes = {l.strip() for l in lanes.split(",") if l.strip()}

    # Event queue for decoupling bus dispatches from socket network writes
    queue: asyncio.Queue[ContextEvent] = asyncio.Queue(maxsize=300)

    def on_bus_event(evt: ContextEvent) -> None:
        # Filter by lane if specific lanes are requested
        if selected_lanes is not None and evt.lane.value not in selected_lanes and evt.lane not in selected_lanes:
            return
        try:
            queue.put_nowait(evt)
        except asyncio.QueueFull:
            pass

    # Subscribe to the event bus
    subscription = event_bus.subscribe(on_bus_event)

    # Initial connection handshake
    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "subscribed_lanes": list(selected_lanes) if selected_lanes else "all",
        "timestamp": _now(),
    })

    async def event_forwarder() -> None:
        """Forward queued bus events to the WebSocket client."""
        while True:
            evt = await queue.get()
            try:
                await websocket.send_json({
                    "type": "event",
                    "lane": evt.lane.value,
                    "severity": evt.severity.value,
                    "payload": evt.payload,
                    "source": evt.source,
                    "timestamp": evt.timestamp,
                    "event_id": evt.event_id,
                    "session_id": evt.session_id,
                })
            except Exception:
                break

    forwarder_task = asyncio.create_task(event_forwarder())

    try:
        while True:
            raw_msg = await websocket.receive_text()
            try:
                data = json.loads(raw_msg)
            except Exception:
                await websocket.send_json({"type": "error", "message": "Invalid JSON format."})
                continue

            action = str(data.get("action", "")).lower()

            if action == "ping":
                await websocket.send_json({"type": "pong", "timestamp": _now()})

            elif action == "chat":
                msg_text = str(data.get("message", "")).strip()
                if not msg_text:
                    await websocket.send_json({"type": "error", "message": "Empty message."})
                    continue

                chat_res: CopilotChatResponse = await _run_sync(nl_engine.process, msg_text, session_id)
                chat_res.reply = sanitize_ai_reasoning(attach_financial_disclaimer(chat_res.reply))
                chat_res.disclaimer = STANDARD_FINANCIAL_DISCLAIMER

                await websocket.send_json({
                    "type": "chat_reply",
                    "data": chat_res.model_dump(),
                    "timestamp": _now(),
                })

            elif action == "kill_switch":
                reason = str(data.get("reason", "Emergency halt triggered via WebSocket"))
                evt = event_bus.publish_kill_switch(
                    reason=reason,
                    triggered_by="websocket",
                    session_id=session_id,
                )
                await websocket.send_json({
                    "type": "kill_switch_activated",
                    "event_id": evt.event_id,
                    "reason": reason,
                    "timestamp": _now(),
                })

            elif action == "subscribe_lane":
                new_lane = data.get("lane")
                if new_lane:
                    if selected_lanes is None:
                        selected_lanes = set()
                    selected_lanes.add(str(new_lane).strip())
                await websocket.send_json({
                    "type": "subscription_updated",
                    "subscribed_lanes": list(selected_lanes) if selected_lanes else "all",
                })

            else:
                await websocket.send_json({
                    "type": "unknown_action",
                    "action": action,
                    "supported": ["ping", "chat", "kill_switch", "subscribe_lane"],
                })

    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        logger.debug("WebSocket terminated with exception: %s", exc)
    finally:
        forwarder_task.cancel()
        event_bus.unsubscribe(subscription)
        logger.debug("WebSocket client disconnected; event bus subscriber cleaned up.")

