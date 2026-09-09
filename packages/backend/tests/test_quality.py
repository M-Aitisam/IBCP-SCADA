# packages/backend/tests/test_quality.py
"""Phase 4 — data quality scoring.

The rule these exist to protect: a missing observation is scored honestly and
never imputed. Everything else is about making the score interrogable.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.intelligence.quality import (
    FLAG_HIGH_CLOUD,
    FLAG_NO_PIXELS,
    FLAG_NO_VALUE,
    FLAG_QUALITY_BAND,
    FLAG_REJECTED,
    FLAG_SPARSE_PIXELS,
    FLAG_STALE,
    STATUS_GOOD,
    STATUS_UNUSABLE,
    aggregate_quality,
    score_observation,
    status_for,
)


def test_clean_observation_scores_well():
    result = score_observation(
        value=0.43, cloud_percentage=5.0, pixel_count=90_000,
        reference_pixel_count=95_000,
    )
    assert result.score >= 90
    assert result.status == STATUS_GOOD


def test_perfect_observation_has_no_concerns():
    result = score_observation(value=0.43, pixel_count=90_000, reference_pixel_count=90_000)
    assert result.score == 100.0
    assert result.flags == []
    assert "No quality concerns" in result.reason


def test_missing_value_is_flagged_never_imputed():
    """The central rule: absence is recorded, not filled in."""
    result = score_observation(value=None, cloud_percentage=95.0, pixel_count=0)
    assert FLAG_NO_VALUE in result.flags
    assert result.status == STATUS_UNUSABLE
    # Nothing in the assessment supplies a substitute value.
    assert "value" not in result.to_row()
    assert result.to_row()["quality_score"] == 0.0


def test_missing_value_is_not_charged_twice_for_pixels():
    """A null value already explains itself; no_pixels would double-count."""
    result = score_observation(value=None, pixel_count=0)
    assert FLAG_NO_VALUE in result.flags
    assert FLAG_NO_PIXELS not in result.flags


def test_cloud_penalty_scales_with_cover():
    light = score_observation(value=0.4, pixel_count=1000, reference_pixel_count=1000)
    moderate = score_observation(
        value=0.4, cloud_percentage=20.0, pixel_count=1000, reference_pixel_count=1000
    )
    heavy = score_observation(
        value=0.4, cloud_percentage=60.0, pixel_count=1000, reference_pixel_count=1000
    )
    assert light.score > moderate.score > heavy.score
    assert FLAG_HIGH_CLOUD in heavy.flags
    assert FLAG_HIGH_CLOUD not in moderate.flags


def test_pipeline_cloud_threshold_still_scores_good():
    """20% is the pipeline's own scene ceiling; it must not read as suspect."""
    result = score_observation(
        value=0.4, cloud_percentage=20.0, pixel_count=5000, reference_pixel_count=5000
    )
    assert result.status == STATUS_GOOD


def test_sparse_pixel_sample_is_penalised():
    result = score_observation(value=0.4, pixel_count=5, reference_pixel_count=5)
    assert FLAG_SPARSE_PIXELS in result.flags
    assert result.score < 90


def test_coverage_is_relative_to_the_region_not_absolute():
    """What counts as enough pixels depends on region area and resolution.

    The same absolute count is fine for a coarse product over a small district
    and poor for a fine product over a large one.
    """
    good = score_observation(value=0.4, pixel_count=200, reference_pixel_count=200)
    poor = score_observation(value=0.4, pixel_count=200, reference_pixel_count=100_000)
    assert good.score > poor.score


def test_staleness_is_measured_in_publication_cycles():
    """A 16-day composite must not be punished for being a 16-day composite."""
    fresh = score_observation(
        value=0.4, pixel_count=1000, reference_pixel_count=1000,
        observation_date=date(2026, 8, 1), as_of=date(2026, 8, 20), cadence_days=16,
    )
    assert FLAG_STALE not in fresh.flags

    stale = score_observation(
        value=0.4, pixel_count=1000, reference_pixel_count=1000,
        observation_date=date(2026, 1, 1), as_of=date(2026, 8, 20), cadence_days=16,
    )
    assert FLAG_STALE in stale.flags


def test_quality_bitfield_is_not_scored_as_a_measurement():
    """A mean over packed QA bits has no physical meaning."""
    result = score_observation(value=1234.0, is_quality_band=True)
    assert FLAG_QUALITY_BAND in result.flags
    assert result.components["scored"] is False
    assert result.score == 100.0


def test_validator_rejection_is_unusable():
    result = score_observation(value=99.0, processing_status="rejected")
    assert result.status == STATUS_UNUSABLE
    assert FLAG_REJECTED in result.flags
    assert result.score == 0.0


def test_score_is_always_within_bounds():
    worst = score_observation(
        value=None, cloud_percentage=100.0, pixel_count=0,
        observation_date=date(2016, 1, 1), as_of=date(2026, 8, 20), cadence_days=1,
    )
    assert 0.0 <= worst.score <= 100.0


def test_every_penalty_carries_a_flag():
    """A deduction nobody can explain is a decoration, not a score."""
    result = score_observation(
        value=0.4, cloud_percentage=50.0, pixel_count=3, reference_pixel_count=100_000
    )
    assert result.score < 100
    assert result.flags, "a reduced score must name its reasons"
    assert result.reason and result.reason != "No quality concerns detected."


@pytest.mark.parametrize(
    "score,expected",
    [(95, "GOOD"), (80, "GOOD"), (70, "FAIR"), (60, "FAIR"), (40, "POOR"), (10, "UNUSABLE")],
)
def test_status_bands(score, expected):
    assert status_for(score) == expected


def test_aggregate_distinguishes_absent_from_zero():
    """No observations to assess is not the same as observations scoring zero."""
    assert aggregate_quality([]) is None
    assert aggregate_quality([0.0, 0.0]) == 0.0
    assert aggregate_quality([90.0, 70.0]) == 80.0


def test_to_row_shape_matches_the_columns():
    row = score_observation(value=0.4, pixel_count=100).to_row()
    assert set(row) == {
        "quality_score",
        "quality_status",
        "quality_flags",
        "quality_reason",
    }
    assert "version" in row["quality_flags"]
