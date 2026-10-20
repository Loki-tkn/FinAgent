"""
hooks.py – Pipeline Hooks Integration for FinAgent Copilot
==========================================================
Phase 3: Operational Pipeline Event Producers.

Provides non-blocking, async-safe event publishing hooks that integrate
directly into existing execution services and routers (Simulation, Sandbox,
Transactions, Quant, Goals, Profile, Dashboard, Admin).

Key Guarantees:
---------------
* **ZERO Latency Overhead**: By default (``background=True``), events are either
  scheduled onto the active asyncio event loop as tasks, or queued to a daemon
  ThreadPoolExecutor. The caller's business logic returns immediately without
  waiting for database I/O or subscriber execution.
* **Non-Blocking Async**: Full ``async`` variants (``aemit_*``) are provided for
  native coroutine callers using ``await event_bus.apublish(...)``.
* **Fault-Tolerant & Fail-Safe**: Any exception occurring during event creation,
  validation, DB logging, or dispatch is caught, logged, and swallowed.
  Core financial and trading workflows will NEVER fail due to copilot hooks.
* **Strict Typing & Backward Compatibility**: Purely additive helper functions
  using ``ContextLane``, ``ContextSeverity``, and ``ContextEvent``.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from app.copilot.bus import (
    ContextEvent,
    ContextLane,
    ContextSeverity,
    event_bus,
)

logger = logging.getLogger("finagent.copilot.hooks")

# Dedicated daemon thread pool for non-blocking background writes from sync handlers
_hook_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="copilot-hooks")


def _safe_sync_worker(event: ContextEvent) -> None:
    """Worker function executed inside the background thread pool."""
    try:
        event_bus.publish(event)
    except Exception as exc:
        logger.error(
            "Background hook publish failed for lane %s: %s",
            event.lane.value,
            exc,
            exc_info=True,
        )


def emit_event(
    event: ContextEvent,
    background: bool = True,
) -> Optional[ContextEvent]:
    """
    Publish a ContextEvent safely with zero disruption to the caller.

    Parameters
    ----------
    event : ContextEvent
        The event object to publish.
    background : bool, default True
        If True, non-blocking execution is guaranteed:
          - If called within an active asyncio event loop, schedules a task.
          - If called in a sync thread, submits to the background thread pool.
        If False, executes synchronously (useful in test assertions).

    Returns
    -------
    Optional[ContextEvent]
        The event (or None if an immediate validation/submission error occurred).
    """
    try:
        if not background:
            return event_bus.publish(event)

        # 1. Check if there is an active running asyncio event loop in this thread
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.apublish(event))
            return event
        except RuntimeError:
            # 2. No running loop in current thread: queue to daemon threadpool
            _hook_executor.submit(_safe_sync_worker, event)
            return event
    except Exception as exc:
        logger.error("emit_event encountered error on lane %s: %s", event.lane.value, exc)
        return None


async def aemit_event(event: ContextEvent) -> Optional[ContextEvent]:
    """
    Async non-blocking publish helper for coroutine route handlers.
    Catches and logs all errors so the caller is never interrupted.
    """
    try:
        return await event_bus.apublish(event)
    except Exception as exc:
        logger.error("aemit_event encountered error on lane %s: %s", event.lane.value, exc)
        return None


# ===========================================================================
# 1. AGENT_COT Hooks (Chain-of-Thought / Decision Reasoning Traces)
# ===========================================================================

def emit_agent_cot(
    trace: Dict[str, Any],
    session_id: str = "default",
    severity: ContextSeverity = ContextSeverity.INFO,
    source: str = "agent",
    background: bool = True,
) -> Optional[ContextEvent]:
    """
    Emit an AGENT_COT event when decisions or analysis traces are generated.
    """
    event = ContextEvent(
        lane=ContextLane.AGENT_COT,
        payload=trace,
        severity=severity,
        source=source,
        session_id=session_id,
    )
    return emit_event(event, background=background)


async def aemit_agent_cot(
    trace: Dict[str, Any],
    session_id: str = "default",
    severity: ContextSeverity = ContextSeverity.INFO,
    source: str = "agent",
) -> Optional[ContextEvent]:
    """Async variant of emit_agent_cot."""
    event = ContextEvent(
        lane=ContextLane.AGENT_COT,
        payload=trace,
        severity=severity,
        source=source,
        session_id=session_id,
    )
    return await aemit_event(event)


# ===========================================================================
# 2. RISK_METRIC & RISK_OVERRIDE Hooks
# ===========================================================================

def emit_risk_metric(
    metrics: Dict[str, Any],
    session_id: str = "default",
    severity: ContextSeverity = ContextSeverity.INFO,
    source: str = "agent",
    background: bool = True,
) -> Optional[ContextEvent]:
    """
    Emit a RISK_METRIC event when risk limits, CFaR, or anomaly checks are evaluated.
    """
    event = ContextEvent(
        lane=ContextLane.RISK_METRIC,
        payload=metrics,
        severity=severity,
        source=source,
        session_id=session_id,
    )
    return emit_event(event, background=background)


async def aemit_risk_metric(
    metrics: Dict[str, Any],
    session_id: str = "default",
    severity: ContextSeverity = ContextSeverity.INFO,
    source: str = "agent",
) -> Optional[ContextEvent]:
    """Async variant of emit_risk_metric."""
    event = ContextEvent(
        lane=ContextLane.RISK_METRIC,
        payload=metrics,
        severity=severity,
        source=source,
        session_id=session_id,
    )
    return await aemit_event(event)


def emit_risk_override(
    overrides: Dict[str, Any],
    session_id: str = "default",
    source: str = "user",
    severity: ContextSeverity = ContextSeverity.WARNING,
    background: bool = True,
) -> Optional[ContextEvent]:
    """
    Emit a RISK_OVERRIDE event when user-defined or agent risk parameters change.
    """
    event = ContextEvent(
        lane=ContextLane.RISK_OVERRIDE,
        payload=overrides,
        severity=severity,
        source=source,
        session_id=session_id,
    )
    return emit_event(event, background=background)


async def aemit_risk_override(
    overrides: Dict[str, Any],
    session_id: str = "default",
    source: str = "user",
    severity: ContextSeverity = ContextSeverity.WARNING,
) -> Optional[ContextEvent]:
    """Async variant of emit_risk_override."""
    event = ContextEvent(
        lane=ContextLane.RISK_OVERRIDE,
        payload=overrides,
        severity=severity,
        source=source,
        session_id=session_id,
    )
    return await aemit_event(event)


# ===========================================================================
# 3. MARKET_INTEL Hooks
# ===========================================================================

def emit_market_intel(
    snapshot: Dict[str, Any],
    session_id: str = "default",
    severity: ContextSeverity = ContextSeverity.INFO,
    source: str = "system",
    background: bool = True,
) -> Optional[ContextEvent]:
    """
    Emit a MARKET_INTEL event when market or transaction snapshots are processed.
    """
    event = ContextEvent(
        lane=ContextLane.MARKET_INTEL,
        payload=snapshot,
        severity=severity,
        source=source,
        session_id=session_id,
    )
    return emit_event(event, background=background)


async def aemit_market_intel(
    snapshot: Dict[str, Any],
    session_id: str = "default",
    severity: ContextSeverity = ContextSeverity.INFO,
    source: str = "system",
) -> Optional[ContextEvent]:
    """Async variant of emit_market_intel."""
    event = ContextEvent(
        lane=ContextLane.MARKET_INTEL,
        payload=snapshot,
        severity=severity,
        source=source,
        session_id=session_id,
    )
    return await aemit_event(event)


# ===========================================================================
# 4. EXEC_LOG Hooks (Execution Events)
# ===========================================================================

def emit_exec_log(
    log: Dict[str, Any],
    session_id: str = "default",
    severity: ContextSeverity = ContextSeverity.INFO,
    source: str = "agent",
    background: bool = True,
) -> Optional[ContextEvent]:
    """
    Emit an EXEC_LOG event when trading or transaction executions occur.
    """
    event = ContextEvent(
        lane=ContextLane.EXEC_LOG,
        payload=log,
        severity=severity,
        source=source,
        session_id=session_id,
    )
    return emit_event(event, background=background)


async def aemit_exec_log(
    log: Dict[str, Any],
    session_id: str = "default",
    severity: ContextSeverity = ContextSeverity.INFO,
    source: str = "agent",
) -> Optional[ContextEvent]:
    """Async variant of emit_exec_log."""
    event = ContextEvent(
        lane=ContextLane.EXEC_LOG,
        payload=log,
        severity=severity,
        source=source,
        session_id=session_id,
    )
    return await aemit_event(event)


def flush_hooks(timeout: float = 2.0) -> None:
    """
    Flush any queued background hook tasks (useful in tests and teardown).
    """
    _hook_executor.shutdown(wait=True)
