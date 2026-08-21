# packages/backend/tests/conftest.py
"""Shared fixtures. No live Earth Engine and no database are required.

The GEE surface is replaced by a fake that returns canned reduceRegions output,
so the whole pipeline — windows, extraction, validation, idempotency, error
isolation — is exercised deterministically and offline.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import pytest

# Make `app` importable exactly the way index.py does for Vercel.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.databases.timestampdb.repository import (  # noqa: E402
    ObservationRecord,
    WriteResult,
)
from app.ingestion.roi import ROI, Region  # noqa: E402


class FakeStore:
    """In-memory stand-in for TimestampRepository.

    Enforces the same uniqueness contract as the real Postgres upsert:
    (observation_timestamp, observation_key) is the primary key, so a second
    write of the same observation updates rather than duplicates.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[datetime, str], ObservationRecord] = {}
        self.checkpoints: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}

    async def upsert_observations(
        self, records: Sequence[ObservationRecord]
    ) -> WriteResult:
        inserted = updated = skipped = 0
        seen_this_batch: set[tuple[datetime, str]] = set()
        for record in records:
            key = (record.observation_timestamp, record.observation_key)
            if key in seen_this_batch:
                skipped += 1
            seen_this_batch.add(key)
            if key in self.rows:
                updated += 1
            else:
                inserted += 1
            self.rows[key] = record
        return WriteResult(inserted=inserted, updated=updated, skipped=skipped)

    async def get_checkpoint(self, dataset: str):
        data = self.checkpoints.get(dataset)
        if data is None:
            return None
        return type("Checkpoint", (), data)

    async def save_checkpoint(self, dataset: str, **fields: Any) -> None:
        self.checkpoints.setdefault(dataset, {}).update(fields)

    async def start_run(self, run_id: str, mode: str, dry_run: bool) -> None:
        self.runs[run_id] = {"mode": mode, "dry_run": dry_run}

    async def finish_run(self, run_id: str, **fields: Any) -> None:
        self.runs.setdefault(run_id, {}).update(fields)


class FakeClient:
    """Replaces EarthEngineClient without importing the ee package."""

    def __init__(self, chunks: Optional[dict] = None, latest: Optional[dict] = None):
        # {(dataset, start_iso): [feature dicts]}
        self.chunks = chunks or {}
        # {dataset: date | Exception}
        self.latest = latest or {}
        self.calls: list[str] = []
        self.ee = None

    def initialise(self) -> None:
        pass

    def verify(self) -> bool:
        return True

    def with_retry(self, operation, description: str = ""):
        self.calls.append(description)
        return operation()


def make_roi(count: int = 2) -> ROI:
    regions = {
        f"R{i}": Region(
            region_id=f"R{i}",
            region_type="district",
            province="Sindh" if i % 2 else "Balochistan",
            district=f"District {i}",
        )
        for i in range(1, count + 1)
    }
    return ROI(
        feature_collection=object(),
        regions=regions,
        source="test-roi",
        region_type="district",
        id_property="__region_id",
    )


def feature(
    region_id: str,
    time_start_ms: int,
    image_id: str,
    props: dict,
) -> dict:
    base = {"__region_id": region_id, "__time_start": time_start_ms, "__image_id": image_id}
    base.update(props)
    return {"properties": base}


def ms(year: int, month: int, day: int) -> int:
    return int(
        datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000
    )


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def roi() -> ROI:
    return make_roi()


@pytest.fixture
def base_record() -> ObservationRecord:
    return ObservationRecord(
        dataset="chirps",
        dataset_asset_id="UCSB-CHG/CHIRPS/DAILY",
        region_id="R1",
        metric="rainfall_mm",
        observation_timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
        observation_date=date(2026, 8, 1),
        source_image_id="20260801",
        unit="mm",
        value=12.5,
    )
