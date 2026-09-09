# packages/backend/app/api/v1/endpoints/intelligence.py
"""Intelligence API — hazards, alerts, briefs, lineage, pipeline observability.

Every endpoint reads rows the analytics cascade wrote. None of them compute a
hazard score at request time: scoring is a nightly job whose output is
versioned and auditable, and recomputing it per request would mean the number
on screen could differ from the number that raised an alert.

Read endpoints need a signed-in user. Anything that mutates state — running a
cycle, acknowledging an alert — requires the operator role and is written to
the audit log.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_operator
from app.databases.timestampdb.intelligence import (
    Alert,
    AuditLog,
    DailyBrief,
    DatasetRegistry,
    DerivedFeature,
    HazardScore,
    MetricBaseline,
    PipelineStageRun,
)
from app.databases.timestampdb.models import SatelliteObservation, utcnow
from app.db.database import get_db
from app.db.models import User
from app.intelligence import alerts as alert_rules
from app.intelligence import baselines as baseline_engine
from app.intelligence import hazards as hazard_engine
from app.intelligence import orchestrator
from app.intelligence import registry_service
from app.intelligence.analytics_service import FEATURE_VERSION
from app.ingestion.registry import DATASETS

router = APIRouter()

DISCLAIMER = alert_rules.DISCLAIMER


async def _latest_scored_date(db: AsyncSession, hazard: str) -> Optional[date]:
    """Newest cycle that actually produced scores for this hazard.

    Falling back to the newest date with any row would show an
    all-INSUFFICIENT_DATA cycle in preference to an older informative one.
    """
    return await db.scalar(
        select(func.max(HazardScore.reference_date)).where(
            HazardScore.hazard == hazard, HazardScore.score.isnot(None)
        )
    ) or await db.scalar(
        select(func.max(HazardScore.reference_date)).where(HazardScore.hazard == hazard)
    )


# ---------------------------------------------------------------------------
# Phase 25 — dataset catalogue from the registry
# ---------------------------------------------------------------------------


@router.get("/datasets")
async def list_datasets(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Every configured dataset with its measured runtime state.

    Read from `gv_dataset_registry`, which the cascade keeps current — the
    dashboard does not recompute coverage from the observation table.
    """
    rows = (
        await db.execute(select(DatasetRegistry).order_by(DatasetRegistry.dataset_id))
    ).scalars().all()

    return {
        "status": "success",
        "count": len(rows),
        "datasets": [
            {
                "dataset_id": r.dataset_id,
                "name": r.name,
                "gee_collection": r.gee_collection,
                "satellite": r.satellite,
                "provider": r.provider,
                "spatial_resolution_m": r.spatial_resolution_m,
                "temporal_resolution": r.temporal_resolution,
                "expected_update_days": r.expected_update_days,
                "earliest_observation": _iso(r.earliest_observation),
                "latest_observation": _iso(r.latest_observation),
                "latest_available_at_source": _iso(r.latest_available_date),
                "last_attempted_ingestion": _iso(r.last_attempted_ingestion),
                "last_successful_ingestion": _iso(r.last_successful_ingestion),
                "status": r.status,
                "freshness_state": r.freshness_state,
                "lag_days": r.lag_days,
                "processing_method": r.processing_method,
                "cloud_filter": r.cloud_filter,
                "bands": (r.bands or {}).get("items", []),
                "derived_indices": (r.derived_indices or {}).get("items", []),
                "region_count": r.region_count,
                "record_count": r.record_count,
                "coverage_pct": r.coverage_pct,
                "quality_score": r.quality_score,
                "failure_count": r.failure_count,
                "last_error": r.last_error,
                "metadata": r.extra_metadata,
                "updated_at": _iso(r.updated_at),
            }
            for r in rows
        ],
    }


@router.get("/freshness")
async def freshness(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Phase 5 — per-dataset lag against what the source has actually published."""
    rows = (await db.execute(select(DatasetRegistry))).scalars().all()
    states = [r.freshness_state for r in rows if r.freshness_state]

    return {
        "status": "success",
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "overall": (
            registry_service.STALE
            if registry_service.STALE in states
            else registry_service.DELAYED
            if registry_service.DELAYED in states
            else registry_service.FRESH
            if states
            else registry_service.UNAVAILABLE
        ),
        "datasets": [
            {
                "dataset_id": r.dataset_id,
                "state": r.freshness_state,
                "lag_days": r.lag_days,
                "latest_observation": _iso(r.latest_observation),
                "expected_latest": _iso(r.latest_available_date),
                "last_successful_ingestion": _iso(r.last_successful_ingestion),
                "next_expected_update": (r.extra_metadata or {}).get("next_expected_update"),
                "detail": ((r.extra_metadata or {}).get("freshness") or {}).get("detail"),
            }
            for r in rows
        ],
        "states": [
            registry_service.FRESH,
            registry_service.DELAYED,
            registry_service.STALE,
            registry_service.UNAVAILABLE,
        ],
    }


# ---------------------------------------------------------------------------
# Phases 13/20 — hazard scores for the map and ranking
# ---------------------------------------------------------------------------


@router.get("/hazards")
async def list_hazards(
    hazard: str = Query(hazard_engine.HAZARD_MULTI),
    reference_date: Optional[date] = Query(None),
    province: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    level: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Per-region hazard scores for one hazard and cycle."""
    target = reference_date or await _latest_scored_date(db, hazard)
    if target is None:
        return {
            "status": "success",
            "data_source": "no_data",
            "hazard": hazard,
            "reference_date": None,
            "count": 0,
            "regions": [],
            "detail": "no hazard scores have been computed yet",
        }

    stmt = select(HazardScore).where(
        HazardScore.hazard == hazard, HazardScore.reference_date == target
    )
    if min_score is not None:
        stmt = stmt.where(HazardScore.score >= min_score)
    if level:
        stmt = stmt.where(HazardScore.level == level)
    rows = (await db.execute(stmt.order_by(desc(HazardScore.score)))).scalars().all()

    geography = await _region_geography(db)
    if province:
        rows = [r for r in rows if geography.get(r.region_id, {}).get("province") == province]

    return {
        "status": "success",
        "data_source": "timestampdb" if rows else "no_data",
        "hazard": hazard,
        "reference_date": target.isoformat(),
        "count": len(rows),
        "regions": [
            {
                "region_id": r.region_id,
                **geography.get(r.region_id, {}),
                "score": r.score,
                "level": r.level,
                "confidence": r.confidence,
                "primary_driver": r.primary_driver,
                "reason": r.reason,
                "previous_level": r.previous_level,
                "consecutive_periods": r.consecutive_periods,
                "data_quality": r.data_quality,
                "status": r.status,
            }
            for r in rows
        ],
        "levels": {
            "hazard": [b[1] for b in hazard_engine.DROUGHT_BANDS],
            "risk": [b[1] for b in hazard_engine.RISK_BANDS],
        },
        "calculation_version": hazard_engine.HAZARD_VERSION,
        "disclaimer": DISCLAIMER,
    }


@router.get("/hazards/{region_id}")
async def region_hazards(
    region_id: str,
    reference_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Phase 14 — every hazard for one region, with full contributor breakdown.

    This is the "why is this region at risk?" answer: each score's inputs,
    their weights and their signed contributions, ranked by influence.
    """
    target = reference_date or await _latest_scored_date(db, hazard_engine.HAZARD_MULTI)
    if target is None:
        return {"status": "success", "data_source": "no_data", "region_id": region_id, "hazards": []}

    rows = (
        await db.execute(
            select(HazardScore).where(
                HazardScore.region_id == region_id,
                HazardScore.reference_date == target,
            )
        )
    ).scalars().all()

    features = (
        await db.execute(
            select(DerivedFeature).where(
                DerivedFeature.region_id == region_id,
                DerivedFeature.reference_date == target,
            )
        )
    ).scalars().all()

    geography = await _region_geography(db)

    return {
        "status": "success",
        "data_source": "timestampdb" if rows else "no_data",
        "region_id": region_id,
        "region": geography.get(region_id, {}),
        "reference_date": target.isoformat(),
        "hazards": [
            {
                "hazard": r.hazard,
                "score": r.score,
                "level": r.level,
                "confidence": r.confidence,
                "primary_driver": r.primary_driver,
                "reason": r.reason,
                "status": r.status,
                # Ranked by contribution so the answer leads with what drove it.
                "contributors": sorted(
                    (r.contributors or {}).get("items", []),
                    key=lambda c: c.get("contribution") or 0,
                    reverse=True,
                ),
                "previous_level": r.previous_level,
                "consecutive_periods": r.consecutive_periods,
                "calculation_version": r.calculation_version,
            }
            for r in rows
        ],
        "features": [_feature_json(f) for f in features],
        "classification": "satellite-derived analytical indicator",
        "is_official_warning": False,
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Phase 18 — alerts
# ---------------------------------------------------------------------------


@router.get("/alerts")
async def list_alerts(
    status: str = Query("active"),
    hazard: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Satellite-derived early-warning indicators. NOT official warnings."""
    stmt = select(Alert)
    if status != "all":
        stmt = stmt.where(Alert.status == status)
    if hazard:
        stmt = stmt.where(Alert.hazard == hazard)
    if severity:
        stmt = stmt.where(Alert.severity == severity)

    rows = (
        await db.execute(stmt.order_by(desc(Alert.reference_date), desc(Alert.score)).limit(limit))
    ).scalars().all()

    counts = {
        r[0]: r[1]
        for r in (
            await db.execute(
                select(Alert.severity, func.count())
                .where(Alert.status == alert_rules.STATUS_ACTIVE)
                .group_by(Alert.severity)
            )
        ).all()
    }

    return {
        "status": "success",
        "count": len(rows),
        "active_by_severity": counts,
        "alerts": [_alert_json(a) for a in rows],
        "severities": list(alert_rules.SEVERITY_RANK),
        "disclaimer": DISCLAIMER,
    }


class AcknowledgeRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    request: AcknowledgeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_operator),
):
    """Mark an alert as seen by an operator. Audited."""
    alert = await db.scalar(select(Alert).where(Alert.alert_id == alert_id))
    if alert is None:
        raise HTTPException(status_code=404, detail=f"no alert {alert_id!r}")

    alert.acknowledged_at = utcnow()
    alert.acknowledged_by = user.username
    alert.updated_at = utcnow()

    db.add(
        AuditLog(
            actor=user.username,
            actor_role=user.role,
            action="alert.acknowledge",
            entity_type="alert",
            entity_id=alert_id,
            detail={"note": request.note, "severity": alert.severity},
        )
    )
    await db.commit()
    return {"status": "success", "alert": _alert_json(alert)}


# ---------------------------------------------------------------------------
# Phases 19/21 — daily brief and what changed
# ---------------------------------------------------------------------------


@router.get("/brief")
async def get_brief(
    brief_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Phase 19 — the stored situation summary for a day.

    Stored rather than regenerated: a brief is a record of what the system
    believed on that date, and rebuilding it against newer data would quietly
    rewrite history.
    """
    if brief_date is None:
        brief_date = await db.scalar(select(func.max(DailyBrief.brief_date)))
    if brief_date is None:
        return {
            "status": "success",
            "data_source": "no_data",
            "brief": None,
            "detail": "no brief has been generated yet",
        }

    row = await db.scalar(select(DailyBrief).where(DailyBrief.brief_date == brief_date))
    if row is None:
        raise HTTPException(status_code=404, detail=f"no brief for {brief_date}")

    return {
        "status": "success",
        "data_source": "timestampdb",
        "brief": {
            "brief_date": row.brief_date.isoformat(),
            "generated_at": _iso(row.generated_at),
            "headline": row.headline,
            "summary": row.summary,
            "active_alerts": row.active_alerts,
            "new_alerts": row.new_alerts,
            "critical_regions": row.critical_regions,
            "deteriorating_regions": row.deteriorating_regions,
            "improving_regions": row.improving_regions,
            "datasets_healthy": row.datasets_healthy,
            "datasets_degraded": row.datasets_degraded,
            "top_risk_regions": (row.top_risk_regions or {}).get("items", []),
            "changes": row.changes,
            "data_health": row.data_health,
            "calculation_version": row.calculation_version,
        },
        "disclaimer": DISCLAIMER,
    }


@router.get("/changes")
async def what_changed(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Phase 21 — significant movement since the previous scored cycle."""
    latest = await db.scalar(select(func.max(DailyBrief.brief_date)))
    if latest is None:
        return {"status": "success", "data_source": "no_data", "changes": None}

    row = await db.scalar(select(DailyBrief).where(DailyBrief.brief_date == latest))
    changes = (row.changes or {}) if row else {}
    geography = await _region_geography(db)

    def decorate(items):
        return [{**item, **geography.get(item.get("region_id"), {})} for item in items or []]

    return {
        "status": "success",
        "data_source": "timestampdb",
        "reference_date": latest.isoformat(),
        "previous_date": changes.get("previous_date"),
        "deteriorating": decorate(changes.get("deteriorating")),
        "improving": decorate(changes.get("improving")),
        "new_alerts": row.new_alerts if row else 0,
        "datasets_degraded": row.datasets_degraded if row else 0,
        "note": changes.get("note"),
    }


# ---------------------------------------------------------------------------
# Phase 23 — pipeline observability
# ---------------------------------------------------------------------------


@router.get("/pipeline")
async def pipeline_status(
    limit: int = Query(5, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Recent analytics cycles, stage by stage.

    Rendered as the pipeline page. These are rows the orchestrator actually
    wrote, so the diagram reflects what executed rather than what was intended.
    """
    recent_runs = (
        await db.execute(
            select(PipelineStageRun.run_id, func.max(PipelineStageRun.started_at).label("started"))
            .group_by(PipelineStageRun.run_id)
            .order_by(desc("started"))
            .limit(limit)
        )
    ).all()
    run_ids = [r.run_id for r in recent_runs]

    stages = []
    if run_ids:
        stages = (
            await db.execute(
                select(PipelineStageRun)
                .where(PipelineStageRun.run_id.in_(run_ids))
                .order_by(desc(PipelineStageRun.started_at), PipelineStageRun.sequence)
            )
        ).scalars().all()

    by_run: dict[str, list[dict[str, Any]]] = {}
    for stage in stages:
        by_run.setdefault(stage.run_id, []).append(
            {
                "stage": stage.stage,
                "sequence": stage.sequence,
                "status": stage.status,
                "started_at": _iso(stage.started_at),
                "finished_at": _iso(stage.finished_at),
                "duration_ms": stage.duration_ms,
                "records_in": stage.records_in,
                "records_out": stage.records_out,
                "records_rejected": stage.records_rejected,
                "error": stage.error,
                "details": stage.details,
            }
        )

    return {
        "status": "success",
        "stage_order": list(orchestrator.STAGE_ORDER),
        "dependencies": {k: list(v) for k, v in orchestrator.DEPENDENCIES.items()},
        "runs": [
            {
                "run_id": run_id,
                "stages": sorted(by_run.get(run_id, []), key=lambda s: s["sequence"]),
                "status": _run_status(by_run.get(run_id, [])),
            }
            for run_id in run_ids
        ],
    }


class RunCycleRequest(BaseModel):
    reference_date: Optional[date] = None
    only: Optional[list[str]] = None
    resume_run_id: Optional[str] = None


@router.post("/run-cycle", status_code=202)
async def run_cycle(
    request: RunCycleRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_operator),
):
    """Phase 32 — run the analytics cascade now.

    Safe to expose over HTTP, unlike ingestion: this reads and writes the
    database only, takes tens of seconds rather than hours, and every stage is
    an idempotent upsert. It never contacts Earth Engine.
    """
    result = await orchestrator.run_cycle(
        db,
        reference_date=request.reference_date,
        run_id=request.resume_run_id,
        only=request.only,
        resume=bool(request.resume_run_id),
    )
    db.add(
        AuditLog(
            actor=user.username,
            actor_role=user.role,
            action="analytics.run_cycle",
            entity_type="analytics_run",
            entity_id=result.run_id,
            outcome=result.status,
            detail={"stages": [s["stage"] for s in result.stages]},
        )
    )
    await db.commit()
    return {"status": "accepted", **result.to_json()}


# ---------------------------------------------------------------------------
# Phase 6 — data lineage
# ---------------------------------------------------------------------------


@router.get("/lineage")
async def lineage(
    region_id: str = Query(...),
    metric: str = Query(...),
    reference_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Full provenance for one displayed figure.

    Walks the chain backwards: the dashboard metric, the derived feature that
    produced it, the observations behind that, the dataset and GEE collection
    they came from, and the processing applied at each step.

    Assembled on demand rather than stored. A lineage row per displayed metric
    would duplicate what the registry and observation tables already record,
    and duplicated provenance is provenance that can disagree with itself.
    """
    feature_stmt = select(DerivedFeature).where(
        DerivedFeature.region_id == region_id,
        DerivedFeature.metric == metric,
        DerivedFeature.calculation_version == FEATURE_VERSION,
    )
    if reference_date:
        feature_stmt = feature_stmt.where(DerivedFeature.reference_date == reference_date)
    feature = (
        await db.execute(feature_stmt.order_by(desc(DerivedFeature.reference_date)).limit(1))
    ).scalars().first()

    if feature is None:
        return {
            "status": "success",
            "data_source": "no_data",
            "region_id": region_id,
            "metric": metric,
            "detail": "no derived feature stored for this region and metric",
        }

    registry = await db.scalar(
        select(DatasetRegistry).where(DatasetRegistry.dataset_id == feature.dataset)
    )
    config = DATASETS.get(feature.dataset)

    observations = (
        await db.execute(
            select(SatelliteObservation)
            .where(
                SatelliteObservation.region_id == region_id,
                SatelliteObservation.metric == metric,
                SatelliteObservation.dataset == feature.dataset,
                SatelliteObservation.value.isnot(None),
            )
            .order_by(desc(SatelliteObservation.observation_date))
            .limit(5)
        )
    ).scalars().all()

    baseline = await baseline_engine.load_baseline(
        db, region_id=region_id, metric=metric, reference=feature.reference_date
    )

    return {
        "status": "success",
        "data_source": "timestampdb",
        "metric": metric,
        "region_id": region_id,
        "value": feature.current_value,
        "observation_date": _iso(feature.observation_date),
        # The chain, in the order a reader would ask about it.
        "chain": [
            {
                "step": "dashboard_metric",
                "detail": f"{metric} for region {region_id}",
                "value": feature.current_value,
            },
            {
                "step": "derived_feature",
                "detail": (
                    f"windowed regional aggregate over {feature.observation_count} "
                    f"observation(s), reference {feature.reference_date}"
                ),
                "calculation_version": feature.calculation_version,
                "computed_at": _iso(feature.computed_at),
            },
            {
                "step": "baseline",
                "detail": (
                    f"{baseline.sample_years} year(s) of history, "
                    f"{'sufficient' if baseline.is_sufficient else 'BELOW minimum'}"
                    if baseline
                    else "no baseline stored for this period"
                ),
                "calculation_version": baseline.calculation_version if baseline else None,
            },
            {
                "step": "observations",
                "detail": f"{len(observations)} most recent source observation(s)",
                "items": [
                    {
                        "observation_date": _iso(o.observation_date),
                        "observation_timestamp": _iso(o.observation_timestamp),
                        "ingested_at": _iso(o.ingested_at),
                        "value": o.value,
                        "unit": o.unit,
                        "source_image_id": o.source_image_id,
                        "cloud_percentage": o.cloud_percentage,
                        "pixel_count": o.pixel_count,
                        "quality_score": o.quality_score,
                        "quality_status": o.quality_status,
                        "quality_reason": o.quality_reason,
                    }
                    for o in observations
                ],
            },
            {
                "step": "dataset",
                "detail": registry.name if registry else feature.dataset,
                "dataset_id": feature.dataset,
                "gee_collection": registry.gee_collection if registry else (
                    config.asset_id if config else None
                ),
                "provider": registry.provider if registry else None,
                "spatial_resolution_m": registry.spatial_resolution_m if registry else None,
                "temporal_resolution": registry.temporal_resolution if registry else None,
            },
            {
                "step": "processing",
                "detail": registry.processing_method if registry else None,
                "cloud_filter": registry.cloud_filter if registry else None,
            },
        ],
        "quality": {
            "feature_quality_score": feature.quality_score,
            "status": feature.status,
        },
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _region_geography(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """region_id -> {name, province, district}, from the observation store."""
    rows = (
        await db.execute(
            select(
                SatelliteObservation.region_id,
                SatelliteObservation.province,
                SatelliteObservation.district,
                SatelliteObservation.tehsil,
            ).distinct(SatelliteObservation.region_id)
        )
    ).all()
    return {
        r.region_id: {
            "province": r.province,
            "district": r.district,
            "tehsil": r.tehsil,
            "name": r.tehsil or r.district or r.region_id,
        }
        for r in rows
    }


def _alert_json(a: Alert) -> dict[str, Any]:
    return {
        "alert_id": a.alert_id,
        "hazard": a.hazard,
        "region_id": a.region_id,
        "region_name": a.region_name,
        "province": a.province,
        "district": a.district,
        "severity": a.severity,
        "previous_severity": a.previous_severity,
        "status": a.status,
        "score": a.score,
        "confidence": a.confidence,
        "reason": a.reason,
        "rule_id": a.rule_id,
        "evidence": a.evidence,
        "occurrence_count": a.occurrence_count,
        "escalation_count": a.escalation_count,
        "first_detected": _iso(a.first_detected),
        "reference_date": _iso(a.reference_date),
        "created_at": _iso(a.created_at),
        "updated_at": _iso(a.updated_at),
        "expires_at": _iso(a.expires_at),
        "acknowledged_at": _iso(a.acknowledged_at),
        "acknowledged_by": a.acknowledged_by,
        "classification": "satellite-derived analytical indicator",
        "is_official_warning": False,
    }


def _feature_json(f: DerivedFeature) -> dict[str, Any]:
    return {
        "metric": f.metric,
        "dataset": f.dataset,
        "current_value": f.current_value,
        "observation_date": _iso(f.observation_date),
        "change_7d_pct": f.change_7d_pct,
        "change_30d_pct": f.change_30d_pct,
        "change_yoy_pct": f.change_yoy_pct,
        "baseline_value": f.baseline_value,
        "baseline_years": f.baseline_years,
        "seasonal_anomaly": f.seasonal_anomaly,
        "z_score": f.z_score,
        "percentile": f.percentile,
        "trend_direction": f.trend_direction,
        "quality_score": f.quality_score,
        "status": f.status,
    }


def _run_status(stages: list[dict[str, Any]]) -> str:
    if not stages:
        return "unknown"
    statuses = {s["status"] for s in stages}
    if "failed" in statuses:
        return "partial" if {"success", "partial"} & statuses else "failed"
    if "partial" in statuses:
        return "partial"
    return "success"


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None
