# packages/backend/app/intelligence/orchestrator.py
"""Phase 32 — the analytics cascade, run as recorded resumable stages.

Runs the post-ingestion chain:

    registry → data_quality → baselines → features → hazards → alerts → brief

Each stage is recorded in `gv_pipeline_stage_runs` before and after it executes,
which buys three things the brief asks for:

**Failure isolation.** A stage that raises is caught, recorded as failed, and
the cascade decides whether to continue. Downstream stages that genuinely
depend on it are skipped rather than run on absent inputs — computing hazards
from features that were never written would produce confident nonsense.

**Resumability.** Because completed stages are recorded, a re-run can skip what
already succeeded for that run id instead of redoing the whole chain.

**Observability.** The pipeline page reads these rows directly, so what the
dashboard shows is what actually executed, not a diagram of what should happen.

This module never ingests. Acquisition is slow, network-bound and lives in
GitHub Actions; the analytics cascade is fast and database-bound. Keeping them
separate is why the drought formula can be changed and re-run in seconds
without re-downloading a decade of imagery.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.databases.timestampdb.intelligence import PipelineStageRun
from app.databases.timestampdb.models import utcnow
from app.intelligence import analytics_service as analytics
from app.intelligence import event_service
from app.intelligence import registry_service

logger = logging.getLogger(__name__)

STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

STAGE_REGISTRY = "registry_sync"
STAGE_QUALITY = "data_quality"
STAGE_BASELINES = "baselines"
STAGE_FEATURES = "features"
STAGE_HAZARDS = "hazards"
STAGE_ALERTS = "alerts"
STAGE_BRIEF = "daily_brief"
STAGE_GEOMETRY = "geometry_stats"
STAGE_FLOOD = "flood_detection"
STAGE_EVENTS = "events"
STAGE_HOTSPOTS = "hotspots"
STAGE_RECOVERY = "recovery"

# Stage -> stages that must have succeeded first. Only real data dependencies
# are listed: quality scoring does not need the registry, so a registry failure
# must not block it.
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    STAGE_REGISTRY: (),
    STAGE_QUALITY: (),
    STAGE_GEOMETRY: (),
    STAGE_BASELINES: (),
    STAGE_FEATURES: (STAGE_BASELINES,),
    # Flood needs water-fraction features and region areas.
    STAGE_FLOOD: (STAGE_FEATURES, STAGE_GEOMETRY),
    STAGE_HAZARDS: (STAGE_FEATURES,),
    # Events consume every hazard score, flood included, so they run after
    # both scoring stages rather than after hazards alone.
    STAGE_EVENTS: (STAGE_HAZARDS,),
    STAGE_HOTSPOTS: (STAGE_HAZARDS, STAGE_GEOMETRY),
    STAGE_ALERTS: (STAGE_HAZARDS,),
    STAGE_RECOVERY: (STAGE_EVENTS,),
    STAGE_BRIEF: (),
}

STAGE_ORDER: tuple[str, ...] = (
    STAGE_REGISTRY,
    STAGE_QUALITY,
    STAGE_GEOMETRY,
    STAGE_BASELINES,
    STAGE_FEATURES,
    STAGE_FLOOD,
    STAGE_HAZARDS,
    STAGE_EVENTS,
    STAGE_HOTSPOTS,
    STAGE_ALERTS,
    STAGE_RECOVERY,
    STAGE_BRIEF,
)


@dataclass
class CycleResult:
    run_id: str
    reference_date: date
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str = STATUS_SUCCESS
    stages: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "reference_date": self.reference_date.isoformat(),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status,
            "stages": self.stages,
        }

    def render(self) -> str:
        lines = [
            "ANALYTICS CYCLE",
            "-" * 40,
            f"Run:  {self.run_id}",
            f"Date: {self.reference_date}",
            "",
        ]
        for stage in self.stages:
            marker = {
                STATUS_SUCCESS: "ok  ",
                STATUS_PARTIAL: "part",
                STATUS_FAILED: "FAIL",
                STATUS_SKIPPED: "skip",
            }.get(stage["status"], "?   ")
            lines.append(
                f"  [{marker}] {stage['stage']:<14} "
                f"{stage.get('duration_ms', 0):>6}ms  "
                f"in={stage.get('records_in', 0):<6} out={stage.get('records_out', 0)}"
            )
            if stage.get("error"):
                lines.append(f"           error: {stage['error']}")
            note = (stage.get("details") or {}).get("note")
            if note:
                lines.append(f"           note:  {note}")
        lines.append("")
        lines.append(f"Status: {self.status.upper()}")
        return "\n".join(lines)


def make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-analytics-{uuid.uuid4().hex[:6]}"


async def _record_stage(
    db: AsyncSession,
    *,
    run_id: str,
    stage: str,
    sequence: int,
    status: str,
    started: datetime,
    result: Optional[analytics.StageResult] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    finished = utcnow()
    duration_ms = int((finished - started).total_seconds() * 1000)

    values = {
        "run_id": run_id,
        "stage": stage,
        "sequence": sequence,
        "status": status,
        "started_at": started,
        "finished_at": finished,
        "duration_ms": duration_ms,
        "records_in": result.records_in if result else 0,
        "records_out": result.records_out if result else 0,
        "records_rejected": result.records_rejected if result else 0,
        "error": error,
        "details": result.details if result else None,
    }
    stmt = pg_insert(PipelineStageRun).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_stage_per_run",
        set_={k: v for k, v in values.items() if k not in ("run_id", "stage")},
    )
    await db.execute(stmt)
    await db.commit()

    payload = dict(values)
    payload["started_at"] = started.isoformat()
    payload["finished_at"] = finished.isoformat()
    return payload


async def completed_stages(db: AsyncSession, run_id: str) -> set[str]:
    """Stages already finished for this run, for resume."""
    rows = (
        await db.execute(
            select(PipelineStageRun.stage).where(
                PipelineStageRun.run_id == run_id,
                PipelineStageRun.status.in_([STATUS_SUCCESS, STATUS_PARTIAL]),
            )
        )
    ).scalars().all()
    return set(rows)


async def run_cycle(
    db: AsyncSession,
    *,
    reference_date: Optional[date] = None,
    run_id: Optional[str] = None,
    only: Optional[list[str]] = None,
    resume: bool = False,
) -> CycleResult:
    """Execute the analytics cascade.

    `resume=True` with an existing `run_id` skips stages that already
    succeeded — the Phase 3 requirement that a retry preserves completed work
    rather than redoing it.
    """
    reference_date = reference_date or datetime.now(timezone.utc).date()
    run_id = run_id or make_run_id()
    cycle = CycleResult(run_id=run_id, reference_date=reference_date, started_at=utcnow())

    already = await completed_stages(db, run_id) if resume else set()
    succeeded: set[str] = set(already)
    failed: set[str] = set()

    handlers: dict[str, Callable[[], Awaitable[analytics.StageResult]]] = {
        STAGE_REGISTRY: lambda: _wrap_registry(db, reference_date),
        STAGE_QUALITY: lambda: analytics.score_observation_quality(db, as_of=reference_date),
        STAGE_BASELINES: lambda: analytics.rebuild_baselines(db, up_to=reference_date),
        STAGE_FEATURES: lambda: analytics.compute_features(db, reference_date=reference_date),
        STAGE_HAZARDS: lambda: analytics.compute_hazards(db, reference_date=reference_date),
        STAGE_ALERTS: lambda: analytics.evaluate_alerts(db, reference_date=reference_date),
        STAGE_BRIEF: lambda: analytics.generate_brief(db, reference_date=reference_date),
        STAGE_GEOMETRY: lambda: event_service.sync_geometry_stats(db),
        STAGE_FLOOD: lambda: event_service.detect_floods(db, reference_date=reference_date),
        STAGE_EVENTS: lambda: event_service.update_events(db, reference_date=reference_date),
        STAGE_HOTSPOTS: lambda: event_service.detect_hotspots(db, reference_date=reference_date),
        STAGE_RECOVERY: lambda: event_service.compute_recovery(db, reference_date=reference_date),
    }

    for sequence, stage in enumerate(STAGE_ORDER):
        if only and stage not in only:
            continue

        if stage in already:
            cycle.stages.append(
                {"stage": stage, "status": STATUS_SKIPPED, "sequence": sequence,
                 "details": {"note": "already completed in this run"}}
            )
            continue

        # Dependency gate: never run a stage on inputs that were not produced.
        missing = [d for d in DEPENDENCIES.get(stage, ()) if d not in succeeded]
        if missing:
            started = utcnow()
            payload = await _record_stage(
                db, run_id=run_id, stage=stage, sequence=sequence,
                status=STATUS_SKIPPED, started=started,
                error=f"upstream stage(s) unavailable: {', '.join(missing)}",
            )
            payload["details"] = {"note": f"skipped: needs {', '.join(missing)}"}
            cycle.stages.append(payload)
            failed.add(stage)
            continue

        started = utcnow()
        try:
            result = await handlers[stage]()
            status = STATUS_PARTIAL if result.status == "partial" else STATUS_SUCCESS
            payload = await _record_stage(
                db, run_id=run_id, stage=stage, sequence=sequence,
                status=status, started=started, result=result,
            )
            succeeded.add(stage)
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            # A failing stage must not abort the cascade: the brief still has
            # value when hazards failed, and the registry sync is independent
            # of everything.
            logger.exception("analytics stage %s failed", stage)
            await db.rollback()
            payload = await _record_stage(
                db, run_id=run_id, stage=stage, sequence=sequence,
                status=STATUS_FAILED, started=started,
                error=f"{type(exc).__name__}: {exc}",
            )
            failed.add(stage)
        cycle.stages.append(payload)

    executed = [s for s in cycle.stages if s["status"] != STATUS_SKIPPED]
    if failed and not succeeded:
        cycle.status = STATUS_FAILED
    elif failed:
        cycle.status = STATUS_PARTIAL
    elif any(s["status"] == STATUS_PARTIAL for s in executed):
        cycle.status = STATUS_PARTIAL
    else:
        cycle.status = STATUS_SUCCESS

    cycle.finished_at = utcnow()
    logger.info("analytics cycle %s finished: %s", run_id, cycle.status)
    return cycle


async def _wrap_registry(db: AsyncSession, reference_date: date) -> analytics.StageResult:
    payload = await registry_service.sync_registry(db, as_of=reference_date)
    return analytics.StageResult(
        stage=STAGE_REGISTRY,
        records_out=payload["datasets"],
        details=payload,
    )
