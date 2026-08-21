# packages/backend/app/ingestion/pipeline.py
"""Ingestion orchestration.

One run = authenticate, resolve ROI, then process each enabled dataset
independently. Dataset isolation is the central guarantee: a failure in one
dataset is caught, recorded, and the run continues with the rest, finishing as
`partial_success` rather than losing the whole night's work.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.ingestion.config import IngestionSettings, ingestion_settings
from app.ingestion.extractor import Extractor
from app.ingestion.gee_client import EarthEngineClient, GEEPermanentError
from app.ingestion.registry import DatasetConfig, enabled_datasets
from app.ingestion.roi import ROI, ROIResolver
from app.ingestion.validation import Validator
from app.ingestion.windows import (
    DateWindow,
    availability_status,
    backfill_window,
    chunked,
    daily_window,
    missing_days,
)

logger = logging.getLogger(__name__)

STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial_success"
STATUS_FAILED = "failed"


@dataclass
class DatasetOutcome:
    dataset: str
    asset_id: str
    status: str = STATUS_SUCCESS
    window: Optional[str] = None
    chunks_processed: int = 0
    chunks_failed: int = 0
    images_found: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    records_rejected: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    latest_observation: Optional[date] = None
    requested_until: Optional[date] = None
    latest_available: Optional[date] = None
    availability: Optional[str] = None
    missing_days: int = 0
    error: Optional[str] = None

    def to_json(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "asset_id": self.asset_id,
            "status": self.status,
            "window": self.window,
            "chunks_processed": self.chunks_processed,
            "chunks_failed": self.chunks_failed,
            "images_found": self.images_found,
            "records_inserted": self.records_inserted,
            "records_updated": self.records_updated,
            "records_skipped": self.records_skipped,
            "records_rejected": self.records_rejected,
            "rejection_reasons": self.rejection_reasons,
            "latest_observation": _iso(self.latest_observation),
            "requested_until": _iso(self.requested_until),
            "latest_available": _iso(self.latest_available),
            "availability_status": self.availability,
            "missing_days": self.missing_days,
            "error": self.error,
        }


@dataclass
class RunResult:
    run_id: str
    mode: str
    dry_run: bool
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str = STATUS_SUCCESS
    roi_source: Optional[str] = None
    roi_regions: int = 0
    outcomes: list[DatasetOutcome] = field(default_factory=list)

    @property
    def totals(self) -> dict[str, int]:
        return {
            "records_inserted": sum(o.records_inserted for o in self.outcomes),
            "records_updated": sum(o.records_updated for o in self.outcomes),
            "records_skipped": sum(o.records_skipped for o in self.outcomes),
            "records_rejected": sum(o.records_rejected for o in self.outcomes),
            "datasets_attempted": len(self.outcomes),
            "datasets_succeeded": sum(
                1 for o in self.outcomes if o.status == STATUS_SUCCESS
            ),
            "datasets_failed": sum(1 for o in self.outcomes if o.status == STATUS_FAILED),
        }

    def render(self) -> str:
        """Human-readable run summary for logs and CI output."""
        totals = self.totals
        lines = [
            "GEE INGESTION RUN",
            "-" * 25,
            f"Run ID: {self.run_id}",
            f"Mode:   {self.mode}{' (DRY RUN - nothing written)' if self.dry_run else ''}",
            f"ROI:    {self.roi_regions} regions from {self.roi_source}",
            "",
        ]
        for outcome in self.outcomes:
            lines.append(f"{outcome.dataset} [{outcome.asset_id}]")
            lines.append(f"  window:         {outcome.window}")
            lines.append(f"  images found:   {outcome.images_found}")
            lines.append(
                f"  records:        +{outcome.records_inserted} new, "
                f"~{outcome.records_updated} updated, "
                f"{outcome.records_skipped} skipped, "
                f"{outcome.records_rejected} rejected"
            )
            if outcome.rejection_reasons:
                lines.append(f"  rejections:     {outcome.rejection_reasons}")
            lines.append(
                f"  latest source:  {_iso(outcome.latest_available)} "
                f"(requested {_iso(outcome.requested_until)}) -> {outcome.availability}"
            )
            if outcome.missing_days:
                lines.append(
                    f"  NOT fabricated: {outcome.missing_days} day(s) have no source data"
                )
            if outcome.error:
                lines.append(f"  ERROR:          {outcome.error}")
            lines.append(f"  status:         {outcome.status}")
            lines.append("")
        lines.append(
            f"Totals: +{totals['records_inserted']} inserted, "
            f"~{totals['records_updated']} updated, "
            f"{totals['records_rejected']} rejected"
        )
        lines.append(
            f"Datasets: {totals['datasets_succeeded']}/{totals['datasets_attempted']} succeeded"
        )
        lines.append(f"Status: {self.status.upper()}")
        return "\n".join(lines)


class IngestionPipeline:
    def __init__(
        self,
        store: Any,
        client: Optional[EarthEngineClient] = None,
        settings: Optional[IngestionSettings] = None,
        roi: Optional[ROI] = None,
        extractor: Optional[Extractor] = None,
        test_lookback_days: int = 18,
    ):
        # For mode="test" only: how far back from each dataset's newest real
        # observation the bounded verification run should reach.
        self.test_lookback_days = test_lookback_days
        self.settings = settings or ingestion_settings
        self.client = client or EarthEngineClient(self.settings)
        self.store = store
        self._roi = roi
        self._extractor = extractor

    # ------------------------------------------------------------------

    def prepare(self) -> ROI:
        """Authenticate and resolve the ROI once per run."""
        if self._roi is None:
            self.client.initialise()
            self.client.verify()
            self._roi = ROIResolver(self.client, self.settings).resolve()
        if self._extractor is None:
            self._extractor = Extractor(self.client, self._roi, self.settings)
        return self._roi

    @property
    def extractor(self) -> Extractor:
        if self._extractor is None:
            self.prepare()
        assert self._extractor is not None
        return self._extractor

    # ------------------------------------------------------------------

    async def run(
        self,
        mode: str,
        dry_run: bool = False,
        only: Optional[list[str]] = None,
        today: Optional[date] = None,
    ) -> RunResult:
        run_id = _make_run_id(mode)
        started = datetime.now(timezone.utc)
        result = RunResult(
            run_id=run_id, mode=mode, dry_run=dry_run, started_at=started
        )

        roi = self.prepare()
        result.roi_source = roi.source
        result.roi_regions = len(roi)

        await self.store.start_run(run_id, mode, dry_run)
        logger.info(
            "run=%s mode=%s dry_run=%s regions=%d", run_id, mode, dry_run, len(roi)
        )

        datasets = enabled_datasets(only)
        for config in datasets:
            outcome = await self._run_dataset(config, mode, dry_run, today)
            result.outcomes.append(outcome)

        totals = result.totals
        if totals["datasets_failed"] == 0:
            result.status = STATUS_SUCCESS
        elif totals["datasets_succeeded"] == 0:
            result.status = STATUS_FAILED
        else:
            result.status = STATUS_PARTIAL

        result.finished_at = datetime.now(timezone.utc)
        await self.store.finish_run(
            run_id,
            status=result.status,
            datasets_attempted=totals["datasets_attempted"],
            datasets_succeeded=totals["datasets_succeeded"],
            datasets_failed=totals["datasets_failed"],
            records_inserted=totals["records_inserted"],
            records_updated=totals["records_updated"],
            records_skipped=totals["records_skipped"],
            records_rejected=totals["records_rejected"],
            dataset_stats={o.dataset: o.to_json() for o in result.outcomes},
            latest_observation_per_dataset={
                o.dataset: {
                    "requested_until": _iso(o.requested_until),
                    "latest_available": _iso(o.latest_available),
                    "latest_ingested": _iso(o.latest_observation),
                    "status": o.availability,
                }
                for o in result.outcomes
            },
            errors={o.dataset: o.error for o in result.outcomes if o.error} or None,
        )
        return result

    # ------------------------------------------------------------------

    async def _run_dataset(
        self,
        config: DatasetConfig,
        mode: str,
        dry_run: bool,
        today: Optional[date],
    ) -> DatasetOutcome:
        """Process one dataset. Never raises: failures become an outcome."""
        outcome = DatasetOutcome(dataset=config.name, asset_id=config.asset_id)
        today = today or datetime.now(timezone.utc).date()
        requested_until = self.settings.GEE_TARGET_END
        outcome.requested_until = requested_until

        try:
            # Ask the source what it actually holds before deciding the window.
            latest_available = self.extractor.latest_available(config, requested_until)
            outcome.latest_available = latest_available
            outcome.availability = availability_status(
                requested_until, latest_available, config.cadence.nominal_days
            )
            outcome.missing_days = missing_days(requested_until, latest_available)

            if latest_available is None:
                outcome.status = STATUS_SUCCESS
                outcome.window = "none (no source observations)"
                logger.warning(
                    "dataset=%s has no observations at or before %s; nothing ingested",
                    config.name,
                    requested_until,
                )
                await self._save_checkpoint(config, outcome, today, mode)
                return outcome

            checkpoint = await self.store.get_checkpoint(config.name)
            window = self._window_for(mode, config, checkpoint, today, latest_available)
            outcome.window = repr(window)

            if window.is_empty:
                logger.info(
                    "dataset=%s window empty; already current through %s",
                    config.name,
                    latest_available,
                )
                await self._save_checkpoint(config, outcome, today, mode)
                return outcome

            await self._process_window(config, window, outcome, dry_run, mode, today)

        except GEEPermanentError as exc:
            # Configuration/asset errors: record and move on, do not retry.
            outcome.status = STATUS_FAILED
            outcome.error = f"permanent: {exc}"
            logger.error("dataset=%s permanently failed: %s", config.name, exc)
            await self._record_failure(config, outcome)
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            outcome.status = STATUS_FAILED
            outcome.error = f"{type(exc).__name__}: {exc}"
            logger.exception("dataset=%s failed; continuing with others", config.name)
            await self._record_failure(config, outcome)

        return outcome

    def _window_for(
        self,
        mode: str,
        config: DatasetConfig,
        checkpoint: Any,
        today: date,
        latest_available: Optional[date],
    ) -> DateWindow:
        if mode == "test":
            # Anchor on what the source actually has, not on the requested
            # cutoff. A dataset lagging by more than the lookback (MOD13Q1 is
            # often 3+ weeks behind) would otherwise get an empty window and
            # the verification run would silently ingest nothing.
            if latest_available is None:
                return DateWindow(today, today)
            start = max(
                self.settings.GEE_HISTORICAL_START,
                latest_available - timedelta(days=self.test_lookback_days),
            )
            return DateWindow(start, latest_available + timedelta(days=1))

        if mode == "backfill":
            cursor = getattr(checkpoint, "backfill_cursor", None) if checkpoint else None
            return backfill_window(
                cursor,
                self.settings.GEE_HISTORICAL_START,
                self.settings.GEE_TARGET_END,
                latest_available,
            )

        last = (
            getattr(checkpoint, "last_successful_observation_date", None)
            if checkpoint
            else None
        )
        window = daily_window(
            last,
            today,
            self.settings.GEE_HISTORICAL_START,
            self.settings.GEE_LOOKBACK_DAYS,
            self.settings.GEE_TARGET_END,
        )
        # No point querying past what the source actually has.
        if latest_available is not None:
            end = min(window.end, latest_available + timedelta(days=1))
            window = DateWindow(window.start, max(window.start, end))
        return window

    async def _process_window(
        self,
        config: DatasetConfig,
        window: DateWindow,
        outcome: DatasetOutcome,
        dry_run: bool,
        mode: str,
        today: date,
    ) -> None:
        """Walk the window in chunks, persisting after each one.

        Persisting per chunk (rather than accumulating a decade in memory) is
        what makes a backfill resumable: an interrupted run leaves the cursor
        at the last completed chunk.
        """
        valid_region_ids = set(self.extractor.roi.regions)
        validator = Validator(config, valid_region_ids)
        latest_seen: Optional[date] = None

        for chunk in chunked(window, config.chunk_days):
            try:
                extracted = self.extractor.extract_chunk(config, chunk.start, chunk.end)
            except GEEPermanentError:
                raise
            except Exception as exc:  # noqa: BLE001
                # One bad chunk should not abandon the rest of the window.
                outcome.chunks_failed += 1
                outcome.status = STATUS_PARTIAL
                outcome.error = f"chunk {chunk} failed: {type(exc).__name__}: {exc}"
                logger.warning(
                    "dataset=%s chunk %s failed, continuing: %s", config.name, chunk, exc
                )
                continue

            outcome.images_found += extracted.images_found
            outcome.chunks_processed += 1

            if not extracted.records:
                latest_seen = _max_date(latest_seen, extracted.latest_observation)
                await self._advance_cursor(config, chunk.end, mode, dry_run)
                continue

            validated = validator.validate(extracted.records)
            validated.log_rejections(config.name)
            outcome.records_rejected += len(validated.rejected)
            for reason, count in validated.rejection_summary.items():
                outcome.rejection_reasons[reason] = (
                    outcome.rejection_reasons.get(reason, 0) + count
                )

            write = await self.store.upsert_observations(validated.accepted)
            outcome.records_inserted += write.inserted
            outcome.records_updated += write.updated
            outcome.records_skipped += write.skipped

            chunk_latest = _max_date(
                extracted.latest_observation,
                max((r.observation_date for r in validated.accepted), default=None),
            )
            latest_seen = _max_date(latest_seen, chunk_latest)

            # Cursor advances only after the chunk's rows are committed.
            await self._advance_cursor(config, chunk.end, mode, dry_run, latest_seen)

        outcome.latest_observation = latest_seen
        if outcome.chunks_failed and outcome.chunks_processed:
            outcome.status = STATUS_PARTIAL
        elif outcome.chunks_failed and not outcome.chunks_processed:
            outcome.status = STATUS_FAILED

        await self._save_checkpoint(config, outcome, today, mode)

    async def _advance_cursor(
        self,
        config: DatasetConfig,
        cursor_end: date,
        mode: str,
        dry_run: bool,
        latest_seen: Optional[date] = None,
    ) -> None:
        if dry_run:
            return
        fields: dict[str, Any] = {"last_run_at": datetime.now(timezone.utc)}
        if mode == "backfill":
            fields["backfill_cursor"] = cursor_end
        if latest_seen is not None:
            fields["last_successful_observation_date"] = latest_seen
        await self.store.save_checkpoint(config.name, **fields)

    async def _save_checkpoint(
        self, config: DatasetConfig, outcome: DatasetOutcome, today: date, mode: str
    ) -> None:
        fields: dict[str, Any] = {
            "last_run_at": datetime.now(timezone.utc),
            "last_status": outcome.status,
            "requested_until": outcome.requested_until,
            "latest_available_at_source": outcome.latest_available,
            "availability_status": outcome.availability,
            "last_error": outcome.error,
        }
        if outcome.latest_observation is not None:
            fields["last_successful_observation_date"] = outcome.latest_observation
        if mode == "backfill" and outcome.status == STATUS_SUCCESS:
            fields["backfill_complete"] = True
        await self.store.save_checkpoint(config.name, **fields)

    async def _record_failure(
        self, config: DatasetConfig, outcome: DatasetOutcome
    ) -> None:
        try:
            await self.store.save_checkpoint(
                config.name,
                last_run_at=datetime.now(timezone.utc),
                last_status=STATUS_FAILED,
                last_error=outcome.error,
            )
        except Exception:  # noqa: BLE001 - never let bookkeeping kill the run
            logger.exception("could not record failure checkpoint for %s", config.name)

    # ------------------------------------------------------------------

    async def availability_report(self) -> list[DatasetOutcome]:
        """Query each dataset's real latest observation without ingesting."""
        self.prepare()
        requested = self.settings.GEE_TARGET_END
        report: list[DatasetOutcome] = []
        for config in enabled_datasets():
            outcome = DatasetOutcome(
                dataset=config.name,
                asset_id=config.asset_id,
                requested_until=requested,
            )
            try:
                latest = self.extractor.latest_available(config, requested)
                outcome.latest_available = latest
                outcome.availability = availability_status(
                    requested, latest, config.cadence.nominal_days
                )
                outcome.missing_days = missing_days(requested, latest)
            except Exception as exc:  # noqa: BLE001
                outcome.status = STATUS_FAILED
                outcome.error = f"{type(exc).__name__}: {exc}"
            report.append(outcome)
        return report


# ----------------------------------------------------------------------


def _make_run_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{mode}-{uuid.uuid4().hex[:6]}"


def _iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


def _max_date(a: Optional[date], b: Optional[date]) -> Optional[date]:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)
