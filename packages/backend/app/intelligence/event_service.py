# packages/backend/app/intelligence/event_service.py
"""Database-facing half of the event, flood, hotspot and recovery engines.

The pure engines (`events`, `flood`, `patterns`, `geo`) know nothing about
storage. This module reads stored features and scores, feeds those engines, and
persists what they produce.

Bulk throughout, for the same reason as the rest of the analytics layer: a
national cycle covers 119 districts, and doing anything per region would be
hundreds of round trips to a database in another region.

Every write is an upsert on a natural key, so re-running a cycle recomputes
rather than duplicating — which is what makes the whole cascade safe to retry.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.databases.timestampdb.events import (
    EventObservation,
    HazardEvent,
    HazardHotspot,
    RecoveryMetric,
    RegionGeometryStats,
)
from app.databases.timestampdb.intelligence import (
    DerivedFeature,
    HazardScore,
    MetricBaseline,
)
from app.databases.timestampdb.models import RegionGeometryCache
from app.databases.timestampdb.models import SatelliteObservation as Observation
from app.databases.timestampdb.models import utcnow
from app.intelligence import events as event_rules
from app.intelligence import flood as flood_engine
from app.intelligence import geo
from app.intelligence import patterns
from app.intelligence.analytics_service import FEATURE_VERSION, StageResult
from app.intelligence.baselines import BASELINE_VERSION
from app.intelligence.hazards import HAZARD_VERSION

logger = logging.getLogger(__name__)

# Metrics whose recovery is tracked after an event ends.
RECOVERY_METRICS = ("ndvi", "evi", "lst_day_c")
# Days after resolution to keep monitoring recovery.
RECOVERY_WINDOW_DAYS = 180


# ---------------------------------------------------------------------------
# Stage: geometry facts
# ---------------------------------------------------------------------------


async def sync_geometry_stats(db: AsyncSession) -> StageResult:
    """Derive area, centroid, bbox and adjacency from the stored boundaries.

    Runs from the stored boundary cache, which the map already populates, so this
    needs no Earth Engine call. Skipped entirely when the facts are already
    current for the stored boundary source — boundaries change on the order of
    years and recomputing 119 polygons every night would be pure waste.
    """
    result = StageResult(stage="geometry_stats")

    # The ORM model, not a hand-written table name. The table is
    # `gee_region_geometry` (it predates the gv_ prefix), and a raw string got
    # that wrong once already — the mapped class cannot.
    row = (
        await db.execute(
            select(RegionGeometryCache).order_by(desc(RegionGeometryCache.generated_at)).limit(1)
        )
    ).scalars().first()

    if row is None:
        result.status = "partial"
        result.details = {
            "note": (
                "no region boundaries stored yet; flood extent in km² and "
                "hotspot clustering both need them. Load the map once, or call "
                "/geovision/regions/geometry, to populate them."
            )
        }
        return result

    existing = await db.scalar(select(func.count()).select_from(RegionGeometryStats))
    feature_count = len((row.geojson or {}).get("features", []))
    if existing and existing >= feature_count:
        result.records_out = existing
        result.details = {"note": "geometry facts already current", "regions": existing}
        return result

    facts = geo.facts_from_geojson(row.geojson or {})
    if not facts:
        result.status = "partial"
        result.details = {"note": "stored boundaries produced no usable regions"}
        return result

    rows = [f.to_row() | {"source": row.source, "computed_at": utcnow()} for f in facts.values()]
    for start in range(0, len(rows), 200):
        chunk = rows[start : start + 200]
        stmt = pg_insert(RegionGeometryStats).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["region_id"],
            set_={k: stmt.excluded[k] for k in chunk[0] if k != "region_id"},
        )
        await db.execute(stmt)
    await db.commit()

    total_area = sum(f.area_km2 or 0 for f in facts.values())
    result.records_out = len(rows)
    result.details = {
        "regions": len(rows),
        "total_area_km2": round(total_area, 1),
        "mean_neighbours": round(
            sum(len(f.neighbours) for f in facts.values()) / len(facts), 1
        ),
        "source": row.source,
    }
    logger.info("geometry stats: %s", result.details)
    return result


async def load_geometry_stats(db: AsyncSession) -> dict[str, RegionGeometryStats]:
    rows = (await db.execute(select(RegionGeometryStats))).scalars().all()
    return {r.region_id: r for r in rows}


# ---------------------------------------------------------------------------
# Stage: flood detection
# ---------------------------------------------------------------------------


async def detect_floods(
    db: AsyncSession, *, reference_date: Optional[date] = None
) -> StageResult:
    """Assess every region for flooding and store it as a hazard score.

    Written into `gv_hazard_scores` under hazard='flood' rather than a
    flood-specific table, so the fused multi-hazard index, the ranking table
    and the alert engine all read flood the same way they read every other
    hazard — no special case anywhere downstream.
    """
    result = StageResult(stage="flood_detection")
    reference_date = reference_date or datetime.now(timezone.utc).date()

    features = (
        await db.execute(
            select(DerivedFeature).where(
                DerivedFeature.metric == "water_fraction",
                DerivedFeature.reference_date == reference_date,
                DerivedFeature.calculation_version == FEATURE_VERSION,
            )
        )
    ).scalars().all()
    result.records_in = len(features)

    if not features:
        result.status = "partial"
        result.details = {
            "note": (
                "no water_fraction features for this cycle. Sentinel-1 must be "
                "ingested with the water_fraction derived metric before flood "
                "detection can run."
            )
        }
        return result

    stats = await load_geometry_stats(db)
    geography = await _region_geography(db)

    rows: list[dict[str, Any]] = []
    detected = 0
    unknown = 0

    for feature in features:
        region_stats = stats.get(feature.region_id)
        assessment = flood_engine.assess(
            region_id=feature.region_id,
            current_fraction=feature.current_value,
            baseline_fraction=feature.baseline_value,
            baseline_stddev=feature.baseline_stddev,
            baseline_years=feature.baseline_years,
            region_area_km2=region_stats.area_km2 if region_stats else None,
            data_quality=feature.quality_score,
        )

        if assessment.status != "ok":
            unknown += 1
        elif assessment.severity != flood_engine.NONE:
            detected += 1

        payload = assessment.to_json()
        rows.append(
            {
                "region_id": feature.region_id,
                "hazard": "flood",
                "reference_date": reference_date,
                "score": assessment.score,
                # A flood that cannot be assessed is INSUFFICIENT_DATA, never
                # NORMAL: "not checked" and "checked and clear" are different.
                "level": (
                    "INSUFFICIENT_DATA"
                    if assessment.status != "ok"
                    else assessment.severity
                ),
                "confidence": (
                    round(min(1.0, (feature.baseline_years or 0) / 10.0), 2)
                    if assessment.status == "ok"
                    else None
                ),
                "contributors": {
                    "items": [
                        {
                            "component": "water_fraction_anomaly",
                            "label": "New surface water",
                            "value": assessment.current_fraction,
                            "anomaly": assessment.new_water_fraction,
                            "weight": 1.0,
                            "contribution": assessment.score or 0.0,
                            "direction": "worsening"
                            if (assessment.score or 0) > 0
                            else "stable",
                            "detail": assessment.reason,
                        }
                    ],
                    "assessment": payload,
                },
                "primary_driver": "New surface water extent",
                "reason": assessment.reason,
                "previous_level": None,
                "level_since": reference_date,
                "consecutive_periods": 1,
                "data_quality": feature.quality_score,
                "status": assessment.status,
                "calculation_version": HAZARD_VERSION,
            }
        )

    if rows:
        await _upsert(db, HazardScore, rows, "uq_hazard_score_identity")

    result.records_out = len(rows)
    result.details = {
        "assessed": len(rows),
        "flood_detected": detected,
        "insufficient_data": unknown,
        "parameters": flood_engine.DEFAULT_PARAMETERS.__dict__,
    }
    logger.info("flood detection: %s", result.details)
    return result


# ---------------------------------------------------------------------------
# Stage: hazard events
# ---------------------------------------------------------------------------


async def update_events(
    db: AsyncSession, *, reference_date: Optional[date] = None
) -> StageResult:
    """Open, update and resolve hazard events from this cycle's scores."""
    result = StageResult(stage="events")
    reference_date = reference_date or datetime.now(timezone.utc).date()

    scores = (
        await db.execute(
            select(HazardScore).where(
                HazardScore.reference_date == reference_date,
                HazardScore.calculation_version == HAZARD_VERSION,
            )
        )
    ).scalars().all()
    result.records_in = len(scores)

    open_events = {
        (e.hazard_type, e.region_id): e
        for e in (
            await db.execute(
                select(HazardEvent).where(HazardEvent.status.in_(event_rules.OPEN_STATES))
            )
        ).scalars().all()
    }

    geography = await _region_geography(db)
    sequence = await _next_sequence(db, reference_date.year)

    opened = updated = resolved = 0
    timeline_rows: list[dict[str, Any]] = []

    for score_row in scores:
        existing = open_events.get((score_row.hazard, score_row.region_id))
        state = _to_state(existing) if existing else None

        decision = event_rules.evaluate(
            hazard_type=score_row.hazard,
            region_id=score_row.region_id,
            score=score_row.score,
            severity=score_row.level,
            reference_date=reference_date,
            existing=state,
            data_quality=score_row.data_quality,
            hazard_status=score_row.status,
        )

        if not decision.should_write:
            continue

        province, district = geography.get(score_row.region_id, (None, None))

        if decision.action == "open":
            event_id = event_rules.build_event_id(
                score_row.hazard, reference_date, sequence
            )
            sequence += 1
            event = HazardEvent(
                event_id=event_id,
                hazard_type=score_row.hazard,
                region_id=score_row.region_id,
                region_name=district or score_row.region_id,
                province=province,
                district=district,
                status=decision.status,
                severity=decision.severity,
                peak_severity=decision.severity,
                current_score=decision.score,
                peak_score=decision.score,
                onset_score=decision.score,
                first_detected_at=reference_date,
                last_updated_at=reference_date,
                duration_days=0,
                consecutive_periods=1,
                observation_count=1,
                evidence=decision.evidence,
                source_datasets={"items": _datasets_for(score_row.hazard)},
                reason=decision.reason,
                data_quality=score_row.data_quality,
                calculation_version=event_rules.EVENT_VERSION,
            )
            db.add(event)
            opened += 1
            timeline_rows.append(_timeline_row(event_id, reference_date, decision, score_row))
            continue

        if existing is None:
            continue

        previous_status = existing.status
        existing.status = decision.status
        existing.severity = decision.severity
        existing.current_score = decision.score
        existing.change_rate = decision.change_from_previous
        existing.last_updated_at = reference_date
        existing.duration_days = (reference_date - existing.first_detected_at).days
        existing.observation_count += 1
        existing.consecutive_periods = (
            existing.consecutive_periods + 1 if previous_status == decision.status else 1
        )
        existing.evidence = decision.evidence
        existing.reason = decision.reason
        existing.data_quality = score_row.data_quality
        existing.updated_at = utcnow()

        if decision.score is not None and (
            existing.peak_score is None or decision.score >= existing.peak_score
        ):
            existing.peak_score = decision.score
            existing.peak_severity = decision.severity
            existing.peak_at = reference_date

        if decision.action == "resolve":
            existing.resolved_at = reference_date
            resolved += 1
        else:
            updated += 1

        timeline_rows.append(
            _timeline_row(existing.event_id, reference_date, decision, score_row)
        )

    if timeline_rows:
        await _upsert(db, EventObservation, timeline_rows, "uq_event_observation")

    await db.commit()

    result.records_out = opened + updated + resolved
    result.details = {
        "opened": opened,
        "updated": updated,
        "resolved": resolved,
        "open_events": len(open_events) + opened - resolved,
    }
    logger.info("events: %s", result.details)
    return result


def _timeline_row(
    event_id: str, reference_date: date, decision, score_row
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "reference_date": reference_date,
        "status": decision.status,
        "severity": decision.severity,
        "score": decision.score,
        "affected_area_km2": (decision.evidence or {}).get("affected_area_km2"),
        "change_from_previous": decision.change_from_previous,
        "data_quality": score_row.data_quality,
        "indicators": {
            "contributors": (score_row.contributors or {}).get("items", []),
            "primary_driver": score_row.primary_driver,
        },
        "transition": decision.transition,
    }


def _to_state(event: HazardEvent) -> event_rules.EventState:
    return event_rules.EventState(
        event_id=event.event_id,
        hazard_type=event.hazard_type,
        region_id=event.region_id,
        status=event.status,
        severity=event.severity,
        current_score=event.current_score,
        peak_score=event.peak_score,
        onset_score=event.onset_score,
        first_detected_at=event.first_detected_at,
        last_updated_at=event.last_updated_at,
        peak_at=event.peak_at,
        consecutive_periods=event.consecutive_periods,
        observation_count=event.observation_count,
        affected_area_km2=event.affected_area_km2,
        peak_area_km2=event.peak_area_km2,
        below_recovery_periods=(event.evidence or {}).get("below_recovery_periods", 0),
    )


def _datasets_for(hazard: str) -> list[str]:
    return {
        "flood": ["sentinel1"],
        "drought": ["chirps", "mod13q1", "mod11a2"],
        "crop_stress": ["mod13q1", "chirps", "mod11a2"],
        "heat_stress": ["mod11a2"],
        "multi_hazard": ["chirps", "mod13q1", "mod11a2", "sentinel1"],
    }.get(hazard, [])


async def _next_sequence(db: AsyncSession, year: int) -> int:
    """Next event number for the year, so ids read FLOOD-2026-0007."""
    count = await db.scalar(
        select(func.count()).select_from(HazardEvent).where(
            func.extract("year", HazardEvent.first_detected_at) == year
        )
    )
    return int(count or 0) + 1


# ---------------------------------------------------------------------------
# Stage: hotspots
# ---------------------------------------------------------------------------


async def detect_hotspots(
    db: AsyncSession, *, reference_date: Optional[date] = None
) -> StageResult:
    """Find contiguous clusters of affected regions per hazard."""
    result = StageResult(stage="hotspots")
    reference_date = reference_date or datetime.now(timezone.utc).date()

    stats = await load_geometry_stats(db)
    if not stats:
        result.status = "partial"
        result.details = {
            "note": "no region adjacency available; run the geometry_stats stage first"
        }
        return result

    adjacency = {
        rid: (s.neighbours or {}).get("items", []) for rid, s in stats.items()
    }
    provinces = {rid: s.province for rid, s in stats.items()}

    scores = (
        await db.execute(
            select(HazardScore).where(
                HazardScore.reference_date == reference_date,
                HazardScore.score.isnot(None),
                HazardScore.calculation_version == HAZARD_VERSION,
            )
        )
    ).scalars().all()
    result.records_in = len(scores)

    by_hazard: dict[str, dict[str, float]] = {}
    levels: dict[str, dict[str, str]] = {}
    for row in scores:
        by_hazard.setdefault(row.hazard, {})[row.region_id] = row.score
        levels.setdefault(row.hazard, {})[row.region_id] = row.level

    previous = await _previous_hotspot_members(db, reference_date)

    rows: list[dict[str, Any]] = []
    for hazard, hazard_scores in by_hazard.items():
        found = patterns.detect_hotspots(
            hazard_type=hazard,
            scores=hazard_scores,
            levels=levels.get(hazard, {}),
            adjacency=adjacency,
            provinces=provinces,
        )
        for hotspot in found:
            prior = previous.get((hazard, hotspot.cluster_id))
            growth_regions, growth_rate = patterns.hotspot_growth(hotspot, prior)
            rows.append(
                {
                    "cluster_id": hotspot.cluster_id,
                    "hazard_type": hazard,
                    "reference_date": reference_date,
                    "region_ids": {"items": hotspot.region_ids},
                    "cluster_size": hotspot.cluster_size,
                    "average_severity": round(hotspot.average_severity, 1),
                    "maximum_severity": round(hotspot.maximum_severity, 1),
                    "dominant_level": hotspot.dominant_level,
                    "provinces": {"items": hotspot.provinces},
                    "first_detected": reference_date,
                    "growth_regions": growth_regions,
                    "growth_rate": growth_rate,
                    "calculation_version": patterns.HOTSPOT_VERSION,
                }
            )

    if rows:
        await _upsert(db, HazardHotspot, rows, "uq_hotspot_cycle")

    result.records_out = len(rows)
    result.details = {
        "hotspots": len(rows),
        "by_hazard": {h: sum(1 for r in rows if r["hazard_type"] == h) for h in by_hazard},
        "min_cluster_size": patterns.MIN_CLUSTER_SIZE,
        "score_threshold": patterns.CLUSTER_SCORE_THRESHOLD,
    }
    logger.info("hotspots: %s", result.details)
    return result


async def _previous_hotspot_members(
    db: AsyncSession, reference_date: date
) -> dict[tuple[str, str], set[str]]:
    previous_date = await db.scalar(
        select(func.max(HazardHotspot.reference_date)).where(
            HazardHotspot.reference_date < reference_date
        )
    )
    if previous_date is None:
        return {}
    rows = (
        await db.execute(
            select(HazardHotspot).where(HazardHotspot.reference_date == previous_date)
        )
    ).scalars().all()
    return {
        (r.hazard_type, r.cluster_id): set((r.region_ids or {}).get("items", []))
        for r in rows
    }


# ---------------------------------------------------------------------------
# Stage: recovery
# ---------------------------------------------------------------------------


async def compute_recovery(
    db: AsyncSession, *, reference_date: Optional[date] = None
) -> StageResult:
    """Track indicators back toward pre-event levels for resolved events."""
    result = StageResult(stage="recovery")
    reference_date = reference_date or datetime.now(timezone.utc).date()

    cutoff = reference_date - timedelta(days=RECOVERY_WINDOW_DAYS)
    resolved = (
        await db.execute(
            select(HazardEvent).where(
                HazardEvent.status == event_rules.RESOLVED,
                HazardEvent.resolved_at.isnot(None),
                HazardEvent.resolved_at >= cutoff,
            )
        )
    ).scalars().all()
    result.records_in = len(resolved)

    if not resolved:
        result.details = {"note": "no recently resolved events to monitor"}
        return result

    rows: list[dict[str, Any]] = []
    for event in resolved:
        windows = event_rules.impact_window(event.first_detected_at, event.resolved_at)
        for metric in RECOVERY_METRICS:
            pre = await _window_mean(db, event.region_id, metric, windows["before"])
            low = await _window_extreme(db, event.region_id, metric, windows["during"], "min")
            current = await _window_mean(
                db, event.region_id, metric,
                (reference_date - timedelta(days=30), reference_date),
            )
            previous = await db.scalar(
                select(RecoveryMetric.recovery_pct)
                .where(
                    RecoveryMetric.event_id == event.event_id,
                    RecoveryMetric.metric == metric,
                    RecoveryMetric.reference_date < reference_date,
                )
                .order_by(desc(RecoveryMetric.reference_date))
                .limit(1)
            )

            assessment = patterns.assess_recovery(
                region_id=event.region_id,
                metric=metric,
                pre_event_value=pre,
                minimum_value=low,
                current_value=current,
                days_since_event=(reference_date - event.resolved_at).days,
                previous_recovery_pct=previous,
            )
            rows.append(
                {
                    "event_id": event.event_id,
                    "region_id": event.region_id,
                    "metric": metric,
                    "reference_date": reference_date,
                    "baseline_value": None,
                    "pre_event_value": pre,
                    "minimum_value": low,
                    "current_value": current,
                    "recovery_pct": assessment.recovery_pct,
                    "days_since_event": assessment.days_since_event,
                    "recovery_status": assessment.status,
                    "trend": assessment.trend,
                    "reason": assessment.reason,
                    "calculation_version": patterns.RECOVERY_VERSION,
                }
            )

    if rows:
        await _upsert(db, RecoveryMetric, rows, "uq_recovery_identity")

    result.records_out = len(rows)
    known = sum(1 for r in rows if r["recovery_pct"] is not None)
    result.details = {
        "events_monitored": len(resolved),
        "metrics_written": len(rows),
        "with_recovery_estimate": known,
    }
    logger.info("recovery: %s", result.details)
    return result


async def _window_mean(
    db: AsyncSession, region_id: str, metric: str, window: tuple[date, date]
) -> Optional[float]:
    value = await db.scalar(
        select(func.avg(Observation.value)).where(
            Observation.region_id == region_id,
            Observation.metric == metric,
            Observation.value.isnot(None),
            Observation.observation_date >= window[0],
            Observation.observation_date <= window[1],
        )
    )
    return round(float(value), 4) if value is not None else None


async def _window_extreme(
    db: AsyncSession, region_id: str, metric: str, window: tuple[date, date], how: str
) -> Optional[float]:
    aggregate = func.min if how == "min" else func.max
    value = await db.scalar(
        select(aggregate(Observation.value)).where(
            Observation.region_id == region_id,
            Observation.metric == metric,
            Observation.value.isnot(None),
            Observation.observation_date >= window[0],
            Observation.observation_date <= window[1],
        )
    )
    return round(float(value), 4) if value is not None else None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _region_geography(db: AsyncSession) -> dict[str, tuple]:
    rows = (
        await db.execute(
            select(
                Observation.region_id, Observation.province, Observation.district
            ).distinct(Observation.region_id)
        )
    ).all()
    return {r.region_id: (r.province, r.district) for r in rows}


async def _upsert(
    db: AsyncSession, model, rows: list[dict[str, Any]], constraint: str
) -> None:
    for start in range(0, len(rows), 300):
        chunk = rows[start : start + 300]
        stmt = pg_insert(model).values(chunk)
        keys = {c.name for c in model.__table__.primary_key.columns}
        update_cols = {col: stmt.excluded[col] for col in chunk[0] if col not in keys}
        await db.execute(
            stmt.on_conflict_do_update(constraint=constraint, set_=update_cols)
        )
    await db.commit()
