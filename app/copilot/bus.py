"""
bus.py – CopilotEventBus
=========================
Requirement A (continued) + Requirement B (Inspection & Repair):

An in-process, async-capable event pub/sub backbone that:
  • Decouples event *producers* (routers, services) from event *consumers*
    (copilot response handlers, kill-switch listeners, repair engines).
  • Routes every published event through the CopilotContextEngine so the
    full audit trail is automatically maintained.
  • Supports synchronous *and* async subscribers — subscribers can be
    plain callables or ``async def`` coroutines.
  • Provides a built-in priority lane for CRITICAL / KILL_SWITCH events
    that are dispatched first before normal handlers.

Design decisions
----------------
* Pure-Python ``asyncio.Queue`` for async subscribers; direct call for
  sync subscribers.  No external broker dependency (Kafka, Redis, etc.)
  so it works within the existing zero-dependency FastAPI setup.
* The bus is intentionally thin — it does NOT implement retry, dead-letter,
  or persistence (the state engine handles persistence).
* Thread safety for registration (``subscribe`` / ``unsubscribe``) is
  achieved via ``threading.Lock``; dispatch is lock-free (reads only).
* The module-level singleton ``event_bus`` is imported by routers.

No existing file is modified.  Full backward compatibility preserved.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Union,
)

from app.copilot.state import (
    ContextEvent,
    ContextLane,
    ContextSeverity,
    context_engine,
)

logger = logging.getLogger("finagent.copilot.bus")


# ===========================================================================
# 1. Subscriber type alias
# ===========================================================================

#: A subscriber is any callable that accepts a single ``ContextEvent``.
#: It may be sync or async (``async def``).
Subscriber = Union[
    Callable[[ContextEvent], None],
    Callable[[ContextEvent], Awaitable[None]],
]


# ===========================================================================
# 2. Subscription handle (returned to caller for unsubscription)
# ===========================================================================

@dataclass
class Subscription:
    """Opaque handle returned by ``CopilotEventBus.subscribe()``."""
    sub_id:   int
    lane:     Optional[ContextLane]   # None means "all lanes"
    callback: Subscriber
    is_async: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.is_async = inspect.iscoroutinefunction(self.callback)


# ===========================================================================
# 3. CopilotEventBus
# ===========================================================================

class CopilotEventBus:
    """
    Async-capable event pub/sub bus for the FinAgent Copilot.

    Usage — registering a subscriber
    ---------------------------------
    .. code-block:: python

        from app.copilot.bus import event_bus, ContextLane

        # Sync subscriber on a specific lane
        def on_kill_switch(evt: ContextEvent) -> None:
            print(f"HALT requested: {evt.payload}")

        sub = event_bus.subscribe(on_kill_switch, lane=ContextLane.KILL_SWITCH)

        # Async subscriber on all lanes
        async def on_any(evt: ContextEvent) -> None:
            await some_async_handler(evt)

        sub2 = event_bus.subscribe(on_any)           # lane=None → all lanes

        # Unsubscribe later
        event_bus.unsubscribe(sub)

    Usage — publishing an event
    ---------------------------
    .. code-block:: python

        from app.copilot.bus import event_bus
        from app.copilot.state import ContextEvent, ContextLane

        # Synchronous publish (blocks until all sync subscribers finish)
        event_bus.publish(ContextEvent(
            lane=ContextLane.USER_PROMPT,
            payload={"text": "Show me my spending anomalies"},
            source="user",
        ))

        # Async publish (awaits async subscribers, schedules sync ones)
        await event_bus.apublish(ContextEvent(
            lane=ContextLane.AGENT_COT,
            payload={"step": "anomaly_scan", "confidence": 0.91},
            source="agent",
        ))
    """

    def __init__(self) -> None:
        self._lock:        threading.Lock                                = threading.Lock()
        self._next_id:     int                                           = 0
        # lane → list of subscriptions  (None key = wildcard / all-lanes)
        self._subscribers: Dict[Optional[ContextLane], List[Subscription]] = defaultdict(list)
        # Tracks all sub IDs for O(1) unsubscribe lookup
        self._sub_index:   Dict[int, Subscription]                       = {}

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def subscribe(
        self,
        callback: Subscriber,
        lane: Optional[ContextLane] = None,
    ) -> Subscription:
        """
        Register ``callback`` to receive events on ``lane``.

        Parameters
        ----------
        callback : callable
            Function (sync or async) that accepts one ``ContextEvent`` arg.
        lane : ContextLane or None
            If ``None``, the subscriber receives events from ALL lanes.

        Returns
        -------
        Subscription
            Opaque handle — pass to ``unsubscribe()`` to deregister.
        """
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            sub = Subscription(sub_id=sub_id, lane=lane, callback=callback)
            self._subscribers[lane].append(sub)
            self._sub_index[sub_id] = sub
        logger.debug(
            "Subscribed %s (id=%d) to lane=%s",
            getattr(callback, "__name__", repr(callback)),
            sub_id,
            lane.value if lane else "ALL",
        )
        return sub

    def unsubscribe(self, subscription: Subscription) -> bool:
        """
        Remove a previously registered subscription.

        Returns ``True`` if found and removed, ``False`` if already gone.
        """
        with self._lock:
            sub = self._sub_index.pop(subscription.sub_id, None)
            if sub is None:
                return False
            lane_list = self._subscribers.get(sub.lane, [])
            try:
                lane_list.remove(sub)
            except ValueError:
                pass
        logger.debug("Unsubscribed id=%d", subscription.sub_id)
        return True

    def subscriber_count(self, lane: Optional[ContextLane] = None) -> int:
        """Return the number of subscribers on ``lane`` (or all if None)."""
        with self._lock:
            if lane is not None:
                return len(self._subscribers.get(lane, []))
            return sum(len(v) for v in self._subscribers.values())

    # ------------------------------------------------------------------
    # Publish API — synchronous
    # ------------------------------------------------------------------

    def publish(self, event: ContextEvent) -> ContextEvent:
        """
        Publish ``event`` synchronously:

        1. Persist to the context engine (memory + DB).
        2. Dispatch to all CRITICAL-priority subscribers first
           (``ContextLane.KILL_SWITCH`` events).
        3. Dispatch to lane-specific subscribers.
        4. Dispatch to wildcard (all-lane) subscribers.

        Async subscribers encountered during a sync ``publish()`` call are
        scheduled as fire-and-forget tasks if a running event-loop exists;
        otherwise they are skipped with a warning.

        Returns the persisted event (with ``event_id`` populated).
        """
        # Step 1 — persist
        event = context_engine.record(event)

        # Step 2+3+4 — dispatch
        self._dispatch_sync(event)
        return event

    # ------------------------------------------------------------------
    # Publish API — asynchronous
    # ------------------------------------------------------------------

    async def apublish(self, event: ContextEvent) -> ContextEvent:
        """
        Async variant of ``publish()``:

        1. Persist to the context engine (memory + DB) in a threadpool.
        2. Await async subscribers; call sync ones directly.

        Use this from within FastAPI route handlers / background tasks.
        """
        # Offload blocking DB write to threadpool so we don't block the loop
        loop = asyncio.get_event_loop()
        event = await loop.run_in_executor(None, context_engine.record, event)

        await self._dispatch_async(event)
        return event

    # ------------------------------------------------------------------
    # Convenience publish helpers (mirror the state engine's named helpers)
    # ------------------------------------------------------------------

    def publish_user_prompt(self, text: str, session_id: str = "default") -> ContextEvent:
        return self.publish(ContextEvent(
            lane=ContextLane.USER_PROMPT,
            payload={"text": text},
            source="user",
            session_id=session_id,
        ))

    def publish_kill_switch(
        self,
        reason: str,
        triggered_by: str = "user",
        session_id: str = "default",
    ) -> ContextEvent:
        """
        Activate the emergency kill-switch.

        Always dispatched at CRITICAL severity; any registered
        ``KILL_SWITCH`` lane subscriber should immediately halt all
        open agent actions and sandbox proposals.
        """
        return self.publish(ContextEvent(
            lane=ContextLane.KILL_SWITCH,
            payload={"reason": reason, "triggered_by": triggered_by},
            severity=ContextSeverity.CRITICAL,
            source=triggered_by,
            session_id=session_id,
        ))

    def publish_agent_cot(
        self,
        trace: Dict[str, Any],
        session_id: str = "default",
        severity: ContextSeverity = ContextSeverity.INFO,
    ) -> ContextEvent:
        """Sync publish of an agent Chain-of-Thought trace."""
        return self.publish(ContextEvent(
            lane=ContextLane.AGENT_COT,
            payload=trace,
            severity=severity,
            source="agent",
            session_id=session_id,
        ))

    async def apublish_agent_cot(
        self,
        trace: Dict[str, Any],
        session_id: str = "default",
        severity: ContextSeverity = ContextSeverity.INFO,
    ) -> ContextEvent:
        """Async publish of an agent Chain-of-Thought trace."""
        return await self.apublish(ContextEvent(
            lane=ContextLane.AGENT_COT,
            payload=trace,
            severity=severity,
            source="agent",
            session_id=session_id,
        ))

    def publish_risk_metric(
        self,
        metrics: Dict[str, Any],
        session_id: str = "default",
        severity: ContextSeverity = ContextSeverity.INFO,
    ) -> ContextEvent:
        """Sync publish of risk/execution metrics."""
        return self.publish(ContextEvent(
            lane=ContextLane.RISK_METRIC,
            payload=metrics,
            severity=severity,
            source="agent",
            session_id=session_id,
        ))

    async def apublish_risk_metric(
        self,
        metrics: Dict[str, Any],
        session_id: str = "default",
        severity: ContextSeverity = ContextSeverity.INFO,
    ) -> ContextEvent:
        """Async publish of risk/execution metrics."""
        return await self.apublish(ContextEvent(
            lane=ContextLane.RISK_METRIC,
            payload=metrics,
            severity=severity,
            source="agent",
            session_id=session_id,
        ))

    def publish_market_intel(
        self,
        snapshot: Dict[str, Any],
        session_id: str = "default",
        severity: ContextSeverity = ContextSeverity.INFO,
    ) -> ContextEvent:
        """Sync publish of a market intelligence snapshot."""
        return self.publish(ContextEvent(
            lane=ContextLane.MARKET_INTEL,
            payload=snapshot,
            severity=severity,
            source="agent",
            session_id=session_id,
        ))

    async def apublish_market_intel(
        self,
        snapshot: Dict[str, Any],
        session_id: str = "default",
        severity: ContextSeverity = ContextSeverity.INFO,
    ) -> ContextEvent:
        """Async publish of a market intelligence snapshot."""
        return await self.apublish(ContextEvent(
            lane=ContextLane.MARKET_INTEL,
            payload=snapshot,
            severity=severity,
            source="agent",
            session_id=session_id,
        ))

    def publish_exec_log(
        self,
        log: Dict[str, Any],
        session_id: str = "default",
        severity: ContextSeverity = ContextSeverity.INFO,
    ) -> ContextEvent:
        """Sync publish of a trade / transaction execution log."""
        return self.publish(ContextEvent(
            lane=ContextLane.EXEC_LOG,
            payload=log,
            severity=severity,
            source="agent",
            session_id=session_id,
        ))

    async def apublish_exec_log(
        self,
        log: Dict[str, Any],
        session_id: str = "default",
        severity: ContextSeverity = ContextSeverity.INFO,
    ) -> ContextEvent:
        """Async publish of a trade / transaction execution log."""
        return await self.apublish(ContextEvent(
            lane=ContextLane.EXEC_LOG,
            payload=log,
            severity=severity,
            source="agent",
            session_id=session_id,
        ))

    def publish_risk_override(
        self,
        overrides: Dict[str, Any],
        session_id: str = "default",
        source: str = "user",
        severity: ContextSeverity = ContextSeverity.WARNING,
    ) -> ContextEvent:
        """Sync publish of a risk limit override."""
        return self.publish(ContextEvent(
            lane=ContextLane.RISK_OVERRIDE,
            payload=overrides,
            severity=severity,
            source=source,
            session_id=session_id,
        ))

    async def apublish_risk_override(
        self,
        overrides: Dict[str, Any],
        session_id: str = "default",
        source: str = "user",
        severity: ContextSeverity = ContextSeverity.WARNING,
    ) -> ContextEvent:
        """Async publish of a risk limit override."""
        return await self.apublish(ContextEvent(
            lane=ContextLane.RISK_OVERRIDE,
            payload=overrides,
            severity=severity,
            source=source,
            session_id=session_id,
        ))

    def safe_publish(self, event: ContextEvent) -> Optional[ContextEvent]:
        """
        Safe, non-blocking sync publish fallback.
        Swallows any exceptions so caller's business flow is never disrupted.
        """
        try:
            return self.publish(event)
        except Exception as exc:
            logger.error("safe_publish failed for lane %s: %s", event.lane, exc)
            return None

    async def safe_apublish(self, event: ContextEvent) -> Optional[ContextEvent]:
        """
        Safe async publish fallback.
        Swallows any exceptions so caller's business flow is never disrupted.
        """
        try:
            return await self.apublish(event)
        except Exception as exc:
            logger.error("safe_apublish failed for lane %s: %s", event.lane, exc)
            return None

    # ------------------------------------------------------------------
    # Internal dispatch helpers
    # ------------------------------------------------------------------

    def _get_subscribers_for(self, event: ContextEvent) -> List[Subscription]:
        """
        Build the ordered subscriber list for an event:
        kill-switch handlers first → lane-specific → wildcard.
        """
        subs: List[Subscription] = []
        with self._lock:
            # Priority: KILL_SWITCH always dispatched to its lane subs first
            if event.lane == ContextLane.KILL_SWITCH:
                subs.extend(list(self._subscribers.get(ContextLane.KILL_SWITCH, [])))

            # Lane-specific (skip duplicates if kill_switch lane already added)
            if event.lane != ContextLane.KILL_SWITCH:
                subs.extend(list(self._subscribers.get(event.lane, [])))

            # Wildcard / all-lanes
            subs.extend(list(self._subscribers.get(None, [])))

        # Deduplicate preserving order (handles edge case of double-registration)
        seen: Set[int] = set()
        deduped: List[Subscription] = []
        for s in subs:
            if s.sub_id not in seen:
                seen.add(s.sub_id)
                deduped.append(s)
        return deduped

    def _dispatch_sync(self, event: ContextEvent) -> None:
        """Dispatch to all subscribers synchronously."""
        for sub in self._get_subscribers_for(event):
            try:
                if sub.is_async:
                    # Best-effort: schedule onto running loop
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(sub.callback(event))
                    except RuntimeError:
                        logger.warning(
                            "Async subscriber %d skipped: no running event loop.",
                            sub.sub_id,
                        )
                else:
                    sub.callback(event)
            except Exception as exc:
                logger.error(
                    "Subscriber %d raised during dispatch: %s", sub.sub_id, exc
                )

    async def _dispatch_async(self, event: ContextEvent) -> None:
        """Dispatch to all subscribers; await async ones concurrently."""
        subs = self._get_subscribers_for(event)
        tasks = []
        for sub in subs:
            try:
                if sub.is_async:
                    tasks.append(sub.callback(event))
                else:
                    sub.callback(event)
            except Exception as exc:
                logger.error(
                    "Subscriber %d raised during async dispatch: %s", sub.sub_id, exc
                )
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error("Async subscriber raised: %s", result)


# ===========================================================================
# 4. Built-in default subscribers
# ===========================================================================

def _builtin_kill_switch_handler(event: ContextEvent) -> None:
    """
    Default KILL_SWITCH handler — logs CRITICAL and records a system alert.

    In Phase 2+ this will trigger the repair engine and halt any pending
    sandbox proposals.  For Phase 1 we ensure the alert is persisted.
    """
    logger.critical(
        "[KILL SWITCH] Activated — reason: %s | triggered_by: %s | session: %s",
        event.payload.get("reason", "unknown"),
        event.payload.get("triggered_by", "unknown"),
        event.session_id,
    )
    # Record an accompanying system alert so the kill-switch is visible
    # in the SYSTEM_ALERT lane as well (different lane, no infinite loop)
    context_engine.record_system_alert(
        message="EMERGENCY KILL SWITCH ACTIVATED",
        details={
            "reason":       event.payload.get("reason"),
            "triggered_by": event.payload.get("triggered_by"),
            "event_id":     event.event_id,
        },
        severity=ContextSeverity.CRITICAL,
        session_id=event.session_id,
    )


def _builtin_critical_alert_logger(event: ContextEvent) -> None:
    """
    Wildcard handler — logs any CRITICAL severity event to the Python
    logger so it appears in the server console / log file immediately.
    """
    if event.severity == ContextSeverity.CRITICAL:
        logger.critical(
            "[COPILOT CRITICAL] lane=%s session=%s payload=%s",
            event.lane.value,
            event.session_id,
            event.payload,
        )


# ===========================================================================
# 5. Module-Level Singleton
# ===========================================================================

#: Global CopilotEventBus instance — import this in routers and services.
event_bus: CopilotEventBus = CopilotEventBus()

# Register built-in handlers
event_bus.subscribe(_builtin_kill_switch_handler, lane=ContextLane.KILL_SWITCH)
event_bus.subscribe(_builtin_critical_alert_logger)   # all lanes

__all__ = [
    "CopilotEventBus",
    "Subscription",
    "Subscriber",
    "event_bus",
    # Re-export for convenience so callers only need one import
    "ContextEvent",
    "ContextLane",
    "ContextSeverity",
]
