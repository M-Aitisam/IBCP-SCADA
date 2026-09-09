# packages/backend/app/api/v1/endpoints/events.py
"""Hazard events, hotspots, replay, recovery and field verification.

Read endpoints need a signed-in user; anything that changes state needs the
operator role and is written to both the audit log and the alert/event history.

Nothing here computes a hazard at request time. The analytics cascade decides,
versioned and auditable; these endpoints report what it decided. Recomputing
per request would let the number on screen differ from the number that opened
an event.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_operator
from app.databases.timestampdb.events import (
    AlertHistory,
    EventObservation,
    FieldReport,
    HazardEvent,
    HazardHotspot,
    RecoveryMetric,
    RegionGeometryStats,
)
from app.databases.timestampdb.intelligence import Alert, AuditLog, HazardScore
from app.databases.timestampdb.models import SatelliteObservation, utcnow
from app.db.database import get_db
from app.db.models import User
from app.intelligence import events as event_rules
from app.intelligence import flood as flood_engine
from app.intelligence import patterns
from app.intelligence.alerts import DISCLAIMER

router = APIRouter()

# Alert lifecycle (§18). Terminal states accept no further transitions.
ALERT_OPEN = "OPEN"
ALERT_ACKNOWLEDGED = "ACKNOWLEDGED"
ALERT_INVESTIGATING = "INVESTIGATING"
ALERT_CONFIRMED = "CONFIRMED"
ALERT_RESOLVED = "RESOLVED"
ALERT_DISMISSED = "DISMISSED"

# Legal moves. A state machine rather than free-form status writes: an operator
# should not be able to move a dismissed alert back to investigating, and the
# audit trail is only meaningful if the transitions were constrained.
ALERT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    ALERT_OPEN: (ALERT_ACKNOWLEDGED, ALERT_INVESTIGATING, ALERT_DISMISSED),
    "active": (ALERT_ACKNOWLEDGED, ALERT_INVESTIGATING, ALERT_DISMISSED),
    ALERT_ACKNOWLEDGED: (ALERT_INVESTIGATING, ALERT_CONFIRMED, ALERT_RESOLVED, ALERT_DISMISSED),
    ALERT_INVESTIGATING: (ALERT_CONFIRMED, ALERT_RESOLVED, ALERT_DISMISSED),
    ALERT_CONFIRMED: (ALERT_RESOLVED,),
    ALERT_RESOLVED: (),
    ALERT_DISMISSED: (),
}


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@router.get("")
async def list_events(
    hazard: Optional[str] = Query(None),
    status: str = Query("open", description="open | resolved | all"),
    province: Optional[str] = Query(None),
    region_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Hazard events, newest activity first."""
    stmt = select(HazardEvent)
    if status == "open":
        stmt = stmt.where(HazardEvent.status.in_(event_rules.OPEN_STATES))
    elif status == "resolved":
        stmt = stmt.where(HazardEvent.status == event_rules.RESOLVED)
    if hazard:
        stmt = stmt.where(HazardEvent.hazard_type == hazard)
    if province:
        stmt = stmt.where(HazardEvent.province == province)
    if region_id:
        stmt = stmt.where(HazardEvent.region_id == region_id)

    rows = (
        await db.execute(stmt.order_by(desc(HazardEvent.last_updated_at)).limit(limit))
    ).scalars().all()

    counts = {
        r[0]: r[1]
        for r in (
            await db.execute(
                select(HazardEvent.status, func.count())
                .where(HazardEvent.status.in_(event_rules.OPEN_STATES))
                .group_by(HazardEvent.status)
            )
        ).all()
    }

    return {
        "status": "success",
        "data_source": "timestampdb" if rows else "no_data",
        "count": len(rows),
        "open_by_status": counts,
        "events": [_event_json(e) for e in rows],
        "lifecycle": list(event_rules.OPEN_STATES) + [event_rules.RESOLVED],
        "disclaimer": DISCLAIMER,
        "detail": None if rows else "no hazard events have been created yet",
    }


@router.get("/hotspots")
async def list_hotspots(
    hazard: Optional[str] = Query(None),
    reference_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Contiguous clusters of affected regions (§12).

    Declared before /{event_id} so the literal path is not captured by the
    parameterised one.
    """
    target = reference_date or await db.scalar(
        select(func.max(HazardHotspot.reference_date))
    )
    if target is None:
        return {
            "status": "success",
            "data_source": "no_data",
            "count": 0,
            "hotspots": [],
            "detail": "no hotspots detected yet",
        }

    stmt = select(HazardHotspot).where(HazardHotspot.reference_date == target)
    if hazard:
        stmt = stmt.where(HazardHotspot.hazard_type == hazard)
    rows = (
        await db.execute(stmt.order_by(desc(HazardHotspot.cluster_size)))
    ).scalars().all()

    stats = {
        s.region_id: s
        for s in (await db.execute(select(RegionGeometryStats))).scalars().all()
    }

    return {
        "status": "success",
        "data_source": "timestampdb" if rows else "no_data",
        "reference_date": target.isoformat(),
        "count": len(rows),
        "hotspots": [
            {
                "cluster_id": r.cluster_id,
                "hazard_type": r.hazard_type,
                "cluster_size": r.cluster_size,
                "region_ids": (r.region_ids or {}).get("items", []),
                "regions": [
                    {
                        "region_id": rid,
                        "district": stats[rid].district if rid in stats else None,
                        "province": stats[rid].province if rid in stats else None,
                        "centroid": (
                            [stats[rid].centroid_lat, stats[rid].centroid_lon]
                            if rid in stats
                            else None
                        ),
                    }
                    for rid in (r.region_ids or {}).get("items", [])
                ],
                "average_severity": r.average_severity,
                "maximum_severity": r.maximum_severity,
                "dominant_level": r.dominant_level,
                "provinces": (r.provinces or {}).get("items", []),
                "first_detected": r.first_detected.isoformat(),
                "growth_regions": r.growth_regions,
                "growth_rate": r.growth_rate,
            }
            for r in rows
        ],
        "rules": {
            "min_cluster_size": patterns.MIN_CLUSTER_SIZE,
            "score_threshold": patterns.CLUSTER_SCORE_THRESHOLD,
            "adjacency": "bounding-box overlap with a ~5 km buffer",
        },
    }


@router.get("/replay")
async def replay(
    hazard: Optional[str] = Query(None),
    region_id: Optional[str] = Query(None),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Historical replay (§21) — the stored state on each past cycle.

    Reads `gv_event_observations`, which the cascade appends to every cycle, so
    the timeline shows what the system actually believed on each date rather
    than what a recomputation against today's data would say. Replaying a
    recomputation would quietly rewrite history and make the feature worthless
    as a record.
    """
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=90))

    stmt = (
        select(EventObservation, HazardEvent)
        .join(HazardEvent, HazardEvent.event_id == EventObservation.event_id)
        .where(
            EventObservation.reference_date >= start,
            EventObservation.reference_date <= end,
        )
    )
    if hazard:
        stmt = stmt.where(HazardEvent.hazard_type == hazard)
    if region_id:
        stmt = stmt.where(HazardEvent.region_id == region_id)

    rows = (await db.execute(stmt.order_by(EventObservation.reference_date))).all()

    frames: dict[str, list[dict[str, Any]]] = {}
    for observation, event in rows:
        frames.setdefault(observation.reference_date.isoformat(), []).append(
            {
                "event_id": event.event_id,
                "hazard_type": event.hazard_type,
                "region_id": event.region_id,
                "district": event.district,
                "province": event.province,
                "status": observation.status,
                "severity": observation.severity,
                "score": observation.score,
                "affected_area_km2": observation.affected_area_km2,
                "change_from_previous": observation.change_from_previous,
                "transition": observation.transition,
                "data_quality": observation.data_quality,
            }
        )

    return {
        "status": "success",
        "data_source": "timestampdb" if frames else "no_data",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "frame_count": len(frames),
        # Sorted so the client can step forward without re-sorting.
        "frames": [
            {"reference_date": key, "events": value}
            for key, value in sorted(frames.items())
        ],
        "detail": None if frames else (
            "no event history in this period. Events are recorded once the "
            "analytics cascade opens one; run the historical backfill so "
            "hazards can be scored."
        ),
    }


@router.get("/{event_id}")
async def get_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """One event with its evidence and linked alerts."""
    event = await db.scalar(select(HazardEvent).where(HazardEvent.event_id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail=f"no event {event_id!r}")

    alerts = (
        await db.execute(select(Alert).where(Alert.event_id == event_id))
    ).scalars().all()
    reports = (
        await db.execute(select(FieldReport).where(FieldReport.event_id == event_id))
    ).scalars().all()

    return {
        "status": "success",
        "event": _event_json(event),
        "alerts": [{"alert_id": a.alert_id, "severity": a.severity, "status": a.status} for a in alerts],
        "field_reports": [_report_json(r) for r in reports],
        "disclaimer": DISCLAIMER,
    }


@router.get("/{event_id}/timeline")
async def event_timeline(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Every recorded cycle of one event, in order."""
    event = await db.scalar(select(HazardEvent).where(HazardEvent.event_id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail=f"no event {event_id!r}")

    rows = (
        await db.execute(
            select(EventObservation)
            .where(EventObservation.event_id == event_id)
            .order_by(EventObservation.reference_date)
        )
    ).scalars().all()

    return {
        "status": "success",
        "event_id": event_id,
        "hazard_type": event.hazard_type,
        "region_id": event.region_id,
        "count": len(rows),
        "timeline": [
            {
                "reference_date": r.reference_date.isoformat(),
                "status": r.status,
                "severity": r.severity,
                "score": r.score,
                "affected_area_km2": r.affected_area_km2,
                "change_from_previous": r.change_from_previous,
                "transition": r.transition,
                "data_quality": r.data_quality,
                "indicators": r.indicators,
            }
            for r in rows
        ],
        "phases": {
            "first_detected": event.first_detected_at.isoformat(),
            "peak": event.peak_at.isoformat() if event.peak_at else None,
            "resolved": event.resolved_at.isoformat() if event.resolved_at else None,
        },
    }


@router.get("/{event_id}/impact")
async def event_impact(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Before / during / after comparison for one event (§22).

    Windows are non-overlapping by construction — a "before" window that
    included the event would compare the event against itself and understate
    the impact.
    """
    event = await db.scalar(select(HazardEvent).where(HazardEvent.event_id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail=f"no event {event_id!r}")

    windows = event_rules.impact_window(event.first_detected_at, event.resolved_at)
    metrics = ("ndvi", "evi", "rainfall_mm", "lst_day_c", "water_fraction")

    comparison: list[dict[str, Any]] = []
    for metric in metrics:
        values: dict[str, Optional[float]] = {}
        for phase, (start, end) in windows.items():
            value = await db.scalar(
                select(func.avg(SatelliteObservation.value)).where(
                    SatelliteObservation.region_id == event.region_id,
                    SatelliteObservation.metric == metric,
                    SatelliteObservation.value.isnot(None),
                    SatelliteObservation.observation_date >= start,
                    SatelliteObservation.observation_date <= end,
                )
            )
            values[phase] = round(float(value), 4) if value is not None else None

        recovery = None
        if values["before"] is not None and values["during"] is not None and values["after"] is not None:
            loss = values["before"] - values["during"]
            if abs(loss) > 1e-9:
                recovery = round(
                    max(0.0, min(100.0, (values["after"] - values["during"]) / loss * 100.0)), 1
                )

        comparison.append(
            {
                "metric": metric,
                "before": values["before"],
                "during": values["during"],
                "after": values["after"],
                "recovery_pct": recovery,
                # Never "recovered": the signal returned, which is not the same
                # as the landscape or the livelihood recovering.
                "interpretation": "satellite-derived recovery indicator",
            }
        )

    return {
        "status": "success",
        "event_id": event_id,
        "region_id": event.region_id,
        "windows": {k: [v[0].isoformat(), v[1].isoformat()] for k, v in windows.items()},
        "comparison": comparison,
        "note": (
            "Windows do not overlap. A metric with a null phase had no "
            "observation in that window; it is not zero."
        ),
    }


@router.get("/{event_id}/recovery")
async def event_recovery(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Stored post-event recovery tracking (§23)."""
    rows = (
        await db.execute(
            select(RecoveryMetric)
            .where(RecoveryMetric.event_id == event_id)
            .order_by(desc(RecoveryMetric.reference_date))
        )
    ).scalars().all()

    return {
        "status": "success",
        "event_id": event_id,
        "data_source": "timestampdb" if rows else "no_data",
        "count": len(rows),
        "recovery": [
            {
                "metric": r.metric,
                "reference_date": r.reference_date.isoformat(),
                "pre_event_value": r.pre_event_value,
                "minimum_value": r.minimum_value,
                "current_value": r.current_value,
                "recovery_pct": r.recovery_pct,
                "days_since_event": r.days_since_event,
                "recovery_status": r.recovery_status,
                "trend": r.trend,
                "reason": r.reason,
                "interpretation": "satellite-derived recovery indicator",
            }
            for r in rows
        ],
        "statuses": [
            patterns.RECOVERING, patterns.SLOW_RECOVERY,
            patterns.STALLED, patterns.RECOVERED, patterns.UNKNOWN,
        ],
        "detail": None if rows else "recovery is tracked only after an event resolves",
    }


class VerifyEventRequest(BaseModel):
    verification_status: str = Field(..., description="CONFIRMED | REJECTED | NEEDS_INVESTIGATION")
    note: Optional[str] = Field(default=None, max_length=1000)


@router.post("/{event_id}/verify")
async def verify_event(
    event_id: str,
    request: VerifyEventRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_operator),
):
    """Operator adjudication of an automatically detected event. Audited."""
    allowed = {"CONFIRMED", "REJECTED", "NEEDS_INVESTIGATION"}
    if request.verification_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"verification_status must be one of {sorted(allowed)}",
        )

    event = await db.scalar(select(HazardEvent).where(HazardEvent.event_id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail=f"no event {event_id!r}")

    previous = event.verification_status
    event.verification_status = request.verification_status
    event.verified_by = user.username
    event.verified_at = utcnow()
    event.updated_at = utcnow()

    db.add(
        AuditLog(
            actor=user.username, actor_role=user.role, action="event.verify",
            entity_type="hazard_event", entity_id=event_id,
            detail={
                "from": previous,
                "to": request.verification_status,
                "note": request.note,
            },
        )
    )
    await db.commit()
    return {"status": "success", "event": _event_json(event)}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _event_json(e: HazardEvent) -> dict[str, Any]:
    return {
        "event_id": e.event_id,
        "hazard_type": e.hazard_type,
        "region_id": e.region_id,
        "region_name": e.region_name,
        "province": e.province,
        "district": e.district,
        "status": e.status,
        "severity": e.severity,
        "peak_severity": e.peak_severity,
        "current_score": e.current_score,
        "peak_score": e.peak_score,
        "change_rate": e.change_rate,
        "first_detected_at": e.first_detected_at.isoformat(),
        "last_updated_at": e.last_updated_at.isoformat(),
        "peak_at": e.peak_at.isoformat() if e.peak_at else None,
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
        "duration_days": e.duration_days,
        "consecutive_periods": e.consecutive_periods,
        "observation_count": e.observation_count,
        "affected_area_km2": e.affected_area_km2,
        "peak_area_km2": e.peak_area_km2,
        "evidence": e.evidence,
        "source_datasets": (e.source_datasets or {}).get("items", []),
        "reason": e.reason,
        "data_quality": e.data_quality,
        "verification_status": e.verification_status,
        "verified_by": e.verified_by,
        "calculation_version": e.calculation_version,
        "classification": "satellite-derived analytical indicator",
        "is_official_warning": False,
    }


def _report_json(r: FieldReport) -> dict[str, Any]:
    return {
        "report_id": r.report_id,
        "alert_id": r.alert_id,
        "event_id": r.event_id,
        "region_id": r.region_id,
        "reported_by": r.reported_by,
        "reported_at": r.reported_at.isoformat(),
        "latitude": r.latitude,
        "longitude": r.longitude,
        "hazard_type": r.hazard_type,
        "observation": r.observation,
        "severity": r.severity,
        "notes": r.notes,
        "photo_reference": r.photo_reference,
        "verification_status": r.verification_status,
        "reviewed_by": r.reviewed_by,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "review_notes": r.review_notes,
    }
