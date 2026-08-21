# packages/backend/app/ingestion/windows.py
"""Date-window arithmetic for incremental and backfill runs.

Isolated from the pipeline so it is testable without GEE or a database — the
resume/lookback logic is where an ingestion pipeline most often goes subtly
wrong, so it gets its own unit tests.

Convention throughout: windows are half-open [start, end), matching GEE's
filterDate, and every date is a plain UTC calendar date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator, Optional


@dataclass(frozen=True)
class DateWindow:
    start: date
    end: date  # exclusive

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"window end {self.end} precedes start {self.start}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    @property
    def is_empty(self) -> bool:
        return self.end <= self.start

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"[{self.start}..{self.end})"


def chunked(window: DateWindow, chunk_days: int) -> Iterator[DateWindow]:
    """Split a window into requests small enough for GEE to answer reliably.

    A single ten-year request is one timeout away from losing all its work;
    chunking bounds both the payload and the amount redone after a failure.
    """
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    if window.is_empty:
        return
    cursor = window.start
    while cursor < window.end:
        stop = min(cursor + timedelta(days=chunk_days), window.end)
        yield DateWindow(cursor, stop)
        cursor = stop


def daily_window(
    last_successful: Optional[date],
    today: date,
    historical_start: date,
    lookback_days: int,
    target_end: Optional[date] = None,
) -> DateWindow:
    """Window for an incremental run.

    With no checkpoint the caller is expected to run a backfill, but returning
    the full historical window here keeps a cold daily run correct rather than
    silently ingesting nothing.

    The lookback re-queries recently-covered days so late-published scenes are
    caught. That deliberately re-reads data already stored; it is safe purely
    because writes are idempotent upserts keyed on the source image.
    """
    if lookback_days < 0:
        raise ValueError("lookback_days must be >= 0")

    # End is exclusive and tomorrow-bounded so today's own publications are
    # included; never extended past the configured requested cutoff.
    end = today + timedelta(days=1)
    if target_end is not None:
        end = min(end, target_end + timedelta(days=1))

    if last_successful is None:
        start = historical_start
    else:
        start = last_successful - timedelta(days=lookback_days)
        start = max(start, historical_start)

    if end < start:
        # Target cutoff already behind the watermark: nothing to do.
        return DateWindow(start, start)
    return DateWindow(start, end)


def backfill_window(
    cursor: Optional[date],
    historical_start: date,
    target_end: date,
    latest_available: Optional[date] = None,
) -> DateWindow:
    """Window for a historical run, resuming from a stored cursor.

    `latest_available` is what GEE actually holds. Clamping to it is what keeps
    the pipeline from repeatedly re-querying an empty future range, and is
    reported separately from `requested_until` so the shortfall stays visible
    instead of being papered over.
    """
    start = cursor or historical_start
    start = max(start, historical_start)

    effective_end = target_end
    if latest_available is not None:
        effective_end = min(effective_end, latest_available)

    end = effective_end + timedelta(days=1)
    if end <= start:
        return DateWindow(start, start)
    return DateWindow(start, end)


def availability_status(
    requested_until: date,
    latest_available: Optional[date],
    cadence_days: int,
) -> str:
    """Classify the gap between what was asked for and what the source holds.

    Never used to fabricate the missing period — only to label it.
    """
    if latest_available is None:
        return "no_source_data"
    if latest_available >= requested_until:
        return "complete"
    gap = (requested_until - latest_available).days
    # A gap inside one publication cycle is just normal latency, not a problem.
    if gap <= cadence_days:
        return "current_within_cadence"
    return "partial_source_availability"


def missing_days(requested_until: date, latest_available: Optional[date]) -> int:
    if latest_available is None:
        return 0
    return max(0, (requested_until - latest_available).days)
