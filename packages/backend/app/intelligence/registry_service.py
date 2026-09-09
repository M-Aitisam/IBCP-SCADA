# packages/backend/app/intelligence/registry_service.py
"""Phases 1 and 5 — dataset registry synchronisation and freshness.

`app.ingestion.registry` stays the single place a dataset is *defined*. This
module mirrors that definition into `gv_dataset_registry` and enriches it with
measured runtime state, so the dashboard reads dataset status from one indexed
table rather than recomputing coverage from the observation table per request.

The direction of that sync is deliberate and one-way. If the database were
allowed to define datasets, the pipeline could end up reducing over one
configuration while the dashboard described another — and the dashboard would
be the more convincing of the two while being wrong.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.databases.timestampdb.intelligence import DatasetRegistry
from app.databases.timestampdb.models import IngestionCheckpoint
from app.databases.timestampdb.models import SatelliteObservation as Observation
from app.ingestion.registry import DATASETS

logger = logging.getLogger(__name__)

REGISTRY_VERSION = "r1"

# --- Phase 5 freshness states --------------------------------------------
FRESH = "FRESH"
DELAYED = "DELAYED"
STALE = "STALE"
UNAVAILABLE = "UNAVAILABLE"

# Lag is judged in publication cycles, not absolute days, so a 16-day composite
# is not marked late for behaving like a 16-day composite.
DELAYED_CYCLES = 1.5
STALE_CYCLES = 3.0
# Floor for scene-based products whose cadence is an average, not a schedule:
# Sentinel-1 can legitimately be quiet over one area for several days.
MIN_CYCLE_DAYS = 5


@dataclass
class FreshnessResult:
    state: str
    lag_days: Optional[int]
    expected_latest: Optional[date]
    actual_latest: Optional[date]
    freshness_pct: Optional[float]
    detail: str

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "lag_days": self.lag_days,
            "expected_latest": self.expected_latest.isoformat() if self.expected_latest else None,
            "actual_latest": self.actual_latest.isoformat() if self.actual_latest else None,
            "freshness_pct": self.freshness_pct,
            "detail": self.detail,
        }


def assess_freshness(
    *,
    latest_observation: Optional[date],
    cadence_days: int,
    as_of: date,
    latest_available_at_source: Optional[date] = None,
) -> FreshnessResult:
    """Classify how far behind a dataset is.

    `latest_available_at_source` is what Earth Engine actually holds. When it is
    known it takes precedence over the calendar: a dataset that has ingested
    everything published is FRESH even if publication itself is running late.
    Judging us against a date the provider has not reached would report our
    pipeline as broken when the provider is simply slow.
    """
    cycle = max(MIN_CYCLE_DAYS, cadence_days)

    if latest_observation is None:
        return FreshnessResult(
            state=UNAVAILABLE, lag_days=None,
            expected_latest=latest_available_at_source, actual_latest=None,
            freshness_pct=0.0,
            detail="no observations stored for this dataset",
        )

    if latest_available_at_source is not None:
        reference = latest_available_at_source
        basis = "the newest observation published by the source"
    else:
        reference = as_of
        basis = "today"

    lag = (reference - latest_observation).days
    lag = max(0, lag)

    # 100% when current, decaying to 0 across the stale threshold.
    freshness_pct = max(0.0, min(100.0, 100.0 * (1.0 - lag / (cycle * STALE_CYCLES))))

    if lag <= cycle * DELAYED_CYCLES:
        state = FRESH
        detail = f"{lag} day(s) behind {basis}; within the {cycle}-day cycle"
    elif lag <= cycle * STALE_CYCLES:
        state = DELAYED
        detail = f"{lag} day(s) behind {basis}; beyond one publication cycle"
    else:
        state = STALE
        detail = f"{lag} day(s) behind {basis}; more than {STALE_CYCLES:g} cycles"

    return FreshnessResult(
        state=state, lag_days=lag,
        expected_latest=reference, actual_latest=latest_observation,
        freshness_pct=round(freshness_pct, 1), detail=detail,
    )


def next_expected_update(
    latest_observation: Optional[date], cadence_days: int
) -> Optional[date]:
    """When the next observation is due, on the product's own cadence."""
    if latest_observation is None:
        return None
    from datetime import timedelta

    return latest_observation + timedelta(days=max(1, cadence_days))


async def sync_registry(
    db: AsyncSession, *, as_of: Optional[date] = None
) -> dict[str, Any]:
    """Mirror configuration plus measured state into the registry table."""
    as_of = as_of or datetime.now(timezone.utc).date()

    coverage_stmt = select(
        Observation.dataset,
        func.count().label("records"),
        # Rows that actually carry a measurement. A scene's footprint covers
        # only part of the ROI, so reduceRegions legitimately returns null for
        # districts outside it — those rows record "we looked and there was
        # nothing", which is worth distinguishing from a usable reading.
        func.count(Observation.value).label("usable_records"),
        func.count(func.distinct(Observation.region_id)).label("regions"),
        func.min(Observation.observation_date).label("earliest"),
        func.max(Observation.observation_date).label("latest"),
        func.avg(Observation.quality_score).label("quality"),
    ).group_by(Observation.dataset)
    coverage = {r.dataset: r for r in (await db.execute(coverage_stmt)).all()}

    checkpoints = {
        c.dataset: c
        for c in (await db.execute(select(IngestionCheckpoint))).scalars().all()
    }

    total_regions = await db.scalar(
        select(func.count(func.distinct(Observation.region_id)))
    ) or 0

    from app.services.geovision_service import DATASET_PROVENANCE

    written = 0
    for name, config in DATASETS.items():
        stored = coverage.get(name)
        point = checkpoints.get(name)
        provenance = DATASET_PROVENANCE.get(name, {})

        latest_observation = stored.latest if stored else None
        freshness = assess_freshness(
            latest_observation=latest_observation,
            cadence_days=config.cadence.nominal_days,
            as_of=as_of,
            latest_available_at_source=point.latest_available_at_source if point else None,
        )

        status = "active" if stored and stored.records else "no_data"
        if point and point.last_status == "failed":
            status = "failing"
        elif freshness.state == STALE:
            status = "degraded"

        values = {
            "dataset_id": name,
            "name": provenance.get("platform") or name,
            "gee_collection": config.asset_id,
            "satellite": provenance.get("platform"),
            "sensor": provenance.get("platform"),
            "provider": provenance.get("provider"),
            "spatial_resolution_m": config.spatial_resolution,
            "temporal_resolution": config.cadence.value,
            "expected_update_days": config.cadence.nominal_days,
            "earliest_observation": stored.earliest if stored else None,
            "latest_available_date": point.latest_available_at_source if point else None,
            "latest_observation": latest_observation,
            "last_attempted_ingestion": point.last_run_at if point else None,
            "last_successful_ingestion": (
                point.last_run_at if point and point.last_status == "success" else None
            ),
            "status": status,
            "freshness_state": freshness.state,
            "lag_days": freshness.lag_days,
            "processing_method": _processing_method(config),
            "cloud_filter": (
                f"{config.cloud_property} <= {config.cloud_threshold}%"
                if config.cloud_property
                else None
            ),
            "bands": {"items": [b.band for b in config.bands]},
            "derived_indices": {"items": [d.metric for d in config.derived]},
            "region_count": stored.regions if stored else 0,
            "record_count": stored.records if stored else 0,
            "coverage_pct": (
                round(100.0 * stored.regions / total_regions, 1)
                if stored and total_regions
                else None
            ),
            "quality_score": (
                round(float(stored.quality), 1) if stored and stored.quality is not None else None
            ),
            "last_error": point.last_error if point else None,
            "registry_version": REGISTRY_VERSION,
            "extra_metadata": {
                "purpose": provenance.get("purpose"),
                "revisit": provenance.get("revisit"),
                "usable_records": stored.usable_records if stored else 0,
                # Share of stored rows with no measurement. High for
                # scene-based optical and SAR products, which is expected;
                # a sudden rise for a composite product is not.
                "null_rate_pct": (
                    round(100.0 * (stored.records - stored.usable_records) / stored.records, 1)
                    if stored and stored.records
                    else None
                ),
                "availability_status": point.availability_status if point else None,
                "backfill_complete": bool(point.backfill_complete) if point else False,
                "backfill_cursor": (
                    point.backfill_cursor.isoformat()
                    if point and point.backfill_cursor
                    else None
                ),
                "next_expected_update": (
                    d.isoformat()
                    if (d := next_expected_update(latest_observation, config.cadence.nominal_days))
                    else None
                ),
                "freshness": freshness.to_json(),
            },
            "updated_at": datetime.now(timezone.utc),
        }

        stmt = pg_insert(DatasetRegistry).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["dataset_id"],
            set_={k: v for k, v in values.items() if k != "dataset_id"},
        )
        await db.execute(stmt)
        written += 1

    await db.commit()
    logger.info("dataset registry: %d dataset(s) synchronised", written)
    return {"datasets": written, "as_of": as_of.isoformat()}


def _processing_method(config) -> str:
    """Human-readable processing chain, for the lineage panel."""
    steps = ["filterDate", "filterBounds"]
    if config.cloud_property:
        steps.append(f"cloud filter ({config.cloud_property})")
    if config.mask_strategy:
        steps.append(f"pixel mask ({config.mask_strategy})")
    if config.derived:
        steps.append("derive " + ", ".join(d.metric for d in config.derived))
    steps.append("scale factors + unit transform")
    steps.append("reduceRegions (regional aggregation)")
    steps.append("validation")
    steps.append("idempotent upsert")
    return " → ".join(steps)


async def dataset_health_counts(
    db: AsyncSession, as_of: date
) -> tuple[int, int, dict[str, Any]]:
    """(healthy, degraded, detail) for the daily brief and status bar."""
    rows = (await db.execute(select(DatasetRegistry))).scalars().all()
    healthy = sum(1 for r in rows if r.freshness_state == FRESH)
    degraded = sum(1 for r in rows if r.freshness_state in (DELAYED, STALE, UNAVAILABLE))
    detail = {
        "datasets": [
            {
                "dataset_id": r.dataset_id,
                "status": r.status,
                "freshness_state": r.freshness_state,
                "lag_days": r.lag_days,
                "latest_observation": r.latest_observation.isoformat()
                if r.latest_observation
                else None,
                "record_count": r.record_count,
            }
            for r in rows
        ]
    }
    return healthy, degraded, detail
