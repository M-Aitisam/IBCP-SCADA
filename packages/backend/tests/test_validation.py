# packages/backend/tests/test_validation.py
"""Invalid values are rejected with a reason, never silently clamped."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app.ingestion.registry import DATASETS
from app.ingestion.validation import (
    BAD_TIMESTAMP,
    FUTURE_TIMESTAMP,
    MISSING_SOURCE_IMAGE,
    NOT_FINITE,
    NULL_VALUE,
    OUT_OF_RANGE,
    UNKNOWN_METRIC,
    UNKNOWN_REGION,
    Validator,
)

REGIONS = {"R1", "R2"}


def validator(dataset: str = "chirps", **kwargs) -> Validator:
    return Validator(DATASETS[dataset], REGIONS, **kwargs)


def test_valid_record_is_accepted(base_record):
    outcome = validator().validate([base_record])
    assert len(outcome.accepted) == 1
    assert not outcome.rejected


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_infinity_rejected(base_record, bad):
    record = replace(base_record, value=bad)
    outcome = validator().validate([record])
    assert outcome.rejected[0][1] == NOT_FINITE
    assert not outcome.accepted


def test_negative_rainfall_rejected(base_record):
    record = replace(base_record, value=-5.0)
    outcome = validator().validate([record])
    assert outcome.rejected[0][1] == OUT_OF_RANGE


def test_rejected_value_is_not_clamped(base_record):
    record = replace(base_record, value=-5.0)
    outcome = validator().validate([record])
    # The original value survives untouched for the log; it just is not stored.
    assert outcome.rejected[0][0].value == -5.0
    assert outcome.rejected[0][0].processing_status == "rejected"


def test_ndvi_outside_minus_one_to_one_rejected():
    record_ok = _mod13q1_record(value=0.65)
    record_bad = _mod13q1_record(value=1.4)
    outcome = validator("mod13q1").validate([record_ok, record_bad])
    assert len(outcome.accepted) == 1
    assert outcome.rejected[0][1] == OUT_OF_RANGE


def test_unscaled_ndvi_integer_is_rejected():
    """A raw 6500 reaching storage means the scale factor was skipped."""
    outcome = validator("mod13q1").validate([_mod13q1_record(value=6500)])
    assert outcome.rejected[0][1] == OUT_OF_RANGE


def test_lst_kelvin_mistaken_for_celsius_is_rejected():
    """300 K stored as Celsius is physically impossible and must not pass."""
    record = _record(
        dataset="mod11a2",
        asset="MODIS/061/MOD11A2",
        metric="lst_day_c",
        value=300.0,
        unit="celsius",
    )
    outcome = validator("mod11a2").validate([record])
    assert outcome.rejected[0][1] == OUT_OF_RANGE

    plausible = replace(record, value=26.85)
    assert len(validator("mod11a2").validate([plausible]).accepted) == 1


def test_quality_band_skips_range_check():
    """QA bitfields have no physical range."""
    record = _record(
        dataset="mod11a2",
        asset="MODIS/061/MOD11A2",
        metric="qc_day",
        value=65.0,
        unit="bitfield",
    )
    assert len(validator("mod11a2").validate([record]).accepted) == 1


def test_null_value_kept_as_visible_gap_by_default(base_record):
    """All-masked regions are recorded as null, not dropped and not filled."""
    record = replace(base_record, value=None)
    outcome = validator().validate([record])
    assert len(outcome.accepted) == 1
    assert outcome.accepted[0].value is None
    assert outcome.accepted[0].processing_status == "no_valid_pixels"


def test_null_value_rejected_when_disallowed(base_record):
    record = replace(base_record, value=None)
    outcome = validator(allow_null_values=False).validate([record])
    assert outcome.rejected[0][1] == NULL_VALUE


def test_unknown_region_rejected(base_record):
    record = replace(base_record, region_id="NOT_IN_ROI")
    outcome = validator().validate([record])
    assert outcome.rejected[0][1] == UNKNOWN_REGION


def test_unknown_metric_rejected(base_record):
    record = replace(base_record, metric="soil_moisture")
    outcome = validator().validate([record])
    assert outcome.rejected[0][1] == UNKNOWN_METRIC


def test_missing_source_image_id_rejected(base_record):
    record = replace(base_record, source_image_id="")
    outcome = validator().validate([record])
    assert outcome.rejected[0][1] == MISSING_SOURCE_IMAGE


def test_naive_timestamp_rejected(base_record):
    record = replace(base_record, observation_timestamp=datetime(2026, 8, 1))
    outcome = validator().validate([record])
    assert outcome.rejected[0][1] == BAD_TIMESTAMP


def test_future_timestamp_rejected(base_record):
    """A satellite cannot observe next year; this catches a seconds/ms mix-up."""
    future = datetime.now(timezone.utc) + timedelta(days=400)
    record = replace(
        base_record, observation_timestamp=future, observation_date=future.date()
    )
    outcome = validator().validate([record])
    assert outcome.rejected[0][1] == FUTURE_TIMESTAMP


def test_rejection_summary_counts_by_reason(base_record):
    records = [
        replace(base_record, value=float("nan"), region_id="R1"),
        replace(base_record, value=float("inf"), region_id="R2"),
        replace(base_record, value=-1.0, source_image_id="img2"),
    ]
    outcome = validator().validate(records)
    assert outcome.rejection_summary == {NOT_FINITE: 2, OUT_OF_RANGE: 1}


def test_bool_is_not_accepted_as_numeric(base_record):
    record = replace(base_record, value=True)
    outcome = validator().validate([record])
    assert outcome.rejected[0][1] == NOT_FINITE


# --- helpers ----------------------------------------------------------------


def _record(*, dataset, asset, metric, value, unit, region="R1"):
    from app.databases.timestampdb.repository import ObservationRecord

    return ObservationRecord(
        dataset=dataset,
        dataset_asset_id=asset,
        region_id=region,
        metric=metric,
        observation_timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
        observation_date=date(2026, 8, 1),
        source_image_id="img1",
        unit=unit,
        value=value,
    )


def _mod13q1_record(value):
    return _record(
        dataset="mod13q1",
        asset="MODIS/061/MOD13Q1",
        metric="ndvi",
        value=value,
        unit="index",
    )
