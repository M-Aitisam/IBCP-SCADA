# packages/backend/tests/test_condition.py
"""Traffic-light condition classification.

Two different rules are deliberately in play, and the tests pin both:
vegetation indices are classified from the reading, while temperature and
rainfall are classified from their departure from the region's own seasonal
normal — there is no context-free "too hot".
"""
from __future__ import annotations

import pytest

from app.services.geovision_service import (
    ANOMALY_CLASSIFIED_METRICS,
    CONDITION_CRITICAL,
    CONDITION_HEALTHY,
    CONDITION_LABELS,
    CONDITION_RANK,
    CONDITION_STRESSED,
    CONDITION_UNKNOWN,
    CONDITION_WATCH,
    VALUE_CLASSIFIED_METRICS,
    WATCH_Z_ADVISORY,
    WATCH_Z_CRITICAL,
    WATCH_Z_HIGH,
    condition_from_anomaly,
    condition_from_value,
)


# --- vegetation: classified from the value -------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.80, CONDITION_HEALTHY),
        (0.45, CONDITION_HEALTHY),   # exactly on the boundary
        (0.44, CONDITION_WATCH),
        (0.30, CONDITION_WATCH),
        (0.29, CONDITION_STRESSED),
        (0.15, CONDITION_STRESSED),
        (0.14, CONDITION_CRITICAL),
        (-0.20, CONDITION_CRITICAL),
    ],
)
def test_vegetation_condition_bands(value, expected):
    assert condition_from_value(value) == expected


def test_missing_vegetation_value_is_unknown():
    assert condition_from_value(None) == CONDITION_UNKNOWN


# --- temperature / rainfall: classified from the anomaly ------------------


@pytest.mark.parametrize(
    "z,expected",
    [
        (0.0, CONDITION_HEALTHY),
        (1.4, CONDITION_HEALTHY),
        (WATCH_Z_ADVISORY, CONDITION_WATCH),
        (1.9, CONDITION_WATCH),
        (WATCH_Z_HIGH, CONDITION_STRESSED),
        (2.9, CONDITION_STRESSED),
        (WATCH_Z_CRITICAL, CONDITION_CRITICAL),
        (5.0, CONDITION_CRITICAL),
    ],
)
def test_anomaly_condition_thresholds(z, expected):
    assert condition_from_anomaly(z) == expected


def test_anomaly_severity_is_symmetric():
    """A cold or dry extreme is as notable as a hot or wet one.

    The magnitude of the departure drives severity; the sign says which way,
    and that belongs in the detail text, not the colour.
    """
    for magnitude in (1.6, 2.4, 3.5):
        assert condition_from_anomaly(magnitude) == condition_from_anomaly(-magnitude)


def test_no_baseline_is_unknown_not_healthy():
    """The critical distinction for this feature.

    A region with no seasonal baseline has not been assessed. Reporting it as
    healthy would claim we checked and found it normal, which is false — and on
    the map it would paint reassuring green over an unknown.
    """
    assert condition_from_anomaly(None) == CONDITION_UNKNOWN
    assert condition_from_anomaly(None) != CONDITION_HEALTHY


# --- ordering and coverage ------------------------------------------------


def test_worse_conditions_rank_higher():
    assert (
        CONDITION_RANK[CONDITION_HEALTHY]
        < CONDITION_RANK[CONDITION_WATCH]
        < CONDITION_RANK[CONDITION_STRESSED]
        < CONDITION_RANK[CONDITION_CRITICAL]
    )


def test_unknown_never_wins_the_overall_rollup():
    """`max` over ranks picks the worst real level, ignoring unknown."""
    assert CONDITION_RANK[CONDITION_UNKNOWN] < CONDITION_RANK[CONDITION_HEALTHY]


def test_every_level_has_a_label():
    for level in CONDITION_RANK:
        assert CONDITION_LABELS[level]


def test_each_metric_is_classified_exactly_one_way():
    """No metric may be both value- and anomaly-classified."""
    overlap = set(VALUE_CLASSIFIED_METRICS) & set(ANOMALY_CLASSIFIED_METRICS)
    assert not overlap


def test_temperature_and_rainfall_are_never_value_classified():
    """Guards the scientific point: no absolute 'too hot' / 'too wet'."""
    for metric in ("lst_day_c", "lst_night_c", "rainfall_mm"):
        assert metric in ANOMALY_CLASSIFIED_METRICS
        assert metric not in VALUE_CLASSIFIED_METRICS
