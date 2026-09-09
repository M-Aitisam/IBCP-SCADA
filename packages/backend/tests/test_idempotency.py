# packages/backend/tests/test_idempotency.py
"""Running the same ingestion twice must not create duplicate records."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from app.databases.timestampdb.models import build_observation_key
from app.databases.timestampdb.repository import DryRunRepository


def test_key_is_deterministic():
    a = build_observation_key("chirps", "20260801", "R1", "rainfall_mm")
    b = build_observation_key("chirps", "20260801", "R1", "rainfall_mm")
    assert a == b and len(a) == 64


@pytest.mark.parametrize(
    "args",
    [
        ("mod13q1", "20260801", "R1", "rainfall_mm"),  # different dataset
        ("chirps", "20260802", "R1", "rainfall_mm"),  # different image
        ("chirps", "20260801", "R2", "rainfall_mm"),  # different region
        ("chirps", "20260801", "R1", "ndvi"),  # different metric
    ],
)
def test_key_varies_with_every_component(args):
    baseline = build_observation_key("chirps", "20260801", "R1", "rainfall_mm")
    assert build_observation_key(*args) != baseline


def test_key_components_cannot_be_confused_by_concatenation():
    """A delimiter-free key would collide on shifted boundaries."""
    assert build_observation_key("a", "bc", "d", "e") != build_observation_key(
        "ab", "c", "d", "e"
    )


@pytest.mark.asyncio
async def test_second_identical_run_updates_instead_of_duplicating(store, base_record):
    first = await store.upsert_observations([base_record])
    assert first.inserted == 1 and first.updated == 0

    second = await store.upsert_observations([base_record])
    assert second.inserted == 0 and second.updated == 1

    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_repeated_daily_runs_stay_at_one_row_per_observation(store, base_record):
    """Simulates the lookback window re-reading the same days for a week."""
    for _ in range(7):
        await store.upsert_observations([base_record])
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_distinct_observations_all_stored(store, base_record):
    records = [
        base_record,
        replace(base_record, region_id="R2"),
        replace(
            base_record,
            source_image_id="20260802",
            observation_timestamp=datetime(2026, 8, 2, tzinfo=timezone.utc),
            observation_date=date(2026, 8, 2),
        ),
        replace(base_record, metric="other_metric"),
    ]
    result = await store.upsert_observations(records)
    assert result.inserted == 4
    assert len(store.rows) == 4


@pytest.mark.asyncio
async def test_in_batch_duplicates_are_collapsed(store, base_record):
    """Two tiles of one image covering one region must not double-insert."""
    result = await store.upsert_observations([base_record, base_record])
    assert result.skipped == 1
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_corrected_value_overwrites_on_reingest(store, base_record):
    await store.upsert_observations([base_record])
    corrected = replace(base_record, value=99.9)
    await store.upsert_observations([corrected])
    stored = next(iter(store.rows.values()))
    assert stored.value == 99.9
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_dry_run_writes_nothing_but_reports_what_it_would_write(base_record):
    repo = DryRunRepository(inner=None)
    result = await repo.upsert_observations([base_record])
    assert result.inserted == 1
    assert repo.would_write == [base_record]
    # Nothing was persisted anywhere: DryRunRepository holds no table.
    assert not hasattr(repo, "rows")


@pytest.mark.asyncio
async def test_dry_run_counts_in_batch_duplicates_as_skipped(base_record):
    repo = DryRunRepository(inner=None)
    result = await repo.upsert_observations([base_record, base_record])
    assert result.inserted == 1
    assert result.skipped == 1


def test_observation_record_row_carries_key_and_ingest_time(base_record):
    row = base_record.to_row()
    assert row["observation_key"] == base_record.observation_key
    # Acquisition time and ingestion time are separate fields.
    assert row["observation_timestamp"] == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert row["ingested_at"] != row["observation_timestamp"]
    assert row["ingested_at"].tzinfo is not None
