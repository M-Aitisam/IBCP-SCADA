# packages/backend/app/api/v1/endpoints/ingestion.py
"""Ingestion control and monitoring.

Read endpoints are available to any signed-in user — seeing whether the data is
fresh is part of reading the dashboard. Write endpoints (run, backfill) require
the operator role: they consume Earth Engine quota and write to the observation
store, which is not something an ordinary viewer should be able to set off.

Nothing here executes ingestion in the request. See services/ingestion_control
for why dispatching to the existing scheduled workflow is the right shape.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_operator
from app.databases.timestampdb.models import IngestionRun
from app.databases.timestampdb.repository import TimestampRepository
from app.db.database import get_db
from app.db.models import User
from app.ingestion.config import ingestion_settings
from app.services import geovision_service as gv
from app.services import ingestion_control

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    """Trigger an incremental run."""

    datasets: Optional[list[str]] = Field(
        default=None,
        description="Limit to these datasets; omit for every enabled dataset.",
    )
    dry_run: bool = Field(
        default=False,
        description="Do everything except write to timestampdb.",
    )


class BackfillRequest(BaseModel):
    """Trigger a historical run over an explicit window."""

    datasets: Optional[list[str]] = None
    dry_run: bool = False
    start_date: Optional[date] = Field(
        default=None,
        description="Requested start. Defaults to GEE_HISTORICAL_START.",
    )
    end_date: Optional[date] = Field(
        default=None,
        description="Requested end. The real cutoff is whatever GEE actually holds.",
    )

    @model_validator(mode="after")
    def _ordered_window(self) -> "BackfillRequest":
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("start_date must not be after end_date")
        return self


# ---------------------------------------------------------------------------
# Status and history
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_status(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Per-dataset ingestion state, plus whatever is running right now.

    `latest_observation` is the real acquisition date from the observation
    store; `last_ingested_at` is when we wrote it. They are different events and
    are reported separately throughout.
    """
    catalog = await gv.dataset_catalog(db)
    repo = TimestampRepository(db)
    locks = await repo.active_locks()

    return {
        "status": "success",
        "datasets": [
            {
                "dataset": d["dataset"],
                "state": d["health"]["state"],
                "detail": d["health"]["detail"],
                "latest_observation": d["coverage"]["latest_observation"],
                "earliest_observation": d["coverage"]["earliest_observation"],
                "last_ingested_at": d["coverage"]["last_ingested_at"],
                "observations": d["coverage"]["observations"],
                "regions": d["coverage"]["regions"],
                "last_run_at": d["last_run_at"],
                "last_status": d["last_status"],
                "last_error": d["last_error"],
                "requested_until": d["requested_until"],
                "latest_available_at_source": d["latest_available_at_source"],
                "availability_status": d["availability_status"],
                "backfill_complete": d["backfill_complete"],
                "backfill_cursor": d["backfill_cursor"],
            }
            for d in catalog
        ],
        # A live lock is the only trustworthy signal that work is in flight; a
        # run row can say "running" long after its process died.
        "in_flight": [
            {
                "dataset": lock.lock_key,
                "run_id": lock.run_id,
                "mode": lock.mode,
                "acquired_at": lock.acquired_at.isoformat(),
                "lease_expires_at": lock.expires_at.isoformat(),
            }
            for lock in locks
        ],
        "recent_runs": await gv.recent_runs(db, limit=10),
        "configuration": {
            "historical_start": ingestion_settings.GEE_HISTORICAL_START.isoformat(),
            "requested_end": ingestion_settings.GEE_TARGET_END.isoformat(),
            "lookback_days": ingestion_settings.GEE_LOOKBACK_DAYS,
            "cloud_threshold_pct": ingestion_settings.GEE_CLOUD_THRESHOLD,
            "daily_enabled": ingestion_settings.GEE_DAILY_ENABLED,
            "max_retries": ingestion_settings.GEE_MAX_RETRIES,
            "remote_dispatch_configured": ingestion_control.dispatch_configured(),
        },
    }


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Recent ingestion runs, newest first."""
    return {
        "status": "success",
        "count": limit,
        "jobs": await gv.recent_runs(db, limit=limit),
    }


@router.get("/jobs/{run_id}")
async def get_job(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """One run in full, including its per-dataset breakdown."""
    run = await db.scalar(select(IngestionRun).where(IngestionRun.run_id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail=f"no ingestion run {run_id!r}")

    duration_ms = (
        int((run.finished_at - run.started_at).total_seconds() * 1000)
        if run.finished_at and run.started_at
        else None
    )
    return {
        "status": "success",
        "job": {
            "run_id": run.run_id,
            "mode": run.mode,
            "dry_run": run.dry_run,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "duration_ms": duration_ms,
            "datasets_attempted": run.datasets_attempted,
            "datasets_succeeded": run.datasets_succeeded,
            "datasets_failed": run.datasets_failed,
            "records_inserted": run.records_inserted,
            "records_updated": run.records_updated,
            "records_skipped": run.records_skipped,
            "records_rejected": run.records_rejected,
            "dataset_stats": run.dataset_stats,
            "latest_observation_per_dataset": run.latest_observation_per_dataset,
            "errors": run.errors,
        },
    }


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


async def _guard_in_flight(
    db: AsyncSession, datasets: Optional[list[str]]
) -> None:
    """Refuse to queue work that is already running.

    Belt and braces: the pipeline's own dataset locks make an overlapping run
    harmless, so this is not a correctness guard. It exists so an operator
    clicking twice gets told what is happening instead of silently queueing a
    second run that will skip everything and look like it did nothing.
    """
    repo = TimestampRepository(db)
    locks = await repo.active_locks()
    if not locks:
        return
    held = {lock.lock_key for lock in locks}
    clashing = held if not datasets else held & set(datasets)
    if clashing:
        raise HTTPException(
            status_code=409,
            detail=(
                "ingestion already in flight for: "
                + ", ".join(sorted(clashing))
                + ". Wait for it to finish, or target other datasets."
            ),
        )


@router.post("/run", status_code=202)
async def trigger_run(
    request: RunRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    """Start an incremental ingestion run.

    202, not 200: the work is queued elsewhere and has not happened yet by the
    time this responds. Poll /ingestion/status for progress.
    """
    await _guard_in_flight(db, request.datasets)
    try:
        result = await ingestion_control.dispatch_workflow(
            "daily", datasets=request.datasets, dry_run=request.dry_run
        )
    except ingestion_control.IngestionRequestError as exc:
        # The caller asked for something impossible; retrying will not help.
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ingestion_control.IngestionControlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return {"status": "accepted" if result.accepted else "not_configured", **result.to_json()}


@router.post("/backfill", status_code=202)
async def trigger_backfill(
    request: BackfillRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_operator),
):
    """Start a historical backfill.

    Resumable by design: the pipeline records a per-dataset cursor after each
    committed chunk, so an interrupted backfill continues from where it stopped
    rather than restarting at the beginning.

    `end_date` is a *request*, not a promise. The pipeline clamps it to what
    Earth Engine actually holds and reports the shortfall rather than filling
    it in.
    """
    await _guard_in_flight(db, request.datasets)
    try:
        result = await ingestion_control.dispatch_workflow(
            "backfill",
            datasets=request.datasets,
            dry_run=request.dry_run,
            start=request.start_date,
            end=request.end_date,
        )
    except ingestion_control.IngestionRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ingestion_control.IngestionControlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    return {
        "status": "accepted" if result.accepted else "not_configured",
        **result.to_json(),
        "requested_window": {
            "start": (request.start_date or ingestion_settings.GEE_HISTORICAL_START).isoformat(),
            "end": (request.end_date or ingestion_settings.GEE_TARGET_END).isoformat(),
            "note": (
                "Requested window. Actual coverage is bounded by what each "
                "collection publishes; see availability_status per dataset."
            ),
        },
    }
