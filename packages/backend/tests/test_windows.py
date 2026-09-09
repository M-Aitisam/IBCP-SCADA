# packages/backend/tests/test_windows.py
"""Date-window arithmetic: historical range, daily increment, target cutoff."""
from __future__ import annotations

from datetime import date

import pytest

from app.ingestion.windows import (
    DateWindow,
    availability_status,
    backfill_window,
    chunked,
    daily_window,
    missing_days,
)

HISTORICAL_START = date(2016, 1, 1)
TARGET_END = date(2026, 8, 19)


# --- chunking ---------------------------------------------------------------


def test_chunking_covers_the_window_without_gaps_or_overlap():
    window = DateWindow(date(2016, 1, 1), date(2016, 4, 1))
    chunks = list(chunked(window, 30))
    assert chunks[0].start == window.start
    assert chunks[-1].end == window.end
    for earlier, later in zip(chunks, chunks[1:]):
        assert earlier.end == later.start


def test_ten_year_backfill_is_split_not_issued_as_one_request():
    window = DateWindow(HISTORICAL_START, date(2026, 8, 20))
    chunks = list(chunked(window, 30))
    assert len(chunks) > 100
    assert all(c.days <= 30 for c in chunks)


def test_empty_window_yields_no_chunks():
    assert list(chunked(DateWindow(date(2026, 1, 1), date(2026, 1, 1)), 30)) == []


def test_chunk_days_must_be_positive():
    with pytest.raises(ValueError):
        list(chunked(DateWindow(date(2026, 1, 1), date(2026, 2, 1)), 0))


def test_window_rejects_reversed_dates():
    with pytest.raises(ValueError, match="precedes start"):
        DateWindow(date(2026, 2, 1), date(2026, 1, 1))


# --- daily / incremental ----------------------------------------------------


def test_cold_start_falls_back_to_full_history():
    window = daily_window(None, date(2026, 8, 21), HISTORICAL_START, 7, TARGET_END)
    assert window.start == HISTORICAL_START


def test_incremental_run_starts_from_checkpoint_minus_lookback():
    window = daily_window(
        date(2026, 8, 15), date(2026, 8, 21), HISTORICAL_START, 7, TARGET_END
    )
    assert window.start == date(2026, 8, 8)  # 15th minus 7 days of overlap


def test_incremental_run_does_not_reprocess_ten_years():
    window = daily_window(
        date(2026, 8, 18), date(2026, 8, 21), HISTORICAL_START, 7, TARGET_END
    )
    assert window.days < 30


def test_lookback_never_reaches_before_historical_start():
    window = daily_window(
        date(2016, 1, 3), date(2016, 1, 10), HISTORICAL_START, 30, TARGET_END
    )
    assert window.start == HISTORICAL_START


def test_target_cutoff_caps_the_window_end():
    window = daily_window(
        date(2026, 8, 10), date(2027, 1, 1), HISTORICAL_START, 0, TARGET_END
    )
    # End is exclusive, so the cutoff day itself is included.
    assert window.end == date(2026, 8, 20)


def test_window_is_empty_once_watermark_passes_cutoff():
    window = daily_window(
        date(2026, 9, 1), date(2026, 9, 2), HISTORICAL_START, 0, TARGET_END
    )
    assert window.is_empty


def test_negative_lookback_rejected():
    with pytest.raises(ValueError):
        daily_window(None, date(2026, 8, 21), HISTORICAL_START, -1)


# --- backfill ---------------------------------------------------------------


def test_backfill_starts_at_historical_start():
    window = backfill_window(None, HISTORICAL_START, TARGET_END, date(2026, 8, 19))
    assert window.start == date(2016, 1, 1)
    assert window.end == date(2026, 8, 20)


def test_backfill_resumes_from_stored_cursor():
    window = backfill_window(
        date(2019, 6, 1), HISTORICAL_START, TARGET_END, date(2026, 8, 19)
    )
    assert window.start == date(2019, 6, 1)


def test_backfill_clamps_to_what_the_source_actually_has():
    """Requested through 2026-08-19 but MODIS only published to 2026-07-12."""
    window = backfill_window(
        None, HISTORICAL_START, TARGET_END, latest_available=date(2026, 7, 12)
    )
    assert window.end == date(2026, 7, 13)
    assert window.end < TARGET_END


def test_backfill_empty_when_cursor_past_availability():
    window = backfill_window(
        date(2026, 8, 1), HISTORICAL_START, TARGET_END, date(2026, 7, 12)
    )
    assert window.is_empty


# --- source availability ----------------------------------------------------


def test_availability_complete_when_source_reaches_request():
    assert (
        availability_status(TARGET_END, date(2026, 8, 19), 1) == "complete"
    )


def test_availability_within_cadence_is_normal_latency():
    """CHIRPS one day behind is latency, not a data problem."""
    assert (
        availability_status(TARGET_END, date(2026, 8, 18), 1)
        == "current_within_cadence"
    )


def test_availability_partial_when_source_lags_beyond_cadence():
    status = availability_status(TARGET_END, date(2026, 7, 12), 16)
    assert status == "partial_source_availability"


def test_availability_none_when_source_has_nothing():
    assert availability_status(TARGET_END, None, 1) == "no_source_data"


def test_missing_days_quantifies_the_gap_rather_than_filling_it():
    assert missing_days(TARGET_END, date(2026, 7, 12)) == 38
    assert missing_days(TARGET_END, date(2026, 8, 19)) == 0
    # A source ahead of the request is not negative missing days.
    assert missing_days(TARGET_END, date(2026, 9, 1)) == 0
