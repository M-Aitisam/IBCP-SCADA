# packages/backend/app/intelligence/alerts.py
"""Phase 18 — early-warning rule engine.

Decides when a hazard score becomes an alert. Pure rule logic here; the
persistence half lives in `analytics_service`.

The hard problem in an alerting system is not detecting a bad value — it is not
crying wolf. Four mechanisms address that, and each exists because of a
specific failure mode:

  persistence   a level must hold for N cycles before it alerts, so one noisy
                composite cannot escalate a district
  hysteresis    de-escalating needs a larger move than escalating, so a score
                sitting on a threshold does not flap up and down nightly
  cooldown      a resolved alert cannot immediately re-raise for the same thing
  deduplication an ongoing hazard updates its existing alert rather than
                emitting a new one every night

Without these, a three-month drought would produce ninety alerts and the
operator would stop reading them — which is worse than having no alerts.

These are satellite-derived indicators, never official warnings. The system has
no authority to issue one and says so on every alert it emits.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from app.intelligence.hazards import (
    HAZARD_CROP,
    HAZARD_DROUGHT,
    HAZARD_FLOOD,
    HAZARD_HEAT,
    HAZARD_MULTI,
    HazardResult,
    LEVEL_EXTREME,
    LEVEL_MODERATE,
    LEVEL_SEVERE,
    LEVEL_WATCH,
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_MEDIUM,
)

ALERT_VERSION = "a1"

# --- severities (Phase 18 vocabulary) ------------------------------------
SEVERITY_WATCH = "WATCH"
SEVERITY_ADVISORY = "ADVISORY"
SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"

SEVERITY_RANK = {
    SEVERITY_WATCH: 1,
    SEVERITY_ADVISORY: 2,
    SEVERITY_WARNING: 3,
    SEVERITY_CRITICAL: 4,
}

STATUS_ACTIVE = "active"
STATUS_RESOLVED = "resolved"
STATUS_EXPIRED = "expired"

# Hazard level -> alert severity. Kept as an explicit table rather than an
# index calculation so a change to hazard bands cannot silently shift what
# severity a district is alerted at.
LEVEL_TO_SEVERITY: dict[str, str] = {
    LEVEL_WATCH: SEVERITY_WATCH,
    LEVEL_MODERATE: SEVERITY_ADVISORY,
    LEVEL_SEVERE: SEVERITY_WARNING,
    LEVEL_EXTREME: SEVERITY_CRITICAL,
    RISK_MEDIUM: SEVERITY_WATCH,
    RISK_HIGH: SEVERITY_WARNING,
    RISK_CRITICAL: SEVERITY_CRITICAL,
}

# Cycles a level must hold before it alerts. Flood is 1 because flood onset is
# genuinely sudden and waiting a second cycle could mean waiting a day too
# long; the slow-onset hazards can afford confirmation.
PERSISTENCE_REQUIRED: dict[str, int] = {
    HAZARD_DROUGHT: 2,
    HAZARD_CROP: 2,
    HAZARD_HEAT: 2,
    HAZARD_FLOOD: 1,
    HAZARD_MULTI: 2,
}

# Score must fall this far BELOW the band boundary before an alert steps down.
# Asymmetric on purpose: escalate readily, de-escalate reluctantly.
HYSTERESIS_MARGIN = 8.0

# Days a resolved alert blocks a new one for the same region and hazard.
COOLDOWN_DAYS = 3

# An alert nobody refreshes goes stale rather than lingering as "active"
# forever — a dashboard full of month-old alerts is a dashboard nobody trusts.
DEFAULT_TTL_DAYS = 7

# Below this confidence a score is reported but never alerted on. Alerting from
# a two-component score with a three-year baseline would be noise wearing the
# costume of a warning.
MIN_ALERT_CONFIDENCE = 0.35


@dataclass
class AlertDecision:
    """What the engine wants to happen for one region/hazard this cycle."""

    action: str  # "raise" | "escalate" | "update" | "deescalate" | "none"
    hazard: str
    region_id: str
    severity: Optional[str] = None
    previous_severity: Optional[str] = None
    reason: str = ""
    score: Optional[float] = None
    confidence: Optional[float] = None
    evidence: dict[str, Any] = field(default_factory=dict)
    rule_id: Optional[str] = None
    suppressed_by: Optional[str] = None

    @property
    def should_write(self) -> bool:
        return self.action in ("raise", "escalate", "update", "deescalate")

    def to_json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "hazard": self.hazard,
            "region_id": self.region_id,
            "severity": self.severity,
            "previous_severity": self.previous_severity,
            "reason": self.reason,
            "score": self.score,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "rule_id": self.rule_id,
            "suppressed_by": self.suppressed_by,
        }


def dedupe_key(hazard: str, region_id: str, severity: str) -> str:
    """Identity of an ongoing situation.

    Region and hazard and severity band — not the score, and not the date. Two
    consecutive nights of SEVERE drought in the same district are one
    situation, and must map to one alert.
    """
    raw = f"{hazard}|{region_id}|{severity}"
    return raw[:160]


def build_alert_id(hazard: str, region_id: str, first_detected: date) -> str:
    """Stable, readable identifier: GV-DROUGHT-20260825-a1b2c3."""
    digest = hashlib.sha256(
        f"{hazard}|{region_id}|{first_detected.isoformat()}".encode("utf-8")
    ).hexdigest()[:6]
    return f"GV-{hazard.upper().replace('_', '')}-{first_detected:%Y%m%d}-{digest}"


def severity_for(level: str) -> Optional[str]:
    return LEVEL_TO_SEVERITY.get(level)


def evaluate(
    result: HazardResult,
    *,
    region_id: str,
    reference_date: date,
    consecutive_periods: int = 1,
    existing_severity: Optional[str] = None,
    existing_status: Optional[str] = None,
    last_resolved_on: Optional[date] = None,
) -> AlertDecision:
    """Decide this cycle's action for one region and hazard.

    `consecutive_periods` is how many cycles the hazard has held its current
    level, supplied by the caller from the stored hazard-score history.
    """
    hazard = result.hazard

    # --- nothing scorable ---
    if result.score is None or result.status != "ok":
        return AlertDecision(
            action="none", hazard=hazard, region_id=region_id,
            reason="hazard not scored this cycle", suppressed_by="insufficient_data",
        )

    severity = severity_for(result.level)

    # --- below the alerting floor ---
    if severity is None:
        if existing_severity and existing_status == STATUS_ACTIVE:
            return AlertDecision(
                action="deescalate", hazard=hazard, region_id=region_id,
                severity=None, previous_severity=existing_severity,
                score=result.score, confidence=result.confidence,
                reason=(
                    f"{hazard} returned to normal "
                    f"(score {result.score:.0f}); alert closed."
                ),
                rule_id=f"{hazard}.return_to_normal",
            )
        return AlertDecision(
            action="none", hazard=hazard, region_id=region_id,
            score=result.score, reason="below alerting threshold",
        )

    # --- confidence floor ---
    if result.confidence is not None and result.confidence < MIN_ALERT_CONFIDENCE:
        return AlertDecision(
            action="none", hazard=hazard, region_id=region_id,
            severity=severity, score=result.score, confidence=result.confidence,
            reason=(
                f"confidence {result.confidence:.2f} below the "
                f"{MIN_ALERT_CONFIDENCE:.2f} alerting floor"
            ),
            suppressed_by="low_confidence",
        )

    evidence = {
        "score": round(result.score, 1),
        "level": result.level,
        "confidence": result.confidence,
        "primary_driver": result.primary_driver,
        "contributors": [c.to_json() for c in result.contributors],
        "consecutive_periods": consecutive_periods,
    }

    # --- already alerting at this severity: refresh, do not re-raise ---
    if existing_severity == severity and existing_status == STATUS_ACTIVE:
        return AlertDecision(
            action="update", hazard=hazard, region_id=region_id,
            severity=severity, previous_severity=existing_severity,
            score=result.score, confidence=result.confidence, evidence=evidence,
            reason=result.reason, rule_id=f"{hazard}.ongoing",
        )

    escalating = (
        existing_severity is None
        or SEVERITY_RANK[severity] > SEVERITY_RANK.get(existing_severity, 0)
    )

    # --- persistence gate, escalation only ---
    required = PERSISTENCE_REQUIRED.get(hazard, 2)
    if escalating and consecutive_periods < required:
        return AlertDecision(
            action="none", hazard=hazard, region_id=region_id,
            severity=severity, score=result.score, confidence=result.confidence,
            reason=(
                f"{result.level} held for {consecutive_periods} of the "
                f"{required} cycles required before alerting"
            ),
            suppressed_by="persistence",
        )

    # --- cooldown after a recent resolve ---
    if (
        escalating
        and last_resolved_on is not None
        and (reference_date - last_resolved_on).days < COOLDOWN_DAYS
    ):
        return AlertDecision(
            action="none", hazard=hazard, region_id=region_id,
            severity=severity, score=result.score, confidence=result.confidence,
            reason=(
                f"a {hazard} alert for this region resolved "
                f"{(reference_date - last_resolved_on).days} day(s) ago; "
                f"{COOLDOWN_DAYS}-day cooldown applies"
            ),
            suppressed_by="cooldown",
        )

    if existing_severity is None:
        action = "raise"
        rule = f"{hazard}.threshold_crossed"
    elif escalating:
        action = "escalate"
        rule = f"{hazard}.escalation"
    else:
        action = "deescalate"
        rule = f"{hazard}.deescalation"

    return AlertDecision(
        action=action, hazard=hazard, region_id=region_id,
        severity=severity, previous_severity=existing_severity,
        score=result.score, confidence=result.confidence, evidence=evidence,
        reason=result.reason, rule_id=rule,
    )


def should_deescalate(
    current_score: float, band_floor: float, margin: float = HYSTERESIS_MARGIN
) -> bool:
    """Hysteresis test for stepping an alert down.

    A score must fall `margin` points clear of the band boundary, not merely
    cross it. Without this, a score oscillating around a threshold flips the
    alert every cycle and the history becomes unreadable.
    """
    return current_score < (band_floor - margin)


def expiry_for(reference_date: date, ttl_days: int = DEFAULT_TTL_DAYS) -> date:
    return reference_date + timedelta(days=ttl_days)


DISCLAIMER = (
    "Satellite-derived analytical indicator produced by this system. Not an "
    "official disaster warning and carries no authority from NDMA, PDMA or any "
    "government body."
)
