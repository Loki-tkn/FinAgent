"""
nl_engine.py – CopilotNLEngine
================================
Phase 2: Natural-Language Query Engine with CoT lookup and
rule-based explainability.

Design
------
* **Zero external LLM dependency** — purely rule-based intent detection
  + structured data retrieval, so it works in any offline environment.
* Intent detection uses a weighted keyword scoring table (no regex
  required for common queries; regex used only for entity extraction).
* Each intent handler fetches from the CopilotContextEngine ring buffers
  AND from the live SQLite financial tables to produce rich, factual
  answers grounded in actual user data.
* All DB calls are blocking (sqlite3 is sync-only); the router wraps
  calls in ``run_in_executor`` so the FastAPI async event loop is never
  blocked.
* The engine is instantiated once as a module-level singleton (``nl_engine``).
"""

from __future__ import annotations

import os
import re
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
    if not os.getenv("OPENROUTER_API_KEY"):
        load_dotenv(".env.example")
except Exception:
    pass

logger = logging.getLogger("finagent.copilot.nl_engine")

# OpenRouter Configuration Defaults
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-3.5-sonnet"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/FinAgent"
OPENROUTER_TITLE = "FinAgent Copilot"


def is_openrouter_configured() -> bool:
    """Check if OPENROUTER_API_KEY is configured in the environment."""
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())


def get_openrouter_model() -> str:
    """Return configured OpenRouter model or default."""
    return os.getenv("OPENROUTER_MODEL", "").strip() or DEFAULT_OPENROUTER_MODEL

from app.copilot.state import (
    ContextLane,
    ContextSeverity,
    context_engine,
)
from app.copilot.schemas import CopilotChatResponse, SourceReference
from app.database import get_db_connection
from app.services.anomaly import detect_spending_anomalies
from app.services.health_score import calculate_financial_health_score
from app.services.metrics import monthly_avg_expense


# ===========================================================================
# 1. Intent Catalogue
# ===========================================================================

#: Each entry: (intent_name, weight_table of keyword → score)
#: The intent with the highest accumulated score wins.

_INTENT_KEYWORDS: List[Tuple[str, Dict[str, int]]] = [
    ("explain_decision", {
        "why":        3, "reason":     3, "explain":    3, "how come":   3,
        "decision":   2, "flagged":    2, "detected":   2, "triggered":  2,
        "agent":      1, "chose":      2, "recommended":2,
    }),
    ("query_anomaly", {
        "anomaly":    4, "anomalies":  4, "spike":      3, "unusual":    3,
        "flagged":    3, "outlier":    2, "z-score":    3, "z score":    3,
        "suspicious": 2, "alert":      2,
    }),
    ("query_health", {
        "health":     4, "score":      3, "grade":      3, "rating":     3,
        "financial":  2, "fit":        2, "wellness":   2,
    }),
    ("query_goal", {
        "goal":       4, "goals":      4, "target":     3, "saving":     3,
        "savings":    3, "progress":   3, "milestone":  2, "reach":      2,
        "track":      2,
    }),
    ("query_risk", {
        "risk":       4, "p&l":        3, "pnl":        3, "slippage":   3,
        "stop-loss":  3, "stop loss":  3, "drawdown":   3, "metric":     2,
        "exposure":   2,
    }),
    ("query_cot", {
        "reasoning":  4, "chain":      4, "thought":    4, "cot":        5,
        "trace":      4, "log":        3, "agent log":  4, "thinking":   3,
        "decision log":4,
    }),
    ("kill_switch", {
        "halt":       5, "stop":       4, "kill":       5, "emergency":  5,
        "abort":      5, "pause":      3, "freeze":     4,
    }),
    ("query_context", {
        "context":    4, "snapshot":   4, "state":      3, "summary":    2,
        "lanes":      3, "bus":        2,
    }),
    ("query_budget", {
        "budget":     4, "spending":   3, "expense":    3, "expenses":   3,
        "category":   3, "breakdown":  3, "monthly":    2,
    }),
    ("help", {
        "help":       5, "what can":   4, "commands":   4, "capabilities":4,
        "features":   3, "how to":     2,
    }),
]


# ===========================================================================
# 2. Intent Classifier
# ===========================================================================

def _classify_intent(message: str) -> Tuple[str, float]:
    """
    Classify ``message`` into an intent using keyword scoring.

    Returns (intent_name, confidence) where confidence ∈ [0, 1].
    """
    lower = message.lower()
    scores: Dict[str, int] = {}
    max_possible: Dict[str, int] = {}

    for intent, kw_table in _INTENT_KEYWORDS:
        total = sum(kw_table.values())
        hit   = sum(score for kw, score in kw_table.items() if kw in lower)
        scores[intent]      = hit
        max_possible[intent] = total

    best_intent = max(scores, key=lambda k: scores[k])
    best_score  = scores[best_intent]
    best_max    = max_possible[best_intent]

    if best_score == 0:
        return ("unknown", 0.0)

    # Normalise against the best possible score for that intent table
    confidence = min(1.0, round(best_score / max(best_max * 0.4, 1), 3))
    return (best_intent, confidence)


def _extract_event_id(message: str) -> Optional[int]:
    """Pull an explicit event ID from a message like 'explain event 42' or '#77'."""
    m = re.search(r'(?:\b(?:event|id)\s*|#)(\d+)\b', message, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _extract_lane_name(message: str) -> Optional[str]:
    """Extract a lane name from a query like 'show agent_cot lane'."""
    valid_lanes = {lane.value for lane in ContextLane}
    lower = message.lower().replace(" ", "_").replace("-", "_")
    for lane in valid_lanes:
        if lane in lower:
            return lane
    return None


# ===========================================================================
# 3. Data Fetchers — blocking (call via run_in_executor from async router)
# ===========================================================================

def _fetch_user_profile(user_id: int = 1) -> Dict[str, Any]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {}


def _fetch_recent_anomalies(user_id: int = 1, limit: int = 5) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, date, category, amount, description, anomaly_reason
        FROM transactions
        WHERE user_id = ? AND is_anomaly = 1
        ORDER BY date DESC, id DESC
        LIMIT ?
    """, (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _fetch_goals(user_id: int = 1) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, target_amount, current_savings, monthly_contribution,
               target_date, selected_plan, status
        FROM goals WHERE user_id = ?
        ORDER BY id ASC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _fetch_health_score(user_id: int = 1) -> Dict[str, Any]:
    prof = _fetch_user_profile(user_id=user_id)
    monthly_income = float(prof.get("monthly_income") or 0.0)
    emergency_months = int(prof.get("emergency_months") or 6)

    metrics = monthly_avg_expense(user_id=user_id, exclude_anomalies=True)
    goals = _fetch_goals(user_id=user_id)
    total_savings = sum(float(g.get("current_savings") or 0.0) for g in goals)

    return calculate_financial_health_score(
        monthly_income=monthly_income,
        total_monthly_expense=float(metrics.get("total", 0.0)),
        essential_expense=float(metrics.get("essential", 0.0)),
        discretionary_expense=float(metrics.get("discretionary", 0.0)),
        current_savings=total_savings,
        emergency_target_months=emergency_months,
    )



def _fetch_copilot_event_by_id(event_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, session_id, lane, source, severity, payload_json, timestamp
        FROM copilot_events WHERE id = ?
    """, (event_id,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    import json
    r = dict(row)
    try:
        r["payload"] = json.loads(r.pop("payload_json", "{}"))
    except Exception:
        r["payload"] = {}
    return r


def _fetch_related_events(lane: str, timestamp: str, window: int = 5) -> List[Dict[str, Any]]:
    """Fetch up to ``window`` events near a given timestamp in a lane."""
    import json as _json
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, lane, source, severity, payload_json, timestamp
        FROM copilot_events
        WHERE lane = ? AND ABS(julianday(timestamp) - julianday(?)) < 0.01
        ORDER BY id DESC LIMIT ?
    """, (lane, timestamp, window))
    rows = cursor.fetchall()
    conn.close()
    results = []
    for row in rows:
        r = dict(row)
        try:
            r["payload"] = _json.loads(r.pop("payload_json", "{}"))
        except Exception:
            r["payload"] = {}
        results.append(r)
    return results


# ===========================================================================
# 4. Intent Handlers — each returns (reply_str, sources, confidence)
# ===========================================================================

def _handle_explain_decision(
    message: str,
    session_id: str,
) -> Tuple[str, List[SourceReference], float]:
    """
    "Why did the agent flag/decide X?"
    1. Check for explicit event_id in message → look up copilot_events.
    2. Otherwise search CoT lane for relevant payloads.
    3. Fall back to recent anomaly table.
    """
    sources: List[SourceReference] = []

    # Try explicit event ID first
    eid = _extract_event_id(message)
    if eid:
        evt = _fetch_copilot_event_by_id(eid)
        if evt:
            payload = evt.get("payload", {})
            reply = (
                f"**Event #{eid} Explanation** (lane: `{evt['lane']}`, "
                f"source: `{evt['source']}`, severity: `{evt['severity']}`):\n\n"
                f"**Timestamp:** {evt['timestamp']}\n\n"
                f"**Payload:**\n"
                + "\n".join(f"  • `{k}`: {v}" for k, v in payload.items())
            )
            sources.append(SourceReference(
                event_id=eid, lane=evt["lane"],
                timestamp=evt["timestamp"],
                summary=f"Event #{eid} payload retrieved"
            ))
            return reply, sources, 0.95

    # Search CoT lane in-memory
    cot_events = context_engine.query(ContextLane.AGENT_COT, limit=10, session_id=session_id)
    if cot_events:
        latest = cot_events[0]
        payload = latest.get("payload", {})
        step = payload.get("step", "unknown step")
        conf = payload.get("confidence", "N/A")
        decision = payload.get("decision", payload.get("action", "N/A"))
        reply = (
            f"**Latest Agent Reasoning Trace:**\n\n"
            f"  • **Step:** `{step}`\n"
            f"  • **Decision:** `{decision}`\n"
            f"  • **Confidence:** `{conf}`\n\n"
        )
        for k, v in payload.items():
            if k not in ("step", "confidence", "decision", "action"):
                reply += f"  • **{k}:** {v}\n"
        sources.append(SourceReference(
            event_id=latest.get("event_id"),
            lane="agent_cot",
            timestamp=latest.get("timestamp", ""),
            summary=f"Agent step '{step}' with confidence {conf}"
        ))
        return reply, sources, 0.80

    # Fall back to most recent anomaly record from DB
    anomalies = _fetch_recent_anomalies(limit=3)
    if anomalies:
        a = anomalies[0]
        reply = (
            f"**Most Recent Flagged Transaction:**\n\n"
            f"  • **Date:** {a['date']}\n"
            f"  • **Category:** {a['category']}\n"
            f"  • **Amount:** ${a['amount']:,.2f}\n"
            f"  • **Description:** {a['description']}\n"
            f"  • **Reason:** {a.get('anomaly_reason', 'Z-score anomaly detected')}\n"
        )
        sources.append(SourceReference(
            event_id=None, lane="exec_log",
            timestamp=a["date"],
            summary=f"Anomaly TX #{a['id']}: {a['description']}"
        ))
        return reply, sources, 0.70

    return (
        "No agent reasoning traces or anomaly records found in the current context. "
        "Run an anomaly scan or wait for the agent to produce CoT logs.",
        [], 0.30
    )


def _handle_query_anomaly(
    message: str,
    session_id: str,
) -> Tuple[str, List[SourceReference], float]:
    """Show recent anomaly detections from DB."""
    anomalies = _fetch_recent_anomalies(limit=5)
    if not anomalies:
        return (
            "✅ **No anomalies detected.** All recent transactions are within normal spending patterns.",
            [], 0.90
        )

    lines = [f"**{len(anomalies)} Recent Spending Anomaly/ies Detected:**\n"]
    sources: List[SourceReference] = []
    for a in anomalies:
        lines.append(
            f"  • **#{a['id']}** | {a['date']} | **{a['category']}** | "
            f"${a['amount']:,.2f} — _{a['description']}_\n"
            f"    ↳ {a.get('anomaly_reason', 'Anomaly flag set.')}"
        )
        sources.append(SourceReference(
            event_id=None, lane="exec_log",
            timestamp=a["date"],
            summary=f"Anomaly: {a['description']} ${a['amount']:,.2f}"
        ))

    return "\n".join(lines), sources, 0.95


def _handle_query_health(
    message: str,
    session_id: str,
) -> Tuple[str, List[SourceReference], float]:
    """Compute and report the financial health score."""
    health = _fetch_health_score(user_id=1)

    score   = health["overall_score"]
    rating  = health["rating"]
    grade   = health["grade"]
    advice  = health["summary_recommendation"]
    bd      = health["breakdown"]

    if health.get("needs_setup"):
        return (
            "⚠️ **Profile setup required.** Please set your monthly income in the Profile section "
            "before the health score can be calculated.",
            [], 0.70
        )

    reply = (
        f"## 💊 Financial Health Score\n\n"
        f"**Score:** {score}/100 — **{rating}** (Grade **{grade}**)\n\n"
        f"| Pillar | Score | Max | Detail |\n"
        f"|--------|-------|-----|---------|\n"
        f"| Savings Ratio | {bd['savings_ratio']['score']} | 30 | {bd['savings_ratio']['detail']} |\n"
        f"| Essential Adherence | {bd['essential_adherence']['score']} | 25 | {bd['essential_adherence']['detail']} |\n"
        f"| Discretionary Control | {bd['discretionary_control']['score']} | 20 | {bd['discretionary_control']['detail']} |\n"
        f"| Emergency Buffer | {bd['emergency_buffer']['score']} | 25 | {bd['emergency_buffer']['detail']} |\n\n"
        f"**Recommendation:** {advice}"
    )
    return reply, [], 0.95


def _handle_query_goal(
    message: str,
    session_id: str,
) -> Tuple[str, List[SourceReference], float]:
    """Report on savings goals and their progress."""
    goals = _fetch_goals()
    if not goals:
        return (
            "No savings goals found. Create a goal via the Goals section of the app.",
            [], 0.85
        )

    lines = [f"## 🎯 Savings Goals ({len(goals)} total)\n"]
    for g in goals:
        target   = float(g["target_amount"])
        saved    = float(g["current_savings"])
        pct      = round((saved / target * 100), 1) if target > 0 else 0.0
        bar_fill = min(20, max(0, int(pct / 5)))
        bar      = "█" * bar_fill + "░" * (20 - bar_fill)
        lines.append(
            f"**{g['title']}**\n"
            f"  {bar} {pct}%\n"
            f"  Saved: ${saved:,.0f} / Target: ${target:,.0f} | "
            f"Plan: {g['selected_plan']} | Status: {g['status']}\n"
            f"  Target Date: {g['target_date']}\n"
        )
    return "\n".join(lines), [], 0.90


def _handle_query_risk(
    message: str,
    session_id: str,
) -> Tuple[str, List[SourceReference], float]:
    """Report on risk metrics from the RISK_METRIC lane."""
    events = context_engine.query(ContextLane.RISK_METRIC, limit=10, session_id=session_id)
    sources: List[SourceReference] = []

    if not events:
        # Fall back to DB risk override events
        events = context_engine.query(ContextLane.RISK_OVERRIDE, limit=5, session_id=session_id)
        if not events:
            return (
                "No risk metrics in context yet. Metrics are populated when the agent "
                "publishes risk events via the event bus.",
                [], 0.60
            )

    lines = [f"## ⚠️ Risk Metrics ({len(events)} events)\n"]
    for evt in events[:5]:
        payload = evt.get("payload", {})
        ts      = evt.get("timestamp", "")[:19]
        sev     = evt.get("severity", "INFO")
        lines.append(f"**[{sev}]** `{ts}`")
        for k, v in payload.items():
            lines.append(f"  • `{k}`: {v}")
        sources.append(SourceReference(
            event_id=evt.get("event_id"),
            lane="risk_metric",
            timestamp=ts,
            summary=", ".join(f"{k}={v}" for k, v in list(payload.items())[:2])
        ))
        lines.append("")

    return "\n".join(lines), sources, 0.85


def _handle_query_cot(
    message: str,
    session_id: str,
) -> Tuple[str, List[SourceReference], float]:
    """Show recent agent Chain-of-Thought traces."""
    events = context_engine.query(ContextLane.AGENT_COT, limit=10, session_id=session_id)
    sources: List[SourceReference] = []

    if not events:
        return (
            "No agent CoT traces in context for this session. "
            "Traces are added when the agent processes decisions.",
            [], 0.70
        )

    lines = [f"## 🧠 Agent Reasoning Traces ({len(events)} recent)\n"]
    for i, evt in enumerate(events, 1):
        payload = evt.get("payload", {})
        ts      = evt.get("timestamp", "")[:19]
        step    = payload.get("step", f"step-{i}")
        conf    = payload.get("confidence", "N/A")
        lines.append(f"**Trace {i}** | `{ts}` | Step: `{step}` | Confidence: `{conf}`")
        for k, v in payload.items():
            if k not in ("step", "confidence"):
                lines.append(f"  • `{k}`: {v}")
        sources.append(SourceReference(
            event_id=evt.get("event_id"),
            lane="agent_cot",
            timestamp=ts,
            summary=f"Step '{step}' conf={conf}"
        ))
        lines.append("")

    return "\n".join(lines), sources, 0.90


def _handle_query_budget(
    message: str,
    session_id: str,
) -> Tuple[str, List[SourceReference], float]:
    """Report monthly spending breakdown by category."""
    metrics = monthly_avg_expense(user_id=1, exclude_anomalies=True)
    by_cat  = metrics.get("by_category", {})

    if not by_cat:
        return (
            "Not enough transaction history to compute a category breakdown. "
            "Add at least 2 months of expense data.",
            [], 0.70
        )

    lines = [
        f"## 💰 Monthly Spending Breakdown\n",
        f"**Total Monthly Expense:** ${metrics['total']:,.2f}",
        f"**Essential:** ${metrics['essential']:,.2f} | **Discretionary:** ${metrics['discretionary']:,.2f}\n",
        "| Category | Monthly Avg | Type |",
        "|----------|-------------|------|",
    ]
    sorted_cats = sorted(by_cat.items(), key=lambda x: x[1]["monthly_amount"], reverse=True)
    for cat, data in sorted_cats:
        cat_type = "Essential" if data["is_essential"] else "Discretionary"
        lines.append(f"| {cat} | ${data['monthly_amount']:,.2f} | {cat_type} |")

    return "\n".join(lines), [], 0.90


def _handle_query_context(
    message: str,
    session_id: str,
) -> Tuple[str, List[SourceReference], float]:
    """Return the full copilot state snapshot summary."""
    snap = context_engine.snapshot()
    counts = {k: v for k, v in snap["event_counts"].items() if v > 0}
    ks_status = "🔴 ACTIVE — System halted" if snap["kill_switch_active"] else "🟢 Inactive"

    lines = [
        "## 🗂 Copilot Context Snapshot\n",
        f"**Kill Switch:** {ks_status}\n",
        "**Event Counts per Lane:**",
    ]
    for lane, count in counts.items():
        lines.append(f"  • `{lane}`: {count} event(s)")
    if not counts:
        lines.append("  _No events recorded yet._")

    return "\n".join(lines), [], 0.90


def _handle_kill_switch(
    message: str,
    session_id: str,
) -> Tuple[str, List[SourceReference], float]:
    """Detected kill-switch intent in chat — warn user to use the dedicated endpoint."""
    return (
        "⚠️ **Kill Switch detected in message.**\n\n"
        "To activate the emergency halt, use the dedicated endpoint:\n\n"
        "```\nPOST /api/copilot/kill-switch\n"
        '{"reason": "Your reason here", "session_id": "' + session_id + '"}\n```\n\n'
        "This prevents accidental halts from chat messages.",
        [], 0.80
    )


def _handle_help(_message: str, _session_id: str) -> Tuple[str, List[SourceReference], float]:
    return (
        "## 🤖 Copilot Capabilities\n\n"
        "I can answer questions about your financial data and agent system:\n\n"
        "| Query Type | Example |\n"
        "|------------|---------|\n"
        "| **Anomalies** | _\"Show me flagged transactions\"_ |\n"
        "| **Health Score** | _\"What is my financial health grade?\"_ |\n"
        "| **Goals** | _\"How are my savings goals tracking?\"_ |\n"
        "| **Budget** | _\"Break down my monthly spending\"_ |\n"
        "| **Agent Reasoning** | _\"Show agent chain-of-thought traces\"_ |\n"
        "| **Risk Metrics** | _\"What are my current risk metrics?\"_ |\n"
        "| **Explain Decision** | _\"Why was transaction #42 flagged?\"_ |\n"
        "| **Context Snapshot** | _\"Show current context state\"_ |\n"
        "| **Kill Switch** | → Use `POST /api/copilot/kill-switch` |\n\n"
        "_Ask me anything about your finances or the agent system._",
        [], 1.0
    )


def _handle_unknown(_message: str, _session_id: str) -> Tuple[str, List[SourceReference], float]:
    return (
        "I didn't understand your query. Try asking about:\n"
        "- Your financial health score\n"
        "- Spending anomalies or flagged transactions\n"
        "- Savings goal progress\n"
        "- Agent reasoning traces\n"
        "- Monthly budget breakdown\n\n"
        "Or type **help** to see all capabilities.",
        [], 0.20
    )


# ===========================================================================
# 5. Dispatch Table
# ===========================================================================

_HANDLERS = {
    "explain_decision": _handle_explain_decision,
    "query_anomaly":    _handle_query_anomaly,
    "query_health":     _handle_query_health,
    "query_goal":       _handle_query_goal,
    "query_risk":       _handle_query_risk,
    "query_cot":        _handle_query_cot,
    "query_budget":     _handle_query_budget,
    "query_context":    _handle_query_context,
    "kill_switch":      _handle_kill_switch,
    "help":             _handle_help,
    "unknown":          _handle_unknown,
}


# ===========================================================================
# 6. OpenRouter LLM Integration
# ===========================================================================

def _build_llm_context(session_id: str = "default") -> Tuple[str, List[SourceReference]]:
    """
    Assemble live multi-lane context snapshots and financial health metrics
    into a structured context prompt for the OpenRouter LLM.
    """
    sources: List[SourceReference] = []
    sections: List[str] = []

    # 1. User financial profile & stats
    try:
        user = _fetch_user_profile(user_id=1)
        income = float(user.get("monthly_income") or 0.0)
        risk_prof = user.get("risk_profile", "moderate")
        health = _fetch_health_score(user_id=1)
        score = health.get("overall_score", 0)
        rating = health.get("rating", "N/A")
        sections.append(
            f"### User Profile\n"
            f"- Monthly Income: ${income:,.2f}\n"
            f"- Risk Profile: {risk_prof}\n"
            f"- Financial Health Score: {score}/100 ({rating})"
        )
    except Exception as exc:
        sections.append(f"### User Profile\n- Status: Profile read error ({exc})")

    # 2. Monthly spending & anomalies
    try:
        metrics = monthly_avg_expense(user_id=1, exclude_anomalies=True)
        total_exp = metrics.get("total", 0.0)
        sections.append(f"### Spending Baseline\n- Average Monthly Expense: ${total_exp:,.2f}")
    except Exception:
        pass

    # 3. Context Engine Lanes (CoT, Risk Metrics, Execution Logs, Market Intel)
    lane_lookups = [
        (ContextLane.AGENT_COT, "Recent Agent CoT Traces (Decision Logic)", 4),
        (ContextLane.RISK_METRIC, "Recent Risk Metrics & Anomaly Scans", 4),
        (ContextLane.EXEC_LOG, "Recent System & Sandbox Execution Logs", 4),
        (ContextLane.MARKET_INTEL, "Market Intelligence & Forecasts", 3),
        (ContextLane.RISK_OVERRIDE, "Active Risk Limit Overrides", 3),
    ]

    for lane, header, limit in lane_lookups:
        events = context_engine.query(lane, limit=limit, session_id=session_id)
        if events:
            lane_lines = [f"### {header}"]
            for evt in events:
                evt_id = evt.get("id") or evt.get("event_id")
                payload = evt.get("payload", {})
                ts = evt.get("timestamp", "")
                lane_lines.append(f"- [Event #{evt_id} | {ts}] {payload}")
                sources.append(SourceReference(
                    event_id=evt_id,
                    lane=lane.value,
                    timestamp=ts,
                    summary=str(payload)[:120],
                ))
            sections.append("\n".join(lane_lines))

    return "\n\n".join(sections), sources


def _query_openrouter(
    message: str,
    session_id: str = "default",
    timeout: float = 20.0,
) -> Optional[Tuple[str, List[SourceReference]]]:
    """
    Query OpenRouter using live context snapshot.
    Returns (reply_text, sources) or None if disabled or on error.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None

    model = get_openrouter_model()
    base_url = os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL).strip()
    referer = os.getenv("OPENROUTER_REFERER", OPENROUTER_REFERER).strip()
    title = os.getenv("OPENROUTER_TITLE", OPENROUTER_TITLE).strip()

    context_str, sources = _build_llm_context(session_id=session_id)

    system_prompt = (
        "You are FinAgent Copilot, an elite Autonomous Trading & Personal Financial Assistant.\n"
        "You have full access to real-time system context, agent Chain-of-Thought logs, "
        "risk metrics, anomaly reports, and user finances.\n\n"
        "Guidelines:\n"
        "1. Ground your answers in the user's actual financial data provided below.\n"
        "2. When explaining agent actions, cite specific events, metrics, or Z-scores.\n"
        "3. Provide structured, concise, and clear answers formatted in markdown.\n"
        "4. Maintain strict financial prudence: never make absolute promises like 'guaranteed profit' or 'risk-free'.\n\n"
        f"--- LIVE FINANCIAL & AGENT CONTEXT ---\n{context_str}\n--------------------------------------"
    )

    try:
        from openai import OpenAI
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            default_headers={
                "HTTP-Referer": referer,
                "X-Title": title,
            },
            timeout=timeout,
        )

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            temperature=0.2,
            max_tokens=1000,
        )

        msg_obj = response.choices[0].message
        reply = msg_obj.content
        if not reply and hasattr(msg_obj, "reasoning") and msg_obj.reasoning:
            reply = msg_obj.reasoning
        if reply:
            return reply.strip(), sources
        return None
    except Exception as exc:
        logger.warning(
            "OpenRouter LLM request failed (%s: %s). Falling back to rule-based engine.",
            type(exc).__name__,
            exc,
        )
        return None


# ===========================================================================
# 7. CopilotNLEngine — Public Interface
# ===========================================================================

class CopilotNLEngine:
    """
    Natural-Language Query Engine for the FinAgent Copilot.

    Supports:
    • OpenRouter LLM reasoning when ``OPENROUTER_API_KEY`` is set.
    • Automatic, seamless fallback to the deterministic rule-based engine
      if OpenRouter is unconfigured, unreachable, or errors.
    """

    def process(
        self,
        message: str,
        session_id: str = "default",
    ) -> CopilotChatResponse:
        """
        Full NL processing pipeline:
        1. Record user prompt in context engine.
        2. Attempt OpenRouter LLM inference if configured.
        3. Fall back to rule-based intent classification & handlers on error or when unconfigured.
        4. Record agent CoT trace.
        5. Return structured response.
        """
        # 1 — persist user prompt
        context_engine.record_user_prompt(message, session_id=session_id)

        # 2 — Attempt OpenRouter LLM if enabled
        if is_openrouter_configured():
            llm_result = _query_openrouter(message, session_id=session_id)
            if llm_result is not None:
                reply, sources = llm_result
                model_name = get_openrouter_model()
                model_short = model_name.split("/")[-1]
                # Record agent CoT trace for LLM inference
                context_engine.record_agent_cot(
                    trace={
                        "step": "openrouter_llm_inference",
                        "model": model_name,
                        "query": message[:200],
                        "reply_len": len(reply),
                        "sources_count": len(sources),
                    },
                    session_id=session_id,
                    severity=ContextSeverity.INFO,
                )
                return CopilotChatResponse(
                    reply=reply,
                    intent="openrouter_llm",
                    confidence=0.95,
                    sources=sources,
                    session_id=session_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )

        # 3 — Fallback: deterministic rule-based intent classification & handler dispatch
        intent, confidence = _classify_intent(message)
        handler = _HANDLERS.get(intent, _HANDLERS["unknown"])
        try:
            reply, sources, handler_confidence = handler(message, session_id)
            final_confidence = round((confidence + handler_confidence) / 2, 3)
        except Exception as exc:
            reply            = f"An error occurred while processing your query: {exc}"
            sources          = []
            final_confidence = 0.10

        # 4 — record agent CoT trace for rule-based dispatch
        context_engine.record_agent_cot(
            trace={
                "step":       "nl_query_dispatch",
                "intent":     intent,
                "confidence": final_confidence,
                "query":      message[:200],
                "reply_len":  len(reply),
            },
            session_id=session_id,
            severity=(ContextSeverity.WARNING if final_confidence < 0.40
                      else ContextSeverity.INFO),
        )

        return CopilotChatResponse(
            reply=reply,
            intent=intent,
            confidence=final_confidence,
            sources=sources,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def explain_event(
        self,
        event_id: int,
        session_id: str = "default",
    ) -> Dict[str, Any]:
        """
        Deep-dive explanation for a specific copilot_events row.
        Returns a dict ready to serialise as ExplainResponse.
        """
        evt = _fetch_copilot_event_by_id(event_id)
        ts  = datetime.now(timezone.utc).isoformat()

        if evt is None:
            return {
                "event_id":    event_id,
                "found":       False,
                "explanation": f"Event #{event_id} not found in the copilot_events store.",
                "event":       None,
                "related":     [],
                "timestamp":   ts,
            }

        lane    = evt["lane"]
        payload = evt.get("payload", {})
        sev     = evt.get("severity", "INFO")
        source  = evt.get("source", "system")

        # Build human-readable explanation
        lines = [
            f"**Event #{event_id}** | Lane: `{lane}` | Source: `{source}` | Severity: `{sev}`",
            f"**Timestamp:** {evt.get('timestamp', 'N/A')}",
            "",
            "**Payload details:**",
        ]
        for k, v in payload.items():
            lines.append(f"  • `{k}`: {v}")

        # Lane-specific context
        lane_explanations = {
            "agent_cot":    "This is an agent Chain-of-Thought reasoning trace — it shows the decision logic used by the agent at this moment.",
            "risk_metric":  "This is a risk snapshot — capturing P&L, slippage, or stop-loss levels at the time of recording.",
            "kill_switch":  "⚠️ This is an EMERGENCY KILL SWITCH event — all agent actions were halted at this timestamp.",
            "user_prompt":  "This is a user query recorded by the copilot.",
            "risk_override":"This is a user-defined risk parameter override applied to the system.",
            "exec_log":     "This is a trade or sandbox execution record.",
            "system_alert": "This is a system-level alert generated internally.",
            "market_intel": "This is a market intelligence snapshot (sentiment scores, indicators, order book).",
        }
        if lane in lane_explanations:
            lines.append(f"\n**Context:** {lane_explanations[lane]}")

        related = _fetch_related_events(lane, evt.get("timestamp", ""), window=5)

        return {
            "event_id":    event_id,
            "found":       True,
            "explanation": "\n".join(lines),
            "event":       evt,
            "related":     [r for r in related if r.get("id") != event_id],
            "timestamp":   ts,
        }


# ===========================================================================
# 7. Module-Level Singleton
# ===========================================================================

#: Global NL engine instance — import in copilot router.
nl_engine: CopilotNLEngine = CopilotNLEngine()
