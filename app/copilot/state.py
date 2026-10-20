"""
state.py – CopilotContextEngine
================================
Requirement A: Full-Context State Tracker (Inputs + Model Outputs).

Maintains a unified, bounded in-memory context store for:
  • User context  : prompts, risk overrides, strategy params, watchlists.
  • Model output  : market intelligence snapshots, agent CoT reasoning
                    traces, risk/execution metrics (P&L, slippage, stops).

Design decisions
----------------
* In-memory store uses ``collections.deque`` ring buffers for each
  context lane — O(1) append, bounded RAM, no GIL-heavy locking needed
  for CPython because we only ever append/read (no concurrent mutation).
* A separate SQLite table ``copilot_events`` provides durable persistence
  so context survives server restarts without touching existing tables.
* The engine is instantiated once as a module-level singleton
  (``context_engine``) and imported by routers / the event bus.
* No changes are made to any existing service, router, or schema file —
  full backward compatibility is guaranteed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Deque, Dict, List, Optional

# ---------------------------------------------------------------------------
# Re-use the existing DB connection factory — read-only import, no mutation.
# ---------------------------------------------------------------------------
from app.database import get_db_connection, DB_PATH


# ===========================================================================
# 1. Domain Enumerations
# ===========================================================================

class ContextLane(str, Enum):
    """Logical partitions of the unified state context."""
    USER_PROMPT       = "user_prompt"       # Raw user queries / NL instructions
    RISK_OVERRIDE     = "risk_override"     # User-defined risk limit changes
    STRATEGY_PARAM    = "strategy_param"    # Strategy configuration updates
    WATCHLIST         = "watchlist"         # Custom asset/category watchlists
    MARKET_INTEL      = "market_intel"      # Market intelligence snapshots
    AGENT_COT         = "agent_cot"         # Chain-of-Thought / PoT traces
    RISK_METRIC       = "risk_metric"       # P&L, slippage, stop-loss levels
    EXEC_LOG          = "exec_log"          # Trade / sandbox execution records
    KILL_SWITCH       = "kill_switch"       # Emergency halt events
    SYSTEM_ALERT      = "system_alert"      # Internal system-level alerts


class ContextSeverity(str, Enum):
    """Severity level for context events — used for filtering & display."""
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"


# ===========================================================================
# 2. Core Data Model
# ===========================================================================

@dataclass
class ContextEvent:
    """
    Atomic unit of copilot context state.

    Every piece of information flowing through the system — a user prompt,
    an agent CoT trace, a risk override, an execution log — is captured as
    a ``ContextEvent`` and routed into the appropriate lane of the engine.
    """
    lane:        ContextLane
    payload:     Dict[str, Any]
    severity:    ContextSeverity  = ContextSeverity.INFO
    source:      str              = "system"          # "user" | "agent" | "system"
    timestamp:   str              = field(default_factory=lambda: _now_iso())
    event_id:    Optional[int]    = None              # Set after DB persistence
    session_id:  str              = "default"         # Future multi-session support

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["lane"]     = self.lane.value
        d["severity"] = self.severity.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ContextEvent":
        d = dict(d)
        d["lane"]     = ContextLane(d["lane"])
        d["severity"] = ContextSeverity(d.get("severity", ContextSeverity.INFO))
        if isinstance(d.get("payload"), str):
            d["payload"] = json.loads(d["payload"])
        return cls(**d)


# ===========================================================================
# 3. DB Migration Helper
# ===========================================================================

def _ensure_copilot_schema(conn: sqlite3.Connection) -> None:
    """
    Idempotently creates the ``copilot_events`` table.

    Called once during engine initialisation. Uses ``CREATE TABLE IF NOT
    EXISTS`` so it never interferes with existing tables or data.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS copilot_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT    NOT NULL DEFAULT 'default',
            lane        TEXT    NOT NULL,
            source      TEXT    NOT NULL DEFAULT 'system',
            severity    TEXT    NOT NULL DEFAULT 'INFO',
            payload_json TEXT   NOT NULL,
            timestamp   TEXT    NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Idempotent index for fast lane + session queries
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_copilot_events_lane_session
        ON copilot_events (lane, session_id, id DESC)
    """)
    conn.commit()


# ===========================================================================
# 4. CopilotContextEngine — the unified state tracker
# ===========================================================================

# Default ring-buffer capacity per lane (tunable via env or init param)
_DEFAULT_MAX_PER_LANE: int = 200


class CopilotContextEngine:
    """
    Unified state context engine for the FinAgent Copilot.

    Architecture
    ------------
    * **In-memory ring buffers** (``deque(maxlen=N)``) keyed by
      ``ContextLane`` — gives O(1) write / O(N) full-scan, bounded RAM.
    * **SQLite persistence** to ``copilot_events`` — survives restarts.
    * **Thread-safe** via a per-instance ``threading.Lock`` — safe for
      FastAPI's async workers calling sync helpers in a threadpool.

    Public API
    ----------
    record(event)                  – Persist one ContextEvent (memory + DB).
    query(lane, limit, session_id) – Retrieve recent events from a lane.
    snapshot()                     – Full multi-lane summary dict.
    get_user_context(session_id)   – All user-side lanes for a session.
    get_model_output_context(sid)  – All agent/system lanes for a session.
    clear_session(session_id)      – Wipe a session from memory (not DB).
    load_recent_from_db(n)         – Warm-up in-memory store from DB.
    """

    def __init__(self, max_per_lane: int = _DEFAULT_MAX_PER_LANE) -> None:
        self._max_per_lane = max_per_lane
        self._lock         = threading.Lock()

        # One ring-buffer per lane
        self._buffers: Dict[ContextLane, Deque[ContextEvent]] = {
            lane: deque(maxlen=max_per_lane) for lane in ContextLane
        }

        # Initialise DB schema (idempotent)
        conn = get_db_connection()
        try:
            _ensure_copilot_schema(conn)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Core write path
    # ------------------------------------------------------------------

    def record(self, event: ContextEvent) -> ContextEvent:
        """
        Persist a single ContextEvent:
        1. Write to in-memory ring buffer.
        2. Write to SQLite ``copilot_events`` and attach the new row ID.

        Returns the event with ``event_id`` populated.
        """
        # DB write first so we get an auto-increment ID
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO copilot_events
                    (session_id, lane, source, severity, payload_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.session_id,
                    event.lane.value,
                    event.source,
                    event.severity.value,
                    json.dumps(event.payload, ensure_ascii=False),
                    event.timestamp,
                ),
            )
            conn.commit()
            event.event_id = cursor.lastrowid
        except Exception as exc:          # pragma: no cover
            # Never crash the caller; log and continue
            print(f"[CopilotContextEngine] DB write error: {exc}")
        finally:
            conn.close()

        # In-memory append (thread-safe)
        with self._lock:
            self._buffers[event.lane].append(event)

        return event

    # ------------------------------------------------------------------
    # Convenience record helpers (named constructors)
    # ------------------------------------------------------------------

    def record_user_prompt(
        self,
        text: str,
        session_id: str = "default",
        extra: Optional[Dict[str, Any]] = None,
    ) -> ContextEvent:
        """Capture a raw user natural-language prompt."""
        return self.record(ContextEvent(
            lane=ContextLane.USER_PROMPT,
            payload={"text": text, **(extra or {})},
            source="user",
            session_id=session_id,
        ))

    def record_risk_override(
        self,
        overrides: Dict[str, Any],
        session_id: str = "default",
        source: str = "user",
    ) -> ContextEvent:
        """Capture a user-driven risk parameter override."""
        return self.record(ContextEvent(
            lane=ContextLane.RISK_OVERRIDE,
            payload=overrides,
            severity=ContextSeverity.WARNING,
            source=source,
            session_id=session_id,
        ))

    def record_strategy_param(
        self,
        params: Dict[str, Any],
        session_id: str = "default",
    ) -> ContextEvent:
        """Capture strategy parameter configuration changes."""
        return self.record(ContextEvent(
            lane=ContextLane.STRATEGY_PARAM,
            payload=params,
            source="user",
            session_id=session_id,
        ))

    def record_watchlist_update(
        self,
        items: List[str],
        action: str = "add",
        session_id: str = "default",
    ) -> ContextEvent:
        """Capture a watchlist add/remove operation."""
        return self.record(ContextEvent(
            lane=ContextLane.WATCHLIST,
            payload={"items": items, "action": action},
            source="user",
            session_id=session_id,
        ))

    def record_market_intel(
        self,
        snapshot: Dict[str, Any],
        session_id: str = "default",
    ) -> ContextEvent:
        """Capture a market intelligence snapshot (sentiment, indicators, OB)."""
        return self.record(ContextEvent(
            lane=ContextLane.MARKET_INTEL,
            payload=snapshot,
            source="agent",
            session_id=session_id,
        ))

    def record_agent_cot(
        self,
        trace: Dict[str, Any],
        session_id: str = "default",
        severity: ContextSeverity = ContextSeverity.INFO,
    ) -> ContextEvent:
        """Capture an agent Chain-of-Thought / Program-of-Thought reasoning trace."""
        return self.record(ContextEvent(
            lane=ContextLane.AGENT_COT,
            payload=trace,
            severity=severity,
            source="agent",
            session_id=session_id,
        ))

    def record_risk_metric(
        self,
        metrics: Dict[str, Any],
        session_id: str = "default",
        severity: ContextSeverity = ContextSeverity.INFO,
    ) -> ContextEvent:
        """Capture a risk/execution metric snapshot (P&L, slippage, stops)."""
        return self.record(ContextEvent(
            lane=ContextLane.RISK_METRIC,
            payload=metrics,
            severity=severity,
            source="agent",
            session_id=session_id,
        ))

    def record_exec_log(
        self,
        log: Dict[str, Any],
        session_id: str = "default",
    ) -> ContextEvent:
        """Capture a trade / sandbox execution record."""
        return self.record(ContextEvent(
            lane=ContextLane.EXEC_LOG,
            payload=log,
            source="agent",
            session_id=session_id,
        ))

    def record_kill_switch(
        self,
        reason: str,
        triggered_by: str = "user",
        session_id: str = "default",
    ) -> ContextEvent:
        """
        Capture an emergency kill-switch activation.
        Always emitted at CRITICAL severity so downstream listeners can
        immediately halt all open agent actions.
        """
        return self.record(ContextEvent(
            lane=ContextLane.KILL_SWITCH,
            payload={"reason": reason, "triggered_by": triggered_by},
            severity=ContextSeverity.CRITICAL,
            source=triggered_by,
            session_id=session_id,
        ))

    def record_system_alert(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        severity: ContextSeverity = ContextSeverity.WARNING,
        session_id: str = "default",
    ) -> ContextEvent:
        """Capture an internal system-level alert (e.g. DB error, auth failure)."""
        return self.record(ContextEvent(
            lane=ContextLane.SYSTEM_ALERT,
            payload={"message": message, **(details or {})},
            severity=severity,
            source="system",
            session_id=session_id,
        ))

    # ------------------------------------------------------------------
    # Query / inspection path
    # ------------------------------------------------------------------

    def query(
        self,
        lane: ContextLane,
        limit: int = 50,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return the most recent ``limit`` events from a lane.

        Reads from the in-memory buffer (fast) and optionally filters
        by ``session_id``.  Events are returned newest-first.
        """
        with self._lock:
            buf: List[ContextEvent] = list(self._buffers[lane])

        if session_id is not None:
            buf = [e for e in buf if e.session_id == session_id]

        # newest-first
        return [e.to_dict() for e in reversed(buf[-limit:])]

    def query_db(
        self,
        lane: ContextLane,
        limit: int = 100,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch events directly from SQLite — used for historical queries
        that exceed the in-memory ring buffer size.
        """
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            if session_id:
                cursor.execute(
                    """
                    SELECT id, session_id, lane, source, severity,
                           payload_json, timestamp
                    FROM copilot_events
                    WHERE lane = ? AND session_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (lane.value, session_id, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, session_id, lane, source, severity,
                           payload_json, timestamp
                    FROM copilot_events
                    WHERE lane = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (lane.value, limit),
                )
            rows = cursor.fetchall()
        finally:
            conn.close()

        results = []
        for row in rows:
            r = dict(row)
            try:
                r["payload"] = json.loads(r.pop("payload_json", "{}"))
            except (json.JSONDecodeError, KeyError):
                r["payload"] = {}
            results.append(r)
        return results

    def get_user_context(self, session_id: str = "default") -> Dict[str, List[Dict[str, Any]]]:
        """
        Return all user-side lanes (prompts, risk overrides, strategy
        params, watchlists) for a given session — newest-first per lane.
        """
        user_lanes = [
            ContextLane.USER_PROMPT,
            ContextLane.RISK_OVERRIDE,
            ContextLane.STRATEGY_PARAM,
            ContextLane.WATCHLIST,
        ]
        return {
            lane.value: self.query(lane, session_id=session_id)
            for lane in user_lanes
        }

    def get_model_output_context(self, session_id: str = "default") -> Dict[str, List[Dict[str, Any]]]:
        """
        Return all model/agent output lanes (market intel, CoT, risk
        metrics, exec logs) for a given session — newest-first per lane.
        """
        model_lanes = [
            ContextLane.MARKET_INTEL,
            ContextLane.AGENT_COT,
            ContextLane.RISK_METRIC,
            ContextLane.EXEC_LOG,
            ContextLane.KILL_SWITCH,
            ContextLane.SYSTEM_ALERT,
        ]
        return {
            lane.value: self.query(lane, session_id=session_id)
            for lane in model_lanes
        }

    def snapshot(self) -> Dict[str, Any]:
        """
        Returns a complete multi-lane summary — each lane's last 10
        events, total event counts, and kill-switch status.
        """
        with self._lock:
            counts = {lane.value: len(self._buffers[lane]) for lane in ContextLane}
            kill_events = list(self._buffers[ContextLane.KILL_SWITCH])

        kill_active = (
            len(kill_events) > 0
            and kill_events[-1].severity == ContextSeverity.CRITICAL
        )

        return {
            "kill_switch_active": kill_active,
            "event_counts":       counts,
            "lanes": {
                lane.value: self.query(lane, limit=10)
                for lane in ContextLane
            },
        }

    def clear_session(self, session_id: str) -> int:
        """
        Remove all in-memory events for ``session_id`` from every lane.
        Returns the number of events evicted.
        Persisted DB records are NOT deleted (audit trail must remain intact).
        """
        evicted = 0
        with self._lock:
            for lane in ContextLane:
                buf = self._buffers[lane]
                keep = [e for e in buf if e.session_id != session_id]
                evicted += len(buf) - len(keep)
                # Rebuild the deque preserving maxlen
                self._buffers[lane] = deque(keep, maxlen=self._max_per_lane)
        return evicted

    def load_recent_from_db(self, n_per_lane: int = 50) -> int:
        """
        Warm-up the in-memory store by reading the N most recent
        events per lane from the database.

        Useful on server restart so the copilot has immediate context
        without requiring a full replay.  Returns total events loaded.
        """
        total = 0
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            for lane in ContextLane:
                cursor.execute(
                    """
                    SELECT id, session_id, lane, source, severity,
                           payload_json, timestamp
                    FROM copilot_events
                    WHERE lane = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (lane.value, n_per_lane),
                )
                rows = cursor.fetchall()
                with self._lock:
                    for row in reversed(rows):  # oldest-first into buffer
                        try:
                            evt = ContextEvent(
                                lane=ContextLane(row["lane"]),
                                payload=json.loads(row["payload_json"] or "{}"),
                                severity=ContextSeverity(row["severity"]),
                                source=row["source"],
                                timestamp=row["timestamp"],
                                event_id=row["id"],
                                session_id=row["session_id"],
                            )
                            self._buffers[lane].append(evt)
                            total += 1
                        except Exception:  # pragma: no cover
                            pass
        finally:
            conn.close()
        return total


# ===========================================================================
# 5. Module-Level Singleton
# ===========================================================================

#: Global CopilotContextEngine instance — import this in routers and the bus.
context_engine: CopilotContextEngine = CopilotContextEngine()


# ===========================================================================
# 6. Utility helpers
# ===========================================================================

def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
