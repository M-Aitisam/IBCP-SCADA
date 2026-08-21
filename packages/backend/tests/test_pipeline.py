# packages/backend/tests/test_pipeline.py
"""Pipeline orchestration: failure isolation, dry-run, incremental resume.

The extractor is stubbed so the run is deterministic and offline.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

import pytest

from app.databases.timestampdb.repository import ObservationRecord
from app.ingestion.extractor import ChunkResult
from app.ingestion.gee_client import GEEPermanentError, classify_error
from app.ingestion.pipeline import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    IngestionPipeline,
)
from app.ingestion.config import ingestion_settings
from app.ingestion.registry import DATASETS
from conftest import FakeClient, make_roi


class StubExtractor:
    """Scripted extractor: canned records per dataset, optional failures."""

    def __init__(
        self,
        roi,
        records_by_dataset: Optional[dict] = None,
        latest_by_dataset: Optional[dict] = None,
        fail_datasets: Optional[dict] = None,
    ):
        self.roi = roi
        self.records_by_dataset = records_by_dataset or {}
        self.latest_by_dataset = latest_by_dataset or {}
        self.fail_datasets = fail_datasets or {}
        self.chunks_requested: list[tuple[str, date, date]] = []

    def latest_available(self, config, ceiling):
        value = self.latest_by_dataset.get(config.name, date(2026, 8, 19))
        if isinstance(value, Exception):
            raise value
        return value

    def extract_chunk(self, config, start, end) -> ChunkResult:
        self.chunks_requested.append((config.name, start, end))
        if config.name in self.fail_datasets:
            raise self.fail_datasets[config.name]
        records = list(self.records_by_dataset.get(config.name, []))
        return ChunkResult(
            records=records,
            images_found=len(records),
            latest_observation=max((r.observation_date for r in records), default=None),
        )


def record(dataset: str, region: str = "R1", day: int = 1) -> ObservationRecord:
    """A record that passes validation for whichever band we picked.

    The value comes from the middle of the band's configured physical range, so
    this helper cannot accidentally produce data the validator rejects.
    """
    config = DATASETS[dataset]
    band = config.bands[0]
    if band.valid_range is not None:
        value = (band.valid_range.minimum + band.valid_range.maximum) / 2
    else:
        value = 1.0
    return ObservationRecord(
        dataset=dataset,
        dataset_asset_id=config.asset_id,
        region_id=region,
        metric=band.metric,
        observation_timestamp=datetime(2026, 8, day, tzinfo=timezone.utc),
        observation_date=date(2026, 8, day),
        source_image_id=f"{dataset}_img_{day}",
        unit=band.unit,
        value=value,
        region_type="district",
    )


def records_for(dataset: str, day: int = 1) -> list[ObservationRecord]:
    """One record per region, the way a real chunk arrives."""
    return [record(dataset, region=r, day=day) for r in ("R1", "R2")]


def narrow_settings(start: date = date(2026, 8, 1)):
    """Settings whose history starts recently, so a cold run is a single chunk.

    Without this, a cold-start daily run legitimately spans 2016..2026 and the
    stub replays its canned records once per chunk, obscuring what is asserted.
    """
    return ingestion_settings.model_copy(update={"GEE_HISTORICAL_START": start})


def build(store, narrow: bool = True, **kwargs) -> IngestionPipeline:
    roi = make_roi(2)
    stub = StubExtractor(roi, **kwargs)
    return IngestionPipeline(
        store=store,
        client=FakeClient(),
        roi=roi,
        extractor=stub,
        settings=narrow_settings() if narrow else ingestion_settings,
    )


# --- failure isolation ------------------------------------------------------


@pytest.mark.asyncio
async def test_one_failed_dataset_does_not_stop_the_others(store):
    """MOD11A2 fails; the other four still ingest and the run is partial."""
    records = {name: records_for(name) for name in DATASETS}
    pipeline = build(
        store,
        records_by_dataset=records,
        fail_datasets={"mod11a2": RuntimeError("GEE exploded")},
    )
    result = await pipeline.run(mode="daily", today=date(2026, 8, 21))

    by_dataset = {o.dataset: o for o in result.outcomes}
    assert by_dataset["mod11a2"].status == STATUS_FAILED
    assert "GEE exploded" in by_dataset["mod11a2"].error
    for name in ("sentinel2", "mod13q1", "chirps", "sentinel1"):
        assert by_dataset[name].records_inserted > 0

    assert result.status == STATUS_PARTIAL
    assert result.totals["datasets_succeeded"] == 4
    assert result.totals["datasets_failed"] == 1


@pytest.mark.asyncio
async def test_all_datasets_failing_marks_the_run_failed(store):
    pipeline = build(
        store,
        fail_datasets={name: RuntimeError("down") for name in DATASETS},
    )
    result = await pipeline.run(mode="daily", today=date(2026, 8, 21))
    assert result.status == STATUS_FAILED


@pytest.mark.asyncio
async def test_all_succeeding_marks_the_run_success(store):
    records = {name: records_for(name) for name in DATASETS}
    pipeline = build(store, records_by_dataset=records)
    result = await pipeline.run(mode="daily", today=date(2026, 8, 21))
    assert result.status == STATUS_SUCCESS
    assert result.totals["datasets_failed"] == 0


@pytest.mark.asyncio
async def test_failure_is_recorded_on_the_checkpoint(store):
    pipeline = build(store, fail_datasets={"chirps": RuntimeError("boom")})
    await pipeline.run(mode="daily", only=["chirps"], today=date(2026, 8, 21))
    assert store.checkpoints["chirps"]["last_status"] == STATUS_FAILED
    assert "boom" in store.checkpoints["chirps"]["last_error"]


# --- retry classification ---------------------------------------------------


def test_permanent_errors_are_not_retried():
    assert isinstance(
        classify_error(Exception("Collection.load: not found")), GEEPermanentError
    )
    assert isinstance(
        classify_error(Exception("Permission denied on asset")), GEEPermanentError
    )


def test_transient_errors_are_retryable():
    from app.ingestion.gee_client import GEETransientError

    for message in ("Deadline exceeded", "429 Too Many Requests", "503 backend error"):
        assert isinstance(classify_error(Exception(message)), GEETransientError)


# --- source availability ----------------------------------------------------


@pytest.mark.asyncio
async def test_missing_source_period_is_reported_not_fabricated(store):
    """Requested through 2026-08-19; MOD13Q1 only has 2026-07-12."""
    pipeline = build(
        store,
        records_by_dataset={"mod13q1": records_for("mod13q1", day=1)},
        latest_by_dataset={"mod13q1": date(2026, 7, 12)},
    )
    result = await pipeline.run(
        mode="daily", only=["mod13q1"], today=date(2026, 8, 21)
    )
    outcome = result.outcomes[0]

    assert outcome.requested_until == date(2026, 8, 19)
    assert outcome.latest_available == date(2026, 7, 12)
    assert outcome.availability == "partial_source_availability"
    assert outcome.missing_days == 38
    # Crucially: no record was invented for the missing window.
    assert all(r.observation_date <= date(2026, 8, 19) for r in store.rows.values())


@pytest.mark.asyncio
async def test_dataset_with_no_source_data_ingests_nothing(store):
    pipeline = build(store, latest_by_dataset={"chirps": None})
    result = await pipeline.run(mode="daily", only=["chirps"], today=date(2026, 8, 21))
    assert result.outcomes[0].availability == "no_source_data"
    assert len(store.rows) == 0
    # Not a failure: an empty source is a fact, not an error.
    assert result.status == STATUS_SUCCESS


@pytest.mark.asyncio
async def test_availability_report_does_not_ingest(store):
    pipeline = build(
        store,
        records_by_dataset={name: records_for(name) for name in DATASETS},
        latest_by_dataset={"mod13q1": date(2026, 7, 12)},
    )
    report = await pipeline.availability_report()
    assert {o.dataset for o in report} == set(DATASETS)
    assert len(store.rows) == 0


# --- dry run ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(store):
    from app.databases.timestampdb.repository import DryRunRepository

    dry = DryRunRepository(inner=None)
    roi = make_roi(2)
    stub = StubExtractor(roi, records_by_dataset={"chirps": records_for("chirps")})
    pipeline = IngestionPipeline(
        store=dry, client=FakeClient(), roi=roi, extractor=stub,
        settings=narrow_settings(),
    )

    result = await pipeline.run(
        mode="daily", dry_run=True, only=["chirps"], today=date(2026, 8, 21)
    )
    assert result.dry_run
    assert result.outcomes[0].records_inserted == 2  # would-write count
    assert len(dry.would_write) == 2
    assert "DRY RUN" in result.render()


@pytest.mark.asyncio
async def test_dry_run_does_not_advance_checkpoints(store):
    from app.databases.timestampdb.repository import DryRunRepository

    dry = DryRunRepository(inner=None)
    roi = make_roi(1)
    stub = StubExtractor(roi, records_by_dataset={"chirps": [record("chirps")]})
    pipeline = IngestionPipeline(
        store=dry, client=FakeClient(), roi=roi, extractor=stub,
        settings=narrow_settings(),
    )
    await pipeline.run(mode="daily", dry_run=True, only=["chirps"], today=date(2026, 8, 21))
    # DryRunRepository keeps no checkpoint state at all.
    assert not hasattr(dry, "checkpoints")


# --- incremental behaviour --------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_advances_after_a_successful_run(store):
    pipeline = build(store, records_by_dataset={"chirps": records_for("chirps", day=5)})
    await pipeline.run(mode="daily", only=["chirps"], today=date(2026, 8, 21))
    assert store.checkpoints["chirps"]["last_successful_observation_date"] == date(
        2026, 8, 5
    )
    assert store.checkpoints["chirps"]["last_status"] == STATUS_SUCCESS


@pytest.mark.asyncio
async def test_second_run_queries_a_narrower_window(store):
    # Real historical start here, so the lookback is not clamped by a narrowed
    # window and the incremental behaviour is what is actually under test.
    records = {"chirps": records_for("chirps", day=5)}
    first = build(store, narrow=False, records_by_dataset=records)
    await first.run(mode="daily", only=["chirps"], today=date(2026, 8, 21))

    second = build(store, narrow=False, records_by_dataset=records)
    await second.run(mode="daily", only=["chirps"], today=date(2026, 8, 21))

    # Second run starts from the watermark minus lookback, not from 2016.
    _, start, _ = second.extractor.chunks_requested[0]
    assert start > date(2016, 1, 1)
    assert start == date(2026, 7, 29)  # 2026-08-05 minus 7 days


@pytest.mark.asyncio
async def test_repeat_runs_do_not_duplicate_rows(store):
    """End-to-end idempotency through the pipeline, not just the store."""
    records = {"chirps": records_for("chirps", day=5)}
    for _ in range(3):
        pipeline = build(store, records_by_dataset=records)
        await pipeline.run(mode="daily", only=["chirps"], today=date(2026, 8, 21))
    # One metric x two regions x one image = two rows, however often it runs.
    assert len(store.rows) == 2


@pytest.mark.asyncio
async def test_backfill_records_a_resume_cursor(store):
    pipeline = build(store, records_by_dataset={"chirps": records_for("chirps")})
    await pipeline.run(mode="backfill", only=["chirps"], today=date(2026, 8, 21))
    assert store.checkpoints["chirps"]["backfill_cursor"] is not None
    assert store.checkpoints["chirps"]["backfill_complete"] is True


@pytest.mark.asyncio
async def test_backfill_chunks_the_full_history(store):
    pipeline = build(store, narrow=False)
    await pipeline.run(mode="backfill", only=["chirps"], today=date(2026, 8, 21))
    chunks = pipeline.extractor.chunks_requested
    assert chunks[0][1] == date(2016, 1, 1)
    assert len(chunks) > 40  # 10 years at 90-day chunks
    assert all((end - start).days <= 90 for _, start, end in chunks)


@pytest.mark.asyncio
async def test_backfill_resumes_from_stored_cursor(store):
    store.checkpoints["chirps"] = {"backfill_cursor": date(2024, 1, 1)}
    pipeline = build(store, narrow=False)
    await pipeline.run(mode="backfill", only=["chirps"], today=date(2026, 8, 21))
    assert pipeline.extractor.chunks_requested[0][1] == date(2024, 1, 1)


# --- run log ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_log_captures_the_required_fields(store):
    records = {name: records_for(name) for name in DATASETS}
    pipeline = build(
        store,
        records_by_dataset=records,
        fail_datasets={"sentinel1": RuntimeError("nope")},
    )
    result = await pipeline.run(mode="daily", today=date(2026, 8, 21))

    logged = store.runs[result.run_id]
    for key in (
        "status",
        "datasets_attempted",
        "datasets_succeeded",
        "datasets_failed",
        "records_inserted",
        "records_updated",
        "records_skipped",
        "records_rejected",
        "dataset_stats",
        "latest_observation_per_dataset",
        "errors",
    ):
        assert key in logged
    assert logged["datasets_attempted"] == 5
    assert "sentinel1" in logged["errors"]
    assert logged["latest_observation_per_dataset"]["chirps"]["requested_until"] == (
        "2026-08-19"
    )


@pytest.mark.asyncio
async def test_render_shows_availability_and_totals(store):
    pipeline = build(
        store,
        records_by_dataset={"mod13q1": records_for("mod13q1")},
        latest_by_dataset={"mod13q1": date(2026, 7, 12)},
    )
    result = await pipeline.run(mode="daily", only=["mod13q1"], today=date(2026, 8, 21))
    text = result.render()
    assert "MODIS/061/MOD13Q1" in text
    assert "2026-07-12" in text
    assert "NOT fabricated" in text
    assert "Status: SUCCESS" in text
