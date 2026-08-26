# packages/backend/app/intelligence/events.py
"""Deterministic hazard event lifecycle.

The idea this module exists to enforce: **an observation is not an event.**

A flood lasting nine days produces dozens of observations across several
datasets. Treating each as its own event hands an operator a list of dozens of
floods for one flood — which is how alert fatigue starts, and how a real
emergency gets lost among its own duplicates.

An event is created once, updated as observations arrive, and resolved when
conditions normalise. Its lifecycle:

    DETECTED -> CONFIRMED -> ESCALATING -> PEAK -> DECLINING -> RESOLVED

Transitions are rule-based over stored scores. No model, no training, no
inference. Every transition records why it fired, and the rule version is
stored on the event so a classification stays interpretable after the rules
change.

Design note for the future ML phase: this module takes a score and a history
and returns a decision. Where that score comes from — a deterministic
composite today, a model later — is not its concern. Swapping in a predicted
score requires no change here, which is the extension point the brief asks for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

EVENT_VERSION = "e1"

# --- lifecycle states -----------------------------------------------------
DETECTED = "DETECTED"
CONFIRMED = "CONFIRMED"
ESCALATING = "ESCALATING"
PEAK = "PEAK"
DECLINING = "DECLINING"
RESOLVED = "RESOLVED"

OPEN_STATES = (DETECTED, CONFIRMED, ESCALATING, PEAK, DECLINING)

# --- hazard types (mirrors app.intelligence.hazards) ----------------------
FLOOD = "flood"
DROUGHT = "drought"
HEAT = "heat_stress"
CROP = "crop_stress"
MULTI = "multi_hazard"

# ---------------------------------------------------------------------------
# Thresholds — configurable per hazard (§8 "make thresholds configurable")
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventRules:
    """Deterministic rule set for one hazard's lifecycle.

    Stated as data rather than buried in branches so the thresholds can be
    tuned, documented and tested without touching the transition logic.
    """

    # A score at or above this opens an event.
    onset_score: float = 40.0
    # And must fall below this to close one. The gap between the two is
    # hysteresis: without it a score hovering at the boundary would open and
    # close an event every cycle, producing a useless event history.
    recovery_score: float = 30.0

    # Cycles at or above onset before DETECTED becomes CONFIRMED. A single
    # noisy composite should not confirm a regional emergency.
    confirm_after_periods: int = 2
    # Cycles below recovery_score before an event resolves. Floods recede and
    # return; closing on the first quiet observation would fragment one event
    # into several.
    resolve_after_periods: int = 2

    # Points per cycle that count as genuinely escalating or declining.
    # Anything smaller is scoring noise, and a lifecycle that flips on noise
    # tells an operator nothing.
    escalation_delta: float = 5.0
    decline_delta: float = 5.0
    # Within this margin of the highest score so far, the event is at PEAK.
    peak_margin: float = 3.0

    # Below this data quality the event is still tracked but never confirmed:
    # acting on a poorly-observed signal is how false emergencies happen.
    min_quality_to_confirm: float = 40.0


# Per-hazard tuning. Flood confirms in one cycle because flood onset is
# genuinely sudden and a second cycle could mean a day too late; the
# slow-onset hazards can afford confirmation. Drought resolves slowly because
# a single wet week does not end a drought.
RULES: dict[str, EventRules] = {
    FLOOD: EventRules(
        onset_score=35.0,
        recovery_score=25.0,
        confirm_after_periods=1,
        resolve_after_periods=2,
        escalation_delta=8.0,
        decline_delta=8.0,
    ),
    DROUGHT: EventRules(
        onset_score=40.0,
        recovery_score=28.0,
        confirm_after_periods=2,
        resolve_after_periods=3,
    ),
    HEAT: EventRules(
        onset_score=45.0,
        recovery_score=32.0,
        confirm_after_periods=2,
        resolve_after_periods=2,
    ),
    CROP: EventRules(
        onset_score=45.0,
        recovery_score=32.0,
        confirm_after_periods=2,
        resolve_after_periods=3,
    ),
    MULTI: EventRules(
        onset_score=50.0,
        recovery_score=35.0,
        confirm_after_periods=2,
        resolve_after_periods=2,
    ),
}

DEFAULT_RULES = EventRules()


def rules_for(hazard: str) -> EventRules:
    return RULES.get(hazard, DEFAULT_RULES)


# ---------------------------------------------------------------------------
# State carried between cycles
# ---------------------------------------------------------------------------


@dataclass
class EventState:
    """An open event's stored state, as the engine needs to see it."""

    event_id: str
    hazard_type: str
    region_id: str
    status: str
    severity: str
    current_score: Optional[float]
    peak_score: Optional[float]
    onset_score: Optional[float]
    first_detected_at: date
    last_updated_at: date
    peak_at: Optional[date] = None
    consecutive_periods: int = 1
    observation_count: int = 1
    affected_area_km2: Optional[float] = None
    peak_area_km2: Optional[float] = None
    # Cycles spent below the recovery threshold, tracked toward resolution.
    below_recovery_periods: int = 0


@dataclass
class EventDecision:
    """What should happen to an event this cycle."""

    action: str  # "open" | "update" | "resolve" | "none"
    hazard_type: str
    region_id: str
    status: str
    severity: str
    score: Optional[float] = None
    previous_status: Optional[str] = None
    transition: Optional[str] = None
    reason: str = ""
    change_from_previous: Optional[float] = None
    is_peak: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)
    suppressed_by: Optional[str] = None
    version: str = EVENT_VERSION

    @property
    def should_write(self) -> bool:
        return self.action in ("open", "update", "resolve")

    def to_json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "hazard_type": self.hazard_type,
            "region_id": self.region_id,
            "status": self.status,
            "severity": self.severity,
            "score": self.score,
            "previous_status": self.previous_status,
            "transition": self.transition,
            "reason": self.reason,
            "change_from_previous": self.change_from_previous,
            "is_peak": self.is_peak,
            "evidence": self.evidence,
            "suppressed_by": self.suppressed_by,
            "calculation_version": self.version,
        }


def build_event_id(hazard_type: str, reference: date, sequence: int) -> str:
    """Readable, sortable identity: FLOOD-2026-0007.

    Deliberately human-facing. An operator has to say this out loud on a call,
    and a UUID cannot be read down a phone line.
    """
    return f"{hazard_type.upper().replace('_', '')}-{reference.year}-{sequence:04d}"


# ---------------------------------------------------------------------------
# The transition rules
# ---------------------------------------------------------------------------


def evaluate(
    *,
    hazard_type: str,
    region_id: str,
    score: Optional[float],
    severity: str,
    reference_date: date,
    existing: Optional[EventState] = None,
    data_quality: Optional[float] = None,
    affected_area_km2: Optional[float] = None,
    hazard_status: str = "ok",
    rules: Optional[EventRules] = None,
) -> EventDecision:
    """Decide this cycle's action for one hazard in one region.

    `existing` is the open event for this (hazard, region) if there is one —
    which is the whole deduplication mechanism: one open event per pair, so a
    second detection updates rather than duplicates.
    """
    rules = rules or rules_for(hazard_type)

    # --- nothing scorable: never open, never close on absence -------------
    #
    # A gap in observation is not evidence a flood ended. Closing an event
    # because the satellite did not see the region would be actively
    # dangerous, so an unscored cycle leaves an open event exactly as it was.
    if score is None or hazard_status != "ok":
        if existing is not None:
            return EventDecision(
                action="none",
                hazard_type=hazard_type,
                region_id=region_id,
                status=existing.status,
                severity=existing.severity,
                reason=(
                    "no scorable observation this cycle; open event left "
                    "unchanged rather than resolved on missing data"
                ),
                suppressed_by="insufficient_data",
            )
        return EventDecision(
            action="none",
            hazard_type=hazard_type,
            region_id=region_id,
            status="NONE",
            severity=severity,
            reason="hazard not scored this cycle",
            suppressed_by="insufficient_data",
        )

    # --- no open event: should one open? ----------------------------------
    if existing is None:
        if score < rules.onset_score:
            return EventDecision(
                action="none",
                hazard_type=hazard_type,
                region_id=region_id,
                status="NONE",
                severity=severity,
                score=score,
                reason=(
                    f"score {score:.0f} below the {rules.onset_score:.0f} "
                    f"onset threshold for {hazard_type}"
                ),
            )

        return EventDecision(
            action="open",
            hazard_type=hazard_type,
            region_id=region_id,
            status=DETECTED,
            severity=severity,
            score=score,
            transition=f"NONE->{DETECTED}",
            reason=(
                f"{hazard_type} score {score:.0f} crossed the "
                f"{rules.onset_score:.0f} onset threshold"
            ),
            evidence={
                "onset_score": rules.onset_score,
                "score": score,
                "severity": severity,
                "data_quality": data_quality,
                "affected_area_km2": affected_area_km2,
            },
        )

    # --- an event is open: how has it moved? ------------------------------
    previous_score = existing.current_score
    delta = None if previous_score is None else score - previous_score
    peak = existing.peak_score if existing.peak_score is not None else score
    is_new_peak = score >= peak

    # Resolution: sustained recovery, never a single quiet cycle.
    if score < rules.recovery_score:
        below = existing.below_recovery_periods + 1
        if below >= rules.resolve_after_periods:
            return EventDecision(
                action="resolve",
                hazard_type=hazard_type,
                region_id=region_id,
                status=RESOLVED,
                severity=severity,
                score=score,
                previous_status=existing.status,
                transition=f"{existing.status}->{RESOLVED}",
                change_from_previous=delta,
                reason=(
                    f"score {score:.0f} held below the "
                    f"{rules.recovery_score:.0f} recovery threshold for "
                    f"{below} consecutive cycles"
                ),
                evidence={
                    "below_recovery_periods": below,
                    "recovery_score": rules.recovery_score,
                    "peak_score": peak,
                    "duration_days": (reference_date - existing.first_detected_at).days,
                },
            )
        return EventDecision(
            action="update",
            hazard_type=hazard_type,
            region_id=region_id,
            status=DECLINING,
            severity=severity,
            score=score,
            previous_status=existing.status,
            transition=(
                f"{existing.status}->{DECLINING}"
                if existing.status != DECLINING
                else None
            ),
            change_from_previous=delta,
            reason=(
                f"score {score:.0f} below recovery threshold for {below} of "
                f"the {rules.resolve_after_periods} cycles needed to resolve"
            ),
            evidence={"below_recovery_periods": below},
        )

    # Still active. Which of the open states applies?
    status, transition, reason = _active_status(
        existing=existing,
        score=score,
        delta=delta,
        peak=peak,
        is_new_peak=is_new_peak,
        rules=rules,
        data_quality=data_quality,
    )

    return EventDecision(
        action="update",
        hazard_type=hazard_type,
        region_id=region_id,
        status=status,
        severity=severity,
        score=score,
        previous_status=existing.status,
        transition=transition,
        change_from_previous=delta,
        is_peak=is_new_peak,
        reason=reason,
        evidence={
            "score": score,
            "previous_score": previous_score,
            "delta": delta,
            "peak_score": max(peak, score),
            "data_quality": data_quality,
            "affected_area_km2": affected_area_km2,
            "duration_days": (reference_date - existing.first_detected_at).days,
        },
    )


def _active_status(
    *,
    existing: EventState,
    score: float,
    delta: Optional[float],
    peak: float,
    is_new_peak: bool,
    rules: EventRules,
    data_quality: Optional[float],
) -> tuple[str, Optional[str], str]:
    """Which open state an active event is in, and why."""
    current = existing.status

    # Escalation takes priority over everything: a rapidly worsening event is
    # the most operationally urgent thing the lifecycle can express.
    if delta is not None and delta >= rules.escalation_delta:
        return (
            ESCALATING,
            f"{current}->{ESCALATING}" if current != ESCALATING else None,
            f"score rose {delta:+.0f} points, at or above the "
            f"{rules.escalation_delta:.0f}-point escalation threshold",
        )

    # Confirmation is resolved BEFORE the peak check.
    #
    # A DETECTED event whose score has not moved sits at its own peak
    # trivially — peak equals current on the first cycle — so testing for PEAK
    # first made DETECTED->CONFIRMED unreachable for any steady hazard, which
    # is the common case. PEAK only means something for an event that is
    # already established.
    if current == DETECTED:
        periods = existing.consecutive_periods + 1
        quality_ok = (
            data_quality is None or data_quality >= rules.min_quality_to_confirm
        )
        if not quality_ok:
            return (
                DETECTED,
                None,
                f"held at DETECTED: data quality {data_quality:.0f} is below "
                f"the {rules.min_quality_to_confirm:.0f} needed to confirm",
            )
        if periods >= rules.confirm_after_periods:
            return (
                CONFIRMED,
                f"{DETECTED}->{CONFIRMED}",
                f"hazard persisted for {periods} cycles at or above the onset "
                f"threshold",
            )
        return (
            DETECTED,
            None,
            f"persisted {periods} of the {rules.confirm_after_periods} cycles "
            f"needed to confirm",
        )

    # A new high, or within a small margin of one.
    if is_new_peak or (peak - score) <= rules.peak_margin:
        return (
            PEAK,
            f"{current}->{PEAK}" if current != PEAK else None,
            f"score {score:.0f} is at or within {rules.peak_margin:.0f} points "
            f"of this event's highest ({peak:.0f})",
        )

    if delta is not None and delta <= -rules.decline_delta:
        return (
            DECLINING,
            f"{current}->{DECLINING}" if current != DECLINING else None,
            f"score fell {delta:+.0f} points from the previous cycle",
        )

    # Anything else holds its current state.
    return (
        current,
        None,
        f"score {score:.0f} stable; {current.lower()} state maintained",
    )


# ---------------------------------------------------------------------------
# Before / during / after (§22)
# ---------------------------------------------------------------------------


def impact_window(
    first_detected: date, resolved_at: Optional[date], window_days: int = 30
) -> dict[str, tuple[date, date]]:
    """The three comparison windows for an event's impact analysis.

    `before` ends the day before onset, so it cannot contain the event itself —
    a "before" window overlapping the flood would understate the impact by
    comparing the flood against itself.
    """
    before_end = first_detected - timedelta(days=1)
    before_start = before_end - timedelta(days=window_days - 1)

    during_end = resolved_at or (first_detected + timedelta(days=window_days - 1))
    after_start = (resolved_at or during_end) + timedelta(days=1)
    after_end = after_start + timedelta(days=window_days - 1)

    return {
        "before": (before_start, before_end),
        "during": (first_detected, during_end),
        "after": (after_start, after_end),
    }
