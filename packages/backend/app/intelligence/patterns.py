# packages/backend/app/intelligence/patterns.py
"""Spatial hotspots and post-event recovery — deterministic pattern analysis.

Two things that only become visible when you look across regions or across
time rather than at one region on one day:

**Hotspots (§12).** Seven adjacent districts in severe vegetation stress is a
regional emergency. Seven scattered ones are seven local problems with probably
different causes. A per-region table cannot express that difference; a
contiguity-aware clustering can.

**Recovery (§23).** A hazard ending is not the same as a region recovering.
Tracking the indicator back toward its pre-event level is how you tell a region
that bounced back from one that is still degraded months later — and it is the
only part of the system that says anything about consequences rather than
conditions.

Both are plain rules over stored numbers. No clustering model, no ML: the
"clustering" is breadth-first search over a precomputed adjacency graph, which
is deterministic, explainable and reproducible.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from app.intelligence.geo import connected_components

HOTSPOT_VERSION = "hs1"
RECOVERY_VERSION = "rc1"

# ---------------------------------------------------------------------------
# Hotspots
# ---------------------------------------------------------------------------

# A single stressed district is not a hotspot; it is a stressed district. Three
# contiguous ones is the smallest group that suggests a shared regional driver
# rather than local variation.
MIN_CLUSTER_SIZE = 3

# Score at or above which a region is eligible to join a cluster. Matches the
# hazard engine's MODERATE band, so "in a hotspot" and "moderately affected"
# mean the same thing rather than two different thresholds.
CLUSTER_SCORE_THRESHOLD = 40.0


@dataclass
class Hotspot:
    cluster_id: str
    hazard_type: str
    region_ids: list[str]
    cluster_size: int
    average_severity: float
    maximum_severity: float
    dominant_level: Optional[str]
    provinces: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "hazard_type": self.hazard_type,
            "region_ids": self.region_ids,
            "cluster_size": self.cluster_size,
            "average_severity": round(self.average_severity, 1),
            "maximum_severity": round(self.maximum_severity, 1),
            "dominant_level": self.dominant_level,
            "provinces": self.provinces,
            "calculation_version": HOTSPOT_VERSION,
        }


def build_cluster_id(hazard_type: str, region_ids: list[str]) -> str:
    """Stable identity from the member set.

    Hashing the sorted members means the same group of districts yields the
    same id on every cycle, so a persisting hotspot updates its own row instead
    of creating a new one each night. A hotspot that grows legitimately becomes
    a new cluster — which is correct, because a different set of districts is a
    different spatial pattern, and `growth_regions` records the relationship.
    """
    digest = hashlib.sha256(
        f"{hazard_type}|{'|'.join(sorted(region_ids))}".encode("utf-8")
    ).hexdigest()[:10]
    return f"{hazard_type.upper().replace('_', '')}-CL-{digest}"


def detect_hotspots(
    *,
    hazard_type: str,
    scores: dict[str, float],
    levels: Optional[dict[str, str]] = None,
    adjacency: dict[str, list[str]],
    provinces: Optional[dict[str, Optional[str]]] = None,
    threshold: float = CLUSTER_SCORE_THRESHOLD,
    min_size: int = MIN_CLUSTER_SIZE,
) -> list[Hotspot]:
    """Find contiguous groups of affected regions.

    `scores` holds only regions that were actually scored — a region with no
    score is absent rather than zero, so it neither joins a cluster nor breaks
    one in a way that would imply it had been assessed and found healthy.
    """
    levels = levels or {}
    provinces = provinces or {}

    affected = [rid for rid, score in scores.items() if score is not None and score >= threshold]
    if len(affected) < min_size:
        return []

    hotspots: list[Hotspot] = []
    for component in connected_components(affected, adjacency):
        if len(component) < min_size:
            continue

        member_scores = [scores[rid] for rid in component]
        member_levels = [levels.get(rid) for rid in component if levels.get(rid)]
        dominant = (
            max(set(member_levels), key=member_levels.count) if member_levels else None
        )
        member_provinces = sorted(
            {provinces.get(rid) for rid in component if provinces.get(rid)}
        )

        hotspots.append(
            Hotspot(
                cluster_id=build_cluster_id(hazard_type, component),
                hazard_type=hazard_type,
                region_ids=component,
                cluster_size=len(component),
                average_severity=sum(member_scores) / len(member_scores),
                maximum_severity=max(member_scores),
                dominant_level=dominant,
                provinces=member_provinces,
            )
        )

    return hotspots


def hotspot_growth(
    current: Hotspot, previous_members: Optional[set[str]]
) -> tuple[int, Optional[float]]:
    """(regions added, growth rate) against the previous cycle.

    A spreading hotspot is the operational signal; a stable one is background.
    """
    if not previous_members:
        return 0, None
    added = len(set(current.region_ids) - previous_members)
    rate = added / len(previous_members) if previous_members else None
    return added, round(rate, 3) if rate is not None else None


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

RECOVERING = "RECOVERING"
SLOW_RECOVERY = "SLOW_RECOVERY"
STALLED = "STALLED"
RECOVERED = "RECOVERED"
UNKNOWN = "UNKNOWN"

# At or above this fraction of the pre-event level, the indicator has returned.
RECOVERED_THRESHOLD = 90.0
# Recovery considered under way rather than stalled.
RECOVERING_THRESHOLD = 60.0
SLOW_THRESHOLD = 25.0


@dataclass
class RecoveryAssessment:
    region_id: str
    metric: str
    baseline_value: Optional[float]
    pre_event_value: Optional[float]
    minimum_value: Optional[float]
    current_value: Optional[float]
    recovery_pct: Optional[float]
    days_since_event: Optional[int]
    status: str
    trend: Optional[str]
    reason: str
    version: str = RECOVERY_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "metric": self.metric,
            "baseline_value": self.baseline_value,
            "pre_event_value": self.pre_event_value,
            "minimum_value": self.minimum_value,
            "current_value": self.current_value,
            "recovery_pct": self.recovery_pct,
            "days_since_event": self.days_since_event,
            "recovery_status": self.status,
            "trend": self.trend,
            "reason": self.reason,
            "calculation_version": self.version,
            # Restated on every row: this is a signal returning, not a
            # livelihood restored.
            "interpretation": "satellite-derived recovery indicator",
        }


def assess_recovery(
    *,
    region_id: str,
    metric: str,
    pre_event_value: Optional[float],
    minimum_value: Optional[float],
    current_value: Optional[float],
    baseline_value: Optional[float] = None,
    days_since_event: Optional[int] = None,
    previous_recovery_pct: Optional[float] = None,
) -> RecoveryAssessment:
    """How far an indicator has returned toward its pre-event level.

        recovery% = (current - minimum) / (pre_event - minimum) × 100

    Measured from the event's low point rather than from zero: an NDVI that
    fell 0.48 → 0.29 and has climbed to 0.36 has recovered 37% of what it lost,
    not 75% of some absolute scale. The denominator is the loss, which is the
    only quantity the question is actually about.

    **This is a satellite-derived recovery indicator, not ecological or
    economic recovery.** NDVI returning to normal says the vegetation signal
    returned. It says nothing about whether a crop was replanted or a farmer
    was compensated, and the wording never implies otherwise.
    """
    def unknown(reason: str) -> RecoveryAssessment:
        return RecoveryAssessment(
            region_id=region_id, metric=metric, baseline_value=baseline_value,
            pre_event_value=pre_event_value, minimum_value=minimum_value,
            current_value=current_value, recovery_pct=None,
            days_since_event=days_since_event, status=UNKNOWN, trend=None,
            reason=reason,
        )

    if pre_event_value is None:
        return unknown("no pre-event observation for this region and metric")
    if minimum_value is None or current_value is None:
        return unknown("insufficient observations during or after the event")

    loss = pre_event_value - minimum_value
    if abs(loss) < 1e-9:
        return unknown(
            "the indicator did not measurably decline during the event, so "
            "there is no loss to recover from"
        )

    recovered = (current_value - minimum_value) / loss * 100.0
    # Clamped: an indicator that overshoots its pre-event level has recovered,
    # not 140% recovered, and a negative value means it fell further rather
    # than "un-recovering".
    recovery_pct = round(max(0.0, min(100.0, recovered)), 1)

    if recovery_pct >= RECOVERED_THRESHOLD:
        status = RECOVERED
    elif recovery_pct >= RECOVERING_THRESHOLD:
        status = RECOVERING
    elif recovery_pct >= SLOW_THRESHOLD:
        status = SLOW_RECOVERY
    else:
        status = STALLED

    trend = None
    if previous_recovery_pct is not None:
        delta = recovery_pct - previous_recovery_pct
        trend = "improving" if delta > 2 else "declining" if delta < -2 else "stable"

    day_text = f" {days_since_event} days after the event" if days_since_event else ""
    return RecoveryAssessment(
        region_id=region_id, metric=metric, baseline_value=baseline_value,
        pre_event_value=pre_event_value, minimum_value=minimum_value,
        current_value=current_value, recovery_pct=recovery_pct,
        days_since_event=days_since_event, status=status, trend=trend,
        reason=(
            f"{metric} fell from {pre_event_value:.3f} to {minimum_value:.3f} and "
            f"now reads {current_value:.3f} — {recovery_pct:.0f}% of the decline "
            f"recovered{day_text}"
        ),
    )
