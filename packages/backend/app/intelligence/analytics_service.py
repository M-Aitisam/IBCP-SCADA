# packages/backend/app/intelligence/analytics_service.py
"""Database-facing half of the intelligence layer.

The pure engines (quality, baselines, hazards, alerts) know nothing about
storage. This module reads observations, feeds those engines, and persists what
they produce.

Everything is bulk. A national cycle covers 119 districts × 5 metrics; doing it
per region would be ~600 round trips to a database in another region, which at
~200 ms each is two minutes of pure latency before any work happens. Each stage
below issues a handful of queries regardless of how many regions there are.

Every write is an upsert on a natural key, so the whole cascade is idempotent:
re-running a cycle recomputes and overwrites rather than accumulating
duplicates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, bindparam, desc, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.databases.timestampdb.intelligence import (
    Alert,
    DailyBrief,
    DerivedFeature,
    HazardScore,
    MetricBaseline,
)
from app.databases.timestampdb.models import SatelliteObservation as Observation
from app.databases.timestampdb.models import utcnow
from app.intelligence import alerts as alert_rules
from app.intelligence import baselines as baseline_engine
from app.intelligence import hazards as hazard_engine
from app.intelligence.quality import aggregate_quality, score_observation

logger = logging.getLogger(__name__)

FEATURE_VERSION = "f1"
BRIEF_VERSION = "br1"

# Metrics the analytics cascade computes features for, with the dataset each
# comes from and how it collapses over time.
FEATURE_METRICS: tuple[tuple[str, str, str], ...] = (
    ("ndvi", "mod13q1", "mean"),
    ("evi", "mod13q1", "mean"),
    ("rainfall_mm", "chirps", "sum"),
    ("lst_day_c", "mod11a2", "mean"),
    ("lst_night_c", "mod11a2", "mean"),
    # Sentinel-1 surface-water fraction. Needs a baseline like everything
    # else: the absolute fraction cannot distinguish flooding from permanently
    # dark dry surfaces, only its departure from the region's own normal can.
    ("water_fraction", "sentinel1", "mean"),
)

# How far back a "current" value may be drawn from. Wide enough to cover a
# 16-day composite plus publication lag; anything older is genuinely stale and
# should surface as missing rather than be presented as current.
CURRENT_WINDOW_DAYS = 32

# Rows per executemany batch when writing quality scores. Large enough to
# amortise the round trip, small enough to keep each statement's parameter
# count well inside Postgres limits.
QUALITY_UPDATE_BATCH = 1000

# Cap on observations scored per cycle. Scoring is resumable — only unscored
# rows are selected — so a large backlog drains across several runs instead of
# making one run unbounded.
QUALITY_SCAN_LIMIT = 20000


@dataclass
class StageResult:
    stage: str
    status: str = "success"
    records_in: int = 0
    records_out: int = 0
    records_rejected: int = 0
    error: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "records_in": self.records_in,
            "records_out": self.records_out,
            "records_rejected": self.records_rejected,
            "error": self.error,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Stage: data quality
# ---------------------------------------------------------------------------


async def score_observation_quality(
    db: AsyncSession, *, since: Optional[date] = None, as_of: Optional[date] = None
) -> StageResult:
    """Attach quality scores to observations that do not have one yet.

    Only unscored rows are touched, so re-running is cheap and a backfill of
    scores can proceed in chunks without redoing finished work.
    """
    result = StageResult(stage="data_quality")
    as_of = as_of or datetime.now(timezone.utc).date()

    # Typical pixel count per (dataset, region): the reference against which
    # spatial coverage is judged. Median, not mean, so a handful of
    # near-empty scenes cannot drag the expectation down.
    reference_stmt = select(
        Observation.dataset,
        Observation.region_id,
        func.percentile_cont(0.5)
        .within_group(Observation.pixel_count)
        .label("typical_pixels"),
    ).where(Observation.pixel_count.isnot(None)).group_by(
        Observation.dataset, Observation.region_id
    )
    reference = {
        (r.dataset, r.region_id): r.typical_pixels
        for r in (await db.execute(reference_stmt)).all()
    }

    conditions = [Observation.quality_score.is_(None)]
    if since is not None:
        conditions.append(Observation.observation_date >= since)

    rows = (
        (await db.execute(select(Observation).where(and_(*conditions)).limit(QUALITY_SCAN_LIMIT)))
        .scalars()
        .all()
    )
    result.records_in = len(rows)

    from app.ingestion.registry import DATASETS

    updates: list[dict[str, Any]] = []
    for row in rows:
        config = DATASETS.get(row.dataset)
        band = config.band_for_metric(row.metric) if config else None
        assessment = score_observation(
            value=row.value,
            cloud_percentage=row.cloud_percentage,
            pixel_count=row.pixel_count,
            reference_pixel_count=reference.get((row.dataset, row.region_id)),
            observation_date=row.observation_date,
            as_of=as_of,
            cadence_days=config.cadence.nominal_days if config else None,
            is_quality_band=bool(band and band.is_quality_band),
            processing_status=row.processing_status,
        )
        updates.append(
            {
                "observation_timestamp": row.observation_timestamp,
                "observation_key": row.observation_key,
                **assessment.to_row(),
            }
        )

    # One prepared statement executed against many parameter sets, NOT one
    # statement per row. Against a database in another region each round trip
    # costs ~200ms, so a per-row loop over 5,000 observations is ~15 minutes of
    # pure latency; as an executemany it is a handful of round trips.
    if updates:
        # Core table, not the ORM entity: passing an ORM class to an
        # executemany makes SQLAlchemy route it through "ORM bulk UPDATE by
        # primary key", which expects the PK inside each parameter dict and
        # rejects a statement that carries its own WHERE. The Core form is the
        # plain UPDATE ... WHERE this needs.
        observations = Observation.__table__
        stmt = (
            sa_update(observations)
            .where(
                observations.c.observation_timestamp == bindparam("b_ts"),
                observations.c.observation_key == bindparam("b_key"),
            )
            .values(
                quality_score=bindparam("b_score"),
                quality_status=bindparam("b_status"),
                quality_flags=bindparam("b_flags", type_=JSONB),
                quality_reason=bindparam("b_reason"),
            )
        )
        params = [
            {
                "b_ts": u["observation_timestamp"],
                "b_key": u["observation_key"],
                "b_score": u["quality_score"],
                "b_status": u["quality_status"],
                "b_flags": u["quality_flags"],
                "b_reason": u["quality_reason"],
            }
            for u in updates
        ]
        for start in range(0, len(params), QUALITY_UPDATE_BATCH):
            await db.execute(stmt, params[start : start + QUALITY_UPDATE_BATCH])
            await db.commit()
            logger.info(
                "data_quality: %d/%d scored",
                min(start + QUALITY_UPDATE_BATCH, len(params)),
                len(params),
            )

    result.records_out = len(updates)
    result.details = {"scored": len(updates), "as_of": as_of.isoformat()}
    logger.info("data_quality: scored %d observation(s)", len(updates))
    return result


# ---------------------------------------------------------------------------
# Stage: baselines
# ---------------------------------------------------------------------------


async def rebuild_baselines(
    db: AsyncSession, *, up_to: Optional[date] = None
) -> StageResult:
    """Recompute monthly climatologies for every configured metric."""
    result = StageResult(stage="baselines")
    totals = baseline_engine.BaselineResult()

    for metric, dataset, aggregation in FEATURE_METRICS:
        rows = await baseline_engine.compute_monthly_baselines(
            db, metric=metric, dataset=dataset, aggregation=aggregation, up_to=up_to
        )
        written = await baseline_engine.persist_baselines(db, rows)
        totals.computed += written.computed
        totals.sufficient += written.sufficient
        totals.insufficient += written.insufficient

    result.records_out = totals.computed
    result.details = totals.to_json()
    result.details["min_years"] = baseline_engine.MIN_BASELINE_YEARS
    if totals.sufficient == 0 and totals.computed > 0:
        # Not a failure — the honest state of a database without history.
        result.status = "partial"
        result.details["note"] = (
            "no region/metric has enough years for a climatology; "
            "anomalies will report insufficient_data until the historical "
            "backfill completes"
        )
    logger.info("baselines: %s", result.details)
    return result


# ---------------------------------------------------------------------------
# Stage: derived features
# ---------------------------------------------------------------------------


async def _current_values(
    db: AsyncSession, metric: str, dataset: str, aggregation: str, window: tuple[date, date]
) -> dict[str, dict[str, Any]]:
    """Per-region value over one window, two-stage aggregated."""
    start, end = window
    collapse = (
        func.sum(Observation.value)
        if aggregation == "sum"
        else func.avg(Observation.value)
    )
    stmt = (
        select(
            Observation.region_id,
            collapse.label("value"),
            func.max(Observation.observation_date).label("observation_date"),
            func.count().label("observations"),
            func.avg(Observation.quality_score).label("quality"),
        )
        .where(
            Observation.metric == metric,
            Observation.dataset == dataset,
            Observation.value.isnot(None),
            Observation.observation_date >= start,
            Observation.observation_date <= end,
        )
        .group_by(Observation.region_id)
    )
    return {
        r.region_id: {
            "value": float(r.value) if r.value is not None else None,
            "observation_date": r.observation_date,
            "observations": int(r.observations or 0),
            "quality": float(r.quality) if r.quality is not None else None,
        }
        for r in (await db.execute(stmt)).all()
    }


async def compute_features(
    db: AsyncSession, *, reference_date: Optional[date] = None
) -> StageResult:
    """Phase 8 — temporal intelligence for every region and metric.

    Windows are compared like-for-like: the 7-day change compares the last 7
    days against the 7 before it, not against a single reading. For composite
    products a single reading may not exist on a given day at all, and
    comparing "the value today" to "the value 7 days ago" would frequently
    compare something to nothing.
    """
    result = StageResult(stage="features")
    reference_date = reference_date or datetime.now(timezone.utc).date()
    rows_out: list[dict[str, Any]] = []

    for metric, dataset, aggregation in FEATURE_METRICS:
        current_window = (reference_date - timedelta(days=CURRENT_WINDOW_DAYS), reference_date)
        current = await _current_values(db, metric, dataset, aggregation, current_window)
        if not current:
            continue

        prev_7 = await _current_values(
            db, metric, dataset, aggregation,
            (reference_date - timedelta(days=14), reference_date - timedelta(days=8)),
        )
        recent_7 = await _current_values(
            db, metric, dataset, aggregation,
            (reference_date - timedelta(days=7), reference_date),
        )
        prev_30 = await _current_values(
            db, metric, dataset, aggregation,
            (reference_date - timedelta(days=60), reference_date - timedelta(days=31)),
        )
        year_ago = await _current_values(
            db, metric, dataset, aggregation,
            (
                reference_date - timedelta(days=365 + CURRENT_WINDOW_DAYS),
                reference_date - timedelta(days=365),
            ),
        )

        baselines = await baseline_engine.load_baselines_bulk(
            db, metric=metric, period_key=reference_date.month
        )

        for region_id, entry in current.items():
            value = entry["value"]
            baseline = baselines.get(region_id)

            change_7d = _delta(recent_7.get(region_id, {}).get("value"), prev_7.get(region_id, {}).get("value"))
            change_30d = _delta(value, prev_30.get(region_id, {}).get("value"))
            change_yoy = _delta(value, year_ago.get(region_id, {}).get("value"))

            z = baseline_engine.z_score(value, baseline)
            pct = baseline_engine.percentile_rank(value, baseline)
            anomaly = (
                value - baseline.mean_value
                if value is not None and baseline and baseline.mean_value is not None
                else None
            )
            anomaly_pct = (
                (anomaly / abs(baseline.mean_value)) * 100.0
                if anomaly is not None
                and baseline
                and baseline.mean_value
                and abs(baseline.mean_value) > 1e-9
                else None
            )

            rows_out.append(
                {
                    "region_id": region_id,
                    "metric": metric,
                    "dataset": dataset,
                    "reference_date": reference_date,
                    "current_value": _round(value),
                    "observation_date": entry["observation_date"],
                    "change_7d": _round(change_7d[0]),
                    "change_7d_pct": _round(change_7d[1], 1),
                    "change_30d": _round(change_30d[0]),
                    "change_30d_pct": _round(change_30d[1], 1),
                    "change_yoy": _round(change_yoy[0]),
                    "change_yoy_pct": _round(change_yoy[1], 1),
                    "baseline_value": _round(baseline.mean_value) if baseline else None,
                    "baseline_stddev": _round(baseline.stddev_value) if baseline else None,
                    "baseline_years": baseline.sample_years if baseline else None,
                    "seasonal_anomaly": _round(anomaly),
                    "seasonal_anomaly_pct": _round(anomaly_pct, 1),
                    "z_score": z,
                    "percentile": pct,
                    "trend_direction": _direction(change_30d[1]),
                    "observation_count": entry["observations"],
                    "quality_score": _round(entry["quality"], 1),
                    # The status the UI keys off: a feature with no baseline is
                    # usable for levels but not for anomalies, and says so.
                    "status": "ok" if z is not None else "no_baseline",
                    "calculation_version": FEATURE_VERSION,
                }
            )

    result.records_in = len(rows_out)
    if rows_out:
        await _upsert(db, DerivedFeature, rows_out, "uq_derived_feature_identity")
    result.records_out = len(rows_out)
    result.details = {
        "metrics": [m for m, _, _ in FEATURE_METRICS],
        "with_anomaly": sum(1 for r in rows_out if r["z_score"] is not None),
        "reference_date": reference_date.isoformat(),
    }
    logger.info("features: %d row(s), %d with anomalies", len(rows_out), result.details["with_anomaly"])
    return result


# ---------------------------------------------------------------------------
# Stage: hazard scoring
# ---------------------------------------------------------------------------


async def compute_hazards(
    db: AsyncSession, *, reference_date: Optional[date] = None
) -> StageResult:
    """Phases 7/10/13 — score every hazard for every region, then fuse."""
    result = StageResult(stage="hazards")
    reference_date = reference_date or datetime.now(timezone.utc).date()

    features = (
        await db.execute(
            select(DerivedFeature).where(
                DerivedFeature.reference_date == reference_date,
                DerivedFeature.calculation_version == FEATURE_VERSION,
            )
        )
    ).scalars().all()

    by_region: dict[str, dict[str, DerivedFeature]] = {}
    for feature in features:
        by_region.setdefault(feature.region_id, {})[feature.metric] = feature

    result.records_in = len(by_region)

    # Previous cycle's levels, for persistence counting.
    previous = await _previous_levels(db, reference_date)

    rows_out: list[dict[str, Any]] = []
    for region_id, metrics in by_region.items():
        def z(name: str) -> Optional[float]:
            f = metrics.get(name)
            return f.z_score if f else None

        def val(name: str) -> Optional[float]:
            f = metrics.get(name)
            return f.current_value if f else None

        years = max(
            (f.baseline_years or 0 for f in metrics.values()), default=0
        ) or None
        qualities = [f.quality_score for f in metrics.values() if f.quality_score is not None]
        quality = sum(qualities) / len(qualities) if qualities else None

        crop = hazard_engine.crop_health(
            ndvi=val("ndvi"), ndvi_z=z("ndvi"), rainfall_z=z("rainfall_mm"),
            lst_z=z("lst_day_c"), baseline_years=years, data_quality=quality,
        )
        drought = hazard_engine.drought(
            rainfall_z=z("rainfall_mm"), ndvi_z=z("ndvi"), lst_z=z("lst_day_c"),
            baseline_years=years, data_quality=quality,
            previous_level=previous.get((region_id, hazard_engine.HAZARD_DROUGHT), (None, 0))[0],
            consecutive_periods=previous.get((region_id, hazard_engine.HAZARD_DROUGHT), (None, 0))[1] + 1,
        )
        heat = hazard_engine.heat_stress(
            lst_day_z=z("lst_day_c"), lst_night_z=z("lst_night_c"),
            lst_day_value=val("lst_day_c"), baseline_years=years, data_quality=quality,
        )
        fused = hazard_engine.multi_hazard(
            {
                hazard_engine.HAZARD_DROUGHT: drought,
                hazard_engine.HAZARD_CROP: crop,
                hazard_engine.HAZARD_HEAT: heat,
                # Flood requires SAR change detection against a pre-event
                # baseline; not computed here, and excluded rather than
                # defaulted to zero so the fusion renormalises honestly.
                hazard_engine.HAZARD_FLOOD: None,
            }
        )

        for outcome in (drought, crop, heat, fused):
            prior_level, prior_streak = previous.get((region_id, outcome.hazard), (None, 0))
            same = prior_level == outcome.level
            rows_out.append(
                {
                    "region_id": region_id,
                    "hazard": outcome.hazard,
                    "reference_date": reference_date,
                    "score": None if outcome.score is None else round(outcome.score, 1),
                    "level": outcome.level,
                    "confidence": outcome.confidence,
                    "contributors": {"items": [c.to_json() for c in outcome.contributors]},
                    "primary_driver": outcome.primary_driver,
                    "reason": outcome.reason,
                    "previous_level": prior_level,
                    "level_since": reference_date if not same else None,
                    "consecutive_periods": (prior_streak + 1) if same else 1,
                    "data_quality": _round(quality, 1),
                    "status": outcome.status,
                    "calculation_version": hazard_engine.HAZARD_VERSION,
                }
            )

    if rows_out:
        await _upsert(db, HazardScore, rows_out, "uq_hazard_score_identity")

    result.records_out = len(rows_out)
    scored = sum(1 for r in rows_out if r["score"] is not None)
    result.details = {
        "regions": len(by_region),
        "scores_written": len(rows_out),
        "scored": scored,
        "insufficient": len(rows_out) - scored,
    }
    logger.info("hazards: %s", result.details)
    return result


async def _previous_levels(
    db: AsyncSession, reference_date: date
) -> dict[tuple[str, str], tuple[Optional[str], int]]:
    """Most recent prior level and streak per (region, hazard)."""
    stmt = (
        select(
            HazardScore.region_id,
            HazardScore.hazard,
            HazardScore.level,
            HazardScore.consecutive_periods,
            func.row_number()
            .over(
                partition_by=(HazardScore.region_id, HazardScore.hazard),
                order_by=desc(HazardScore.reference_date),
            )
            .label("rn"),
        )
        .where(HazardScore.reference_date < reference_date)
        .subquery()
    )
    rows = (await db.execute(select(stmt).where(stmt.c.rn == 1))).all()
    return {(r.region_id, r.hazard): (r.level, r.consecutive_periods or 0) for r in rows}


# ---------------------------------------------------------------------------
# Stage: alerts
# ---------------------------------------------------------------------------


async def evaluate_alerts(
    db: AsyncSession, *, reference_date: Optional[date] = None
) -> StageResult:
    """Phase 18 — turn hazard scores into deduplicated, hysteretic alerts."""
    result = StageResult(stage="alerts")
    reference_date = reference_date or datetime.now(timezone.utc).date()

    scores = (
        await db.execute(
            select(HazardScore).where(
                HazardScore.reference_date == reference_date,
                HazardScore.calculation_version == hazard_engine.HAZARD_VERSION,
            )
        )
    ).scalars().all()
    result.records_in = len(scores)

    open_alerts = {
        (a.region_id, a.hazard): a
        for a in (
            await db.execute(select(Alert).where(Alert.status == alert_rules.STATUS_ACTIVE))
        ).scalars().all()
    }
    resolved = {
        (a.region_id, a.hazard): a.resolved_at
        for a in (
            await db.execute(
                select(Alert).where(Alert.status == alert_rules.STATUS_RESOLVED)
            )
        ).scalars().all()
        if a.resolved_at
    }

    # Region naming for readable alerts, from the observation store.
    names = {
        r.region_id: (r.province, r.district)
        for r in (
            await db.execute(
                select(
                    Observation.region_id,
                    Observation.province,
                    Observation.district,
                ).distinct(Observation.region_id)
            )
        ).all()
    }

    raised = escalated = updated = closed = suppressed = 0

    for row in scores:
        outcome = hazard_engine.HazardResult(
            hazard=row.hazard,
            score=row.score,
            level=row.level,
            confidence=row.confidence,
            contributors=[],
            primary_driver=row.primary_driver,
            reason=row.reason or "",
            status=row.status,
        )
        existing = open_alerts.get((row.region_id, row.hazard))
        last_resolved = resolved.get((row.region_id, row.hazard))

        decision = alert_rules.evaluate(
            outcome,
            region_id=row.region_id,
            reference_date=reference_date,
            consecutive_periods=row.consecutive_periods,
            existing_severity=existing.severity if existing else None,
            existing_status=existing.status if existing else None,
            last_resolved_on=last_resolved.date() if last_resolved else None,
        )

        if not decision.should_write:
            if decision.suppressed_by:
                suppressed += 1
            continue

        province, district = names.get(row.region_id, (None, None))

        if decision.action == "deescalate" and existing is not None:
            existing.status = alert_rules.STATUS_RESOLVED
            existing.resolved_at = utcnow()
            existing.reason = decision.reason
            existing.updated_at = utcnow()
            closed += 1
            continue

        if existing is not None:
            existing.previous_severity = existing.severity
            existing.severity = decision.severity or existing.severity
            existing.score = decision.score
            existing.confidence = decision.confidence
            existing.reason = decision.reason
            existing.evidence = decision.evidence
            existing.rule_id = decision.rule_id
            existing.reference_date = reference_date
            existing.occurrence_count += 1
            existing.updated_at = utcnow()
            existing.expires_at = datetime.combine(
                alert_rules.expiry_for(reference_date),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            existing.dedupe_key = alert_rules.dedupe_key(
                row.hazard, row.region_id, existing.severity
            )
            if decision.action == "escalate":
                existing.escalation_count += 1
                escalated += 1
            else:
                updated += 1
            continue

        alert = Alert(
            alert_id=alert_rules.build_alert_id(row.hazard, row.region_id, reference_date),
            hazard=row.hazard,
            region_id=row.region_id,
            region_name=district or row.region_id,
            province=province,
            district=district,
            severity=decision.severity or alert_rules.SEVERITY_WATCH,
            status=alert_rules.STATUS_ACTIVE,
            score=decision.score,
            confidence=decision.confidence,
            reason=decision.reason,
            rule_id=decision.rule_id,
            evidence=decision.evidence,
            dedupe_key=alert_rules.dedupe_key(
                row.hazard, row.region_id, decision.severity or ""
            ),
            first_detected=reference_date,
            reference_date=reference_date,
            expires_at=datetime.combine(
                alert_rules.expiry_for(reference_date),
                datetime.min.time(),
                tzinfo=timezone.utc,
            ),
            calculation_version=alert_rules.ALERT_VERSION,
        )
        db.add(alert)
        raised += 1

    await db.commit()

    result.records_out = raised + escalated + updated
    result.details = {
        "raised": raised,
        "escalated": escalated,
        "updated": updated,
        "resolved": closed,
        "suppressed": suppressed,
    }
    logger.info("alerts: %s", result.details)
    return result


# ---------------------------------------------------------------------------
# Stage: daily brief
# ---------------------------------------------------------------------------


async def generate_brief(
    db: AsyncSession, *, reference_date: Optional[date] = None
) -> StageResult:
    """Phase 19 — situation summary assembled entirely from stored rows."""
    result = StageResult(stage="daily_brief")
    reference_date = reference_date or datetime.now(timezone.utc).date()

    risk_rows = (
        await db.execute(
            select(HazardScore)
            .where(
                HazardScore.hazard == hazard_engine.HAZARD_MULTI,
                HazardScore.reference_date == reference_date,
                HazardScore.score.isnot(None),
            )
            .order_by(desc(HazardScore.score))
            .limit(10)
        )
    ).scalars().all()

    active = (
        await db.execute(
            select(Alert.severity, func.count())
            .where(Alert.status == alert_rules.STATUS_ACTIVE)
            .group_by(Alert.severity)
        )
    ).all()
    active_by_severity = {r[0]: r[1] for r in active}
    active_total = sum(active_by_severity.values())

    new_alerts = await db.scalar(
        select(func.count()).select_from(Alert).where(Alert.first_detected == reference_date)
    ) or 0

    # Deterioration measured on the fused score's own history, not on raw
    # metrics — a region can have falling NDVI and still be at lower overall
    # risk, and the brief should reflect the thing operators act on.
    movement = await _risk_movement(db, reference_date)

    from app.intelligence.registry_service import dataset_health_counts

    healthy, degraded, health_detail = await dataset_health_counts(db, reference_date)

    critical = sum(
        1
        for r in risk_rows
        if r.level in (hazard_engine.RISK_CRITICAL, hazard_engine.RISK_HIGH)
    )

    headline = _headline(active_total, critical, len(movement["deteriorating"]), degraded)

    row = {
        "brief_date": reference_date,
        "generated_at": utcnow(),
        "headline": headline,
        "summary": {
            "alerts_by_severity": active_by_severity,
            "assessed_regions": len(risk_rows),
        },
        "active_alerts": active_total,
        "new_alerts": int(new_alerts),
        "critical_regions": critical,
        "deteriorating_regions": len(movement["deteriorating"]),
        "improving_regions": len(movement["improving"]),
        "datasets_healthy": healthy,
        "datasets_degraded": degraded,
        "top_risk_regions": {
            "items": [
                {
                    "region_id": r.region_id,
                    "score": r.score,
                    "level": r.level,
                    "confidence": r.confidence,
                    "primary_driver": r.primary_driver,
                }
                for r in risk_rows
            ]
        },
        "changes": movement,
        "data_health": health_detail,
        "calculation_version": BRIEF_VERSION,
    }

    stmt = pg_insert(DailyBrief).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["brief_date"],
        set_={k: v for k, v in row.items() if k != "brief_date"},
    )
    await db.execute(stmt)
    await db.commit()

    result.records_out = 1
    result.details = {
        "headline": headline,
        "active_alerts": active_total,
        "critical_regions": critical,
    }
    logger.info("daily_brief: %s", headline)
    return result


async def _risk_movement(db: AsyncSession, reference_date: date) -> dict[str, Any]:
    """Phase 21 — what changed since the previous scored cycle."""
    previous_date = await db.scalar(
        select(func.max(HazardScore.reference_date)).where(
            HazardScore.hazard == hazard_engine.HAZARD_MULTI,
            HazardScore.reference_date < reference_date,
        )
    )
    if previous_date is None:
        return {
            "deteriorating": [],
            "improving": [],
            "previous_date": None,
            "note": "no earlier cycle to compare against",
        }

    def scores_for(target: date):
        return select(HazardScore.region_id, HazardScore.score, HazardScore.level).where(
            HazardScore.hazard == hazard_engine.HAZARD_MULTI,
            HazardScore.reference_date == target,
            HazardScore.score.isnot(None),
        )

    now = {r.region_id: (r.score, r.level) for r in (await db.execute(scores_for(reference_date))).all()}
    before = {r.region_id: (r.score, r.level) for r in (await db.execute(scores_for(previous_date))).all()}

    deteriorating, improving = [], []
    for region_id, (score, level) in now.items():
        if region_id not in before:
            continue
        delta = score - before[region_id][0]
        # 5 points is the reporting floor: below that is scoring noise, and a
        # "what changed" panel listing every 0.4-point wobble is unreadable.
        if delta >= 5:
            deteriorating.append(
                {"region_id": region_id, "delta": round(delta, 1),
                 "from": before[region_id][1], "to": level, "score": round(score, 1)}
            )
        elif delta <= -5:
            improving.append(
                {"region_id": region_id, "delta": round(delta, 1),
                 "from": before[region_id][1], "to": level, "score": round(score, 1)}
            )

    deteriorating.sort(key=lambda x: x["delta"], reverse=True)
    improving.sort(key=lambda x: x["delta"])
    return {
        "deteriorating": deteriorating[:10],
        "improving": improving[:10],
        "previous_date": previous_date.isoformat(),
    }


def _headline(active: int, critical: int, deteriorating: int, degraded: int) -> str:
    if active == 0 and critical == 0:
        base = "No active hazard indicators."
    else:
        base = f"{active} active indicator(s); {critical} region(s) at high or critical risk."
    if deteriorating:
        base += f" {deteriorating} region(s) deteriorated since the last cycle."
    if degraded:
        base += f" {degraded} dataset(s) degraded."
    return base


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _upsert(
    db: AsyncSession, model, rows: list[dict[str, Any]], constraint: str
) -> None:
    """Chunked upsert on a named constraint, keeping every stage idempotent."""
    for start in range(0, len(rows), 400):
        chunk = rows[start : start + 400]
        stmt = pg_insert(model).values(chunk)
        keys = {c.name for c in model.__table__.primary_key.columns}
        update_cols = {
            col: stmt.excluded[col] for col in chunk[0] if col not in keys
        }
        await db.execute(
            stmt.on_conflict_do_update(constraint=constraint, set_=update_cols)
        )
    await db.commit()


def _delta(
    current: Optional[float], previous: Optional[float]
) -> tuple[Optional[float], Optional[float]]:
    """Absolute and percentage change, or (None, None).

    None rather than 0 when either side is missing: "we cannot compare" and
    "nothing changed" are different findings.
    """
    if current is None or previous is None:
        return None, None
    absolute = current - previous
    if abs(previous) < 1e-9:
        return absolute, None
    return absolute, (absolute / abs(previous)) * 100.0


def _direction(change_pct: Optional[float]) -> Optional[str]:
    if change_pct is None:
        return None
    if change_pct > 2.0:
        return "rising"
    if change_pct < -2.0:
        return "falling"
    return "stable"


def _round(value: Optional[float], places: int = 4) -> Optional[float]:
    return None if value is None else round(float(value), places)
