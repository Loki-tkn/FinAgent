"""
guardrails.py – Financial Guardrails & Safety Checks
====================================================
Phase 5: Compliance, Risk Ceilings, and AI Safety Filters.

Enforces financial prudence and regulatory safety rules across all FinAgent
Copilot operations:
  1. Parameter bounds checking for user risk overrides (preventing reckless
     inputs like >30% annual returns, excessive leverage, negative stop-losses).
  2. Mandatory compliance disclaimers on AI reasoning traces and chat outputs.
  3. Hallucination and certainty mitigations (flagging "guaranteed profit" or
     "risk-free" assertions).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.copilot.state import ContextSeverity

logger = logging.getLogger("finagent.copilot.guardrails")

# ===========================================================================
# 1. Mandatory Compliance Disclaimers
# ===========================================================================

STANDARD_FINANCIAL_DISCLAIMER: str = (
    "DISCLAIMER: FinAgent Copilot is an AI decision-support and educational tool. "
    "It does not provide certified financial, investment, legal, or tax advice. "
    "All simulated trajectories, what-if projections, and optimization models "
    "are hypothetical and do not guarantee future financial results."
)

MARKDOWN_FINANCIAL_DISCLAIMER: str = (
    "\n\n---\n*Disclaimer: FinAgent Copilot provides educational analytics and simulations only. "
    "It does not constitute certified investment or financial advice. Market forecasts and "
    "model trajectories are hypothetical and not guaranteed.*"
)


def attach_financial_disclaimer(content: str, markdown: bool = True) -> str:
    """
    Append standard regulatory compliance disclaimer to content if not already present.
    """
    disclaimer = MARKDOWN_FINANCIAL_DISCLAIMER if markdown else f"\n\n{STANDARD_FINANCIAL_DISCLAIMER}"
    if "Disclaimer:" in content or "DISCLAIMER:" in content:
        return content
    return content.strip() + disclaimer


# ===========================================================================
# 2. Risk Parameter Safe Operating Bounds
# ===========================================================================

# Parameter safe bounds: (min_value, max_value, human_label, is_percentage_or_decimal)
SAFE_RISK_BOUNDS: Dict[str, Tuple[float, float, str]] = {
    "expected_return":     (0.0, 0.30, "Expected annual return must be between 0% and 30% (0.0 – 0.30)"),
    "expected_return_pct": (0.0, 30.0, "Expected annual return must be between 0% and 30%"),
    "volatility":          (0.01, 1.0, "Annual volatility must be between 1% and 100% (0.01 – 1.0)"),
    "volatility_pct":      (1.0, 100.0, "Annual volatility must be between 1% and 100%"),
    "inflation":           (0.0, 0.25, "Inflation rate must be between 0% and 25% (0.0 – 0.25)"),
    "inflation_pct":       (0.0, 25.0, "Inflation rate must be between 0% and 25%"),
    "emergency_months":    (1.0, 36.0, "Emergency fund runway must be between 1 and 36 months"),
    "max_drawdown":        (0.01, 0.50, "Maximum drawdown threshold must be between 1% and 50% (0.01 – 0.50)"),
    "max_drawdown_pct":    (1.0, 50.0, "Maximum drawdown percentage must be between 1% and 50%"),
    "stop_loss_pct":       (0.5, 50.0, "Stop loss percentage must be between 0.5% and 50%"),
    "leverage":            (1.0, 3.0, "Maximum trading leverage ceiling is 3.0x"),
    "max_leverage":        (1.0, 3.0, "Maximum trading leverage ceiling is 3.0x"),
    "risk_per_trade_pct":  (0.1, 10.0, "Risk per trade must not exceed 10% of portfolio"),
}


@dataclass
class GuardrailViolation:
    """Record of a single parameter guardrail check failure."""
    field:           str
    attempted_value: Any
    safe_bound:      str
    severity:        ContextSeverity
    message:         str


@dataclass
class GuardrailResult:
    """Comprehensive outcome of running guardrail checks against a payload."""
    passed:            bool
    sanitized_payload: Dict[str, Any]
    violations:        List[GuardrailViolation] = field(default_factory=list)
    warnings:          List[str]                = field(default_factory=list)

    @property
    def has_critical_violations(self) -> bool:
        return any(v.severity == ContextSeverity.CRITICAL for v in self.violations)


def validate_risk_override(
    overrides: Dict[str, Any],
    clamp_out_of_bounds: bool = True,
) -> GuardrailResult:
    """
    Validate user-submitted risk parameter overrides against safety bounds.

    Parameters
    ----------
    overrides : Dict[str, Any]
        Dictionary of proposed risk parameter overrides.
    clamp_out_of_bounds : bool, default True
        If True, numeric values that moderately breach bounds are clamped to
        safe boundary values with a warning; severe/negative breaches are rejected.

    Returns
    -------
    GuardrailResult
        Detailed result containing pass/fail status, sanitized payload,
        violations list, and human-readable warnings.
    """
    sanitized: Dict[str, Any] = dict(overrides)
    violations: List[GuardrailViolation] = []
    warnings: List[str] = []

    for key, val in overrides.items():
        bound = SAFE_RISK_BOUNDS.get(key)
        if bound is None:
            # Check normalized key without trailing _pct or similar
            norm_key = key.lower().replace("-", "_")
            bound = SAFE_RISK_BOUNDS.get(norm_key)

        if bound is None:
            continue

        min_val, max_val, desc = bound

        # Numeric bounds verification
        if isinstance(val, (int, float)):
            num_val = float(val)

            # Check for negative or dangerous nonsense values
            if num_val < min_val:
                msg = f"Parameter '{key}' value {val} is below minimum allowed safe bound {min_val}. {desc}."
                violations.append(GuardrailViolation(
                    field=key,
                    attempted_value=val,
                    safe_bound=f"[{min_val}, {max_val}]",
                    severity=ContextSeverity.CRITICAL if num_val < 0 else ContextSeverity.WARNING,
                    message=msg,
                ))
                if clamp_out_of_bounds:
                    sanitized[key] = min_val
                    warnings.append(f"Clamped '{key}' from {val} up to safe minimum {min_val}.")

            elif num_val > max_val:
                msg = f"Parameter '{key}' value {val} exceeds maximum allowed safe ceiling {max_val}. {desc}."
                severity = ContextSeverity.CRITICAL if num_val > max_val * 2.0 else ContextSeverity.WARNING
                violations.append(GuardrailViolation(
                    field=key,
                    attempted_value=val,
                    safe_bound=f"[{min_val}, {max_val}]",
                    severity=severity,
                    message=msg,
                ))
                if clamp_out_of_bounds:
                    sanitized[key] = max_val
                    warnings.append(f"Clamped '{key}' from {val} down to safe ceiling {max_val}.")

    passed = not any(v.severity == ContextSeverity.CRITICAL for v in violations)
    return GuardrailResult(
        passed=passed,
        sanitized_payload=sanitized,
        violations=violations,
        warnings=warnings,
    )


# ===========================================================================
# 3. AI Output Safety & Hallucination Mitigations
# ===========================================================================

UNSAFE_PROMISES: List[Tuple[str, str]] = [
    ("guaranteed profit", "projected potential returns"),
    ("guaranteed return", "estimated return scenario"),
    ("risk-free", "risk-mitigated"),
    ("100% safe", "statistically modeled"),
    ("zero risk", "low evaluated risk"),
    ("sure thing", "favorable historical probability"),
]


def sanitize_ai_reasoning(text: str) -> str:
    """
    Filter out unsafe guarantees or absolutist investment promises from AI reasoning,
    substituting realistic probabilistic phrasing.
    """
    sanitized = text
    for bad_phrase, replacement in UNSAFE_PROMISES:
        if bad_phrase in sanitized.lower():
            # Case-insensitive replacement preserving general phrasing
            import re
            pattern = re.compile(re.escape(bad_phrase), re.IGNORECASE)
            sanitized = pattern.sub(replacement, sanitized)
            logger.warning("Guardrail sanitized unsafe phrase '%s' → '%s'", bad_phrase, replacement)

    return sanitized
