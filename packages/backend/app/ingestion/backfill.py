"""Planning helpers and policy for the unattended GEE backfill queue."""
from __future__ import annotations

from datetime import date, timedelta

from app.ingestion.registry import DATASETS
from app.ingestion.windows import DateWindow, chunked

BACKFILL_DATASETS = ("chirps", "mod13q1", "mod11a2", "sentinel1", "sentinel2")
BACKFILL_CHUNK_DAYS = {
    "chirps": 30,
    "mod13q1": 60,
    "mod11a2": 30,
    "sentinel1": 30,
    "sentinel2": 30,
}
MAX_ATTEMPTS = 3


def validate_datasets(datasets: list[str] | None) -> list[str]:
    selected = datasets or list(BACKFILL_DATASETS)
    unknown = sorted(set(selected) - set(DATASETS))
    if unknown:
        raise ValueError(f"unknown backfill dataset(s): {', '.join(unknown)}")
    return selected


def planned_rows(
    start: date, end: date, datasets: list[str] | None = None
) -> list[dict[str, object]]:
    if end < start:
        raise ValueError("backfill end must not precede start")
    rows: list[dict[str, object]] = []
    for dataset in validate_datasets(datasets):
        window = DateWindow(start, end + timedelta(days=1))
        for chunk in chunked(window, BACKFILL_CHUNK_DAYS[dataset]):
            rows.append(
                {
                    "dataset": dataset,
                    "chunk_start": chunk.start,
                    "chunk_end": chunk.end - timedelta(days=1),
                    "status": "pending",
                    "records_inserted": 0,
                    "attempts": 0,
                }
            )
    return rows