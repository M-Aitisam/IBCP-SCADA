# packages/backend/app/databases/timestampdb/repository.py
"""Write/read access to the timestampdb observation store.

Everything the pipeline persists goes through this module. Two implementations
share one interface:

  TimestampRepository     - real Postgres/TimescaleDB writes via bulk upsert
  DryRunRepository        - counts what *would* be written, touches nothing

Bulk upsert uses Postgres ON CONFLICT against the composite primary key
(observation_timestamp, observation_key), which is the deterministic natural
key. Re-running the pipeline over the same source images is therefore a no-op
in terms of row count, no matter how many times it happens.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional, Protocol, Sequence

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.databases.timestampdb.models import (
    IngestionCheckpoint,
    IngestionLock,
    IngestionRun,
    GeeBackfillProgress,
    SatelliteObservation,
    build_observation_key,
    utcnow,
)

logger = logging.getLogger(__name__)

# How long a lock survives without renewal. Long enough that a slow GEE chunk
# never loses its lease, short enough that a crashed run frees the dataset the
# same night rather than blocking the next cron.
DEFAULT_LOCK_LEASE_SECONDS = 300

# Rows per INSERT ... ON CONFLICT statement. Keeps parameter counts well under
# the Postgres 65535-bind limit given ~30 columns per row.
UPSERT_BATCH_SIZE = 500


@dataclass
class ObservationRecord:
    """Normalized, validated observation ready for storage.

    This is the pipeline's internal currency: extractors produce it, the
    validator screens it, the repository writes it.
    """

    dataset: str
    dataset_asset_id: str
    region_id: str
    metric: str
    observation_timestamp: datetime
    observation_date: date
    source_image_id: str
    unit: str

    dataset_version: Optional[str] = None
    region_type: str = "tehsil"
    province: Optional[str] = None
    district: Optional[str] = None
    tehsil: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    band: Optional[str] = None
    derived_metric: Optional[str] = None

    value: Optional[float] = None
    scale_factor: float = 1.0

    min_value: Optional[float] = None
    max_value: Optional[float] = None
    mean_value: Optional[float] = None
    median_value: Optional[float] = None
    pixel_count: Optional[int] = None

    quality_flag: Optional[str] = None
    cloud_percentage: Optional[float] = None
    source_product_id: Optional[str] = None
    spatial_resolution: Optional[float] = None
    acquisition_metadata: Optional[dict] = None
    processing_status: str = "stored"

    @property
    def observation_key(self) -> str:
        return build_observation_key(
            self.dataset, self.source_image_id, self.region_id, self.metric
        )

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["observation_key"] = self.observation_key
        row["ingested_at"] = utcnow()
        return row


@dataclass
class WriteResult:
    """Outcome of one bulk write."""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0

    def merge(self, other: "WriteResult") -> "WriteResult":
        return WriteResult(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            skipped=self.skipped + other.skipped,
        )


class ObservationStore(Protocol):
    """Interface the pipeline depends on, so dry-run and tests can substitute."""

    async def upsert_observations(
        self, records: Sequence[ObservationRecord]
    ) -> WriteResult: ...

    async def get_checkpoint(self, dataset: str) -> Optional[IngestionCheckpoint]: ...

    async def save_checkpoint(self, dataset: str, **fields: Any) -> None: ...

    async def start_run(self, run_id: str, mode: str, dry_run: bool) -> None: ...

    async def finish_run(self, run_id: str, **fields: Any) -> None: ...

    async def acquire_lock(
        self, lock_key: str, run_id: str, mode: Optional[str] = None, **kwargs: Any
    ) -> bool: ...

    async def renew_lock(self, lock_key: str, run_id: str, **kwargs: Any) -> bool: ...

    async def release_lock(self, lock_key: str, run_id: str) -> None: ...


class TimestampRepository:
    """Real writes against the timestampdb tables."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------------- observations ----------------

    async def upsert_observations(
        self, records: Sequence[ObservationRecord]
    ) -> WriteResult:
        if not records:
            return WriteResult()

        # Collapse in-batch duplicates first. Postgres raises
        # "ON CONFLICT DO UPDATE command cannot affect row a second time" if
        # the same key appears twice in one statement, which can legitimately
        # happen when a region is covered by two tiles of the same image.
        deduped: dict[tuple[datetime, str], dict[str, Any]] = {}
        in_batch_dupes = 0
        for record in records:
            key = (record.observation_timestamp, record.observation_key)
            if key in deduped:
                in_batch_dupes += 1
            deduped[key] = record.to_row()

        rows = list(deduped.values())
        result = WriteResult(skipped=in_batch_dupes)

        for start in range(0, len(rows), UPSERT_BATCH_SIZE):
            chunk = rows[start : start + UPSERT_BATCH_SIZE]
            result = result.merge(await self._upsert_chunk(chunk))

        await self.session.commit()
        return result

    async def _upsert_chunk(self, chunk: list[dict[str, Any]]) -> WriteResult:
        """Upsert one batch, reporting how many rows were new vs refreshed.

        ON CONFLICT DO UPDATE returns every affected row without saying which
        branch it took, so the insert/update split comes from a pre-check
        SELECT. That is one extra cheap indexed query per batch, negligible
        next to the GEE round-trip that produced the data.
        """
        keys = [(r["observation_timestamp"], r["observation_key"]) for r in chunk]
        preexisting = await self.existing_keys(keys)

        stmt = pg_insert(SatelliteObservation).values(chunk)
        # Refresh everything except the key columns, so a corrected re-run
        # overwrites stale values; ingested_at is bumped to record the refresh.
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in SatelliteObservation.__table__.columns
            if col.name not in ("observation_timestamp", "observation_key")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["observation_timestamp", "observation_key"],
            set_=update_cols,
        )
        await self.session.execute(stmt)

        updated = len(preexisting)
        return WriteResult(inserted=len(chunk) - updated, updated=updated)

    async def latest_observation_date(self, dataset: str) -> Optional[date]:
        return await self.session.scalar(
            select(func.max(SatelliteObservation.observation_date)).where(
                SatelliteObservation.dataset == dataset
            )
        )

    async def count_observations(self, dataset: Optional[str] = None) -> int:
        stmt = select(func.count()).select_from(SatelliteObservation)
        if dataset:
            stmt = stmt.where(SatelliteObservation.dataset == dataset)
        return await self.session.scalar(stmt) or 0

    async def existing_keys(
        self, keys: Iterable[tuple[datetime, str]]
    ) -> set[tuple[datetime, str]]:
        """Which of these (timestamp, key) pairs are already stored."""
        key_list = list(keys)
        if not key_list:
            return set()
        found: set[tuple[datetime, str]] = set()
        for start in range(0, len(key_list), UPSERT_BATCH_SIZE):
            chunk = key_list[start : start + UPSERT_BATCH_SIZE]
            rows = (
                await self.session.execute(
                    select(
                        SatelliteObservation.observation_timestamp,
                        SatelliteObservation.observation_key,
                    ).where(
                        SatelliteObservation.observation_key.in_([k[1] for k in chunk])
                    )
                )
            ).all()
            found.update((r[0], r[1]) for r in rows)
        return found

    # ---------------- checkpoints ----------------

    async def get_checkpoint(self, dataset: str) -> Optional[IngestionCheckpoint]:
        # populate_existing forces a re-read instead of returning the instance
        # already in the session's identity map. The sessionmaker sets
        # expire_on_commit=False, so without this a get() following a
        # save_checkpoint() in the same session hands back pre-update values —
        # which would make the pipeline resume from a stale watermark.
        return await self.session.get(
            IngestionCheckpoint, dataset, populate_existing=True
        )

    async def save_checkpoint(self, dataset: str, **fields: Any) -> None:
        values = {"dataset": dataset, **fields}
        stmt = pg_insert(IngestionCheckpoint).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["dataset"],
            set_={k: v for k, v in values.items() if k != "dataset"},
        )
        await self.session.execute(stmt)
        await self.session.commit()

    # ---------------- locks ----------------

    async def acquire_lock(
        self,
        lock_key: str,
        run_id: str,
        mode: Optional[str] = None,
        holder: Optional[str] = None,
        lease_seconds: int = DEFAULT_LOCK_LEASE_SECONDS,
    ) -> bool:
        """Take the lease for `lock_key`, or return False if someone holds it.

        The whole decision is one statement. A read-then-write in Python would
        race: two runs starting together would both see no lock and both
        proceed. `ON CONFLICT ... DO UPDATE ... WHERE expires_at <= now()` lets
        Postgres arbitrate — the conflicting UPDATE is skipped when the lease is
        still live, so RETURNING yields no row and the caller backs off.
        """
        now = utcnow()
        expires = now + timedelta(seconds=lease_seconds)
        values = {
            "lock_key": lock_key,
            "run_id": run_id,
            "mode": mode,
            "holder": holder,
            "acquired_at": now,
            "expires_at": expires,
        }
        stmt = pg_insert(IngestionLock).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["lock_key"],
            set_={k: v for k, v in values.items() if k != "lock_key"},
            # Reclaim only an expired lease; never steal a live one.
            where=IngestionLock.expires_at <= now,
        ).returning(IngestionLock.lock_key)

        acquired = (await self.session.execute(stmt)).scalar_one_or_none() is not None
        await self.session.commit()
        if not acquired:
            logger.warning(
                "lock %s is held by another run; skipping to avoid duplicate work",
                lock_key,
            )
        return acquired

    async def renew_lock(
        self,
        lock_key: str,
        run_id: str,
        lease_seconds: int = DEFAULT_LOCK_LEASE_SECONDS,
    ) -> bool:
        """Extend our own lease. False means we no longer hold it."""
        stmt = (
            sa_update(IngestionLock)
            .where(IngestionLock.lock_key == lock_key, IngestionLock.run_id == run_id)
            .values(expires_at=utcnow() + timedelta(seconds=lease_seconds))
            .returning(IngestionLock.lock_key)
        )
        held = (await self.session.execute(stmt)).scalar_one_or_none() is not None
        await self.session.commit()
        return held

    async def release_lock(self, lock_key: str, run_id: str) -> None:
        """Release only if we still own it, so a reclaimer is never evicted."""
        await self.session.execute(
            sa_delete(IngestionLock).where(
                IngestionLock.lock_key == lock_key, IngestionLock.run_id == run_id
            )
        )
        await self.session.commit()

    async def active_locks(self) -> list[IngestionLock]:
        """Locks whose lease has not expired — i.e. work believed in flight."""
        rows = await self.session.execute(
            select(IngestionLock).where(IngestionLock.expires_at > utcnow())
        )
        return list(rows.scalars().all())

    async def reap_abandoned_runs(self) -> int:
        """Close out runs left `running` by a process that died.

        A run is abandoned when it is still marked running, started long enough
        ago to be past any plausible lease, and holds no live lock. Marking them
        keeps the ingestion history honest instead of showing a phantom job
        forever — which is exactly the state this database was found in.
        """
        cutoff = utcnow() - timedelta(seconds=DEFAULT_LOCK_LEASE_SECONDS)
        live = select(IngestionLock.run_id).where(IngestionLock.expires_at > utcnow())
        stmt = (
            sa_update(IngestionRun)
            .where(
                IngestionRun.status == "running",
                IngestionRun.started_at < cutoff,
                IngestionRun.run_id.notin_(live),
            )
            .values(status="abandoned", finished_at=utcnow())
            .returning(IngestionRun.run_id)
        )
        reaped = (await self.session.execute(stmt)).scalars().all()
        await self.session.commit()
        if reaped:
            logger.info("marked %d abandoned ingestion run(s)", len(reaped))
        return len(reaped)

    # ---------------- automated backfill queue ----------------

    async def plan_backfill(self, rows: Sequence[dict[str, Any]]) -> int:
        if not rows:
            return 0
        stmt = pg_insert(GeeBackfillProgress).values(rows).on_conflict_do_nothing(
            index_elements=["dataset", "chunk_start"]
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount or 0

    async def backfill_progress(
        self, dataset: Optional[str] = None
    ) -> list[GeeBackfillProgress]:
        stmt = select(GeeBackfillProgress).order_by(
            GeeBackfillProgress.chunk_start, GeeBackfillProgress.dataset
        )
        if dataset:
            stmt = stmt.where(GeeBackfillProgress.dataset == dataset)
        return list((await self.session.execute(stmt)).scalars().all())

    async def claim_next_backfill(self) -> Optional[GeeBackfillProgress]:
        eligible = or_(
            GeeBackfillProgress.status == "pending",
            (GeeBackfillProgress.status == "failed")
            & (GeeBackfillProgress.attempts < 3),
        )
        row = await self.session.scalar(
            select(GeeBackfillProgress)
            .where(eligible)
            .order_by(GeeBackfillProgress.chunk_start, GeeBackfillProgress.dataset)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            await self.session.rollback()
            return None
        row.status = "running"
        row.attempts += 1
        row.started_at = utcnow()
        row.error = None
        await self.session.commit()
        return row

    async def finish_backfill(
        self,
        progress_id: int,
        *,
        status: str,
        records_inserted: int = 0,
        error: Optional[str] = None,
    ) -> None:
        row = await self.session.get(GeeBackfillProgress, progress_id)
        if row is None:
            raise ValueError(f"unknown backfill progress id {progress_id}")
        row.status = status
        row.records_inserted = records_inserted
        row.error = error
        row.finished_at = utcnow()
        await self.session.commit()

    async def reset_backfill(self, dataset: Optional[str] = None) -> int:
        stmt = sa_delete(GeeBackfillProgress)
        if dataset:
            stmt = stmt.where(GeeBackfillProgress.dataset == dataset)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount or 0

    # ---------------- run log ----------------

    async def start_run(self, run_id: str, mode: str, dry_run: bool) -> None:
        self.session.add(
            IngestionRun(
                run_id=run_id,
                mode=mode,
                dry_run=dry_run,
                started_at=utcnow(),
                status="running",
            )
        )
        await self.session.commit()

    async def finish_run(self, run_id: str, **fields: Any) -> None:
        run = await self.session.get(IngestionRun, run_id)
        if run is None:
            logger.warning("finish_run called for unknown run_id=%s", run_id)
            return
        for key, value in fields.items():
            setattr(run, key, value)
        run.finished_at = utcnow()
        await self.session.commit()


class DryRunRepository:
    """Performs every step except the write (spec: dry-run must not touch db).

    Reads still hit the real database so that resume points and existing-key
    checks reflect reality; only mutations are suppressed.
    """

    def __init__(self, inner: Optional[TimestampRepository] = None):
        self.inner = inner
        self.would_write: list[ObservationRecord] = []

    async def upsert_observations(
        self, records: Sequence[ObservationRecord]
    ) -> WriteResult:
        self.would_write.extend(records)
        unique = {(r.observation_timestamp, r.observation_key) for r in records}
        already = set()
        if self.inner is not None:
            already = await self.inner.existing_keys(unique)
        return WriteResult(
            inserted=len(unique - already),
            updated=len(unique & already),
            skipped=len(records) - len(unique),
        )

    async def get_checkpoint(self, dataset: str) -> Optional[IngestionCheckpoint]:
        if self.inner is None:
            return None
        return await self.inner.get_checkpoint(dataset)

    async def save_checkpoint(self, dataset: str, **fields: Any) -> None:
        logger.info("[dry-run] would save checkpoint dataset=%s %s", dataset, fields)

    async def start_run(self, run_id: str, mode: str, dry_run: bool) -> None:
        logger.info("[dry-run] would open ingestion run %s (%s)", run_id, mode)

    async def finish_run(self, run_id: str, **fields: Any) -> None:
        logger.info("[dry-run] would close ingestion run %s", run_id)

    # A dry run writes nothing, so it has nothing to protect and must not
    # block a real run that is legitimately holding the lease.
    async def acquire_lock(
        self, lock_key: str, run_id: str, mode: Optional[str] = None, **kwargs: Any
    ) -> bool:
        logger.info("[dry-run] would acquire lock %s", lock_key)
        return True

    async def renew_lock(self, lock_key: str, run_id: str, **kwargs: Any) -> bool:
        return True

    async def release_lock(self, lock_key: str, run_id: str) -> None:
        logger.info("[dry-run] would release lock %s", lock_key)
