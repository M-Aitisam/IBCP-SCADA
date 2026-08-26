# packages/backend/tests/test_flood_patterns.py
"""Flood detection, hotspots, recovery and geometry (§9, §12, §23).

The load-bearing test in this file is
`test_absolute_water_fraction_alone_never_detects_flooding`. Smooth dry
surfaces are as dark to C-band radar as open water, and a real Balochistan
district in this database reads 0.61 water fraction in a dry August. Any
implementation that flags that as flooding is wrong.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.intelligence import flood, geo, patterns

PARAMS = flood.DEFAULT_PARAMETERS


def assess(current, baseline, **kwargs):
    kwargs.setdefault("baseline_years", 8)
    kwargs.setdefault("region_area_km2", 2000.0)
    kwargs.setdefault("data_quality", 85.0)
    return flood.assess(
        region_id="R1", current_fraction=current, baseline_fraction=baseline, **kwargs
    )


# --- the central correctness rule -----------------------------------------


def test_absolute_water_fraction_alone_never_detects_flooding():
    """A permanently dark district is not a permanently flooded one.

    Sibi genuinely reads ~0.61 water fraction in a dry August because sand
    sheets and bare rock are specular in VV. Judged against its own baseline it
    is normal; judged absolutely it would be a standing catastrophe.
    """
    result = assess(0.614, 0.600, baseline_stddev=0.03)
    assert result.severity == flood.NONE
    assert result.status == "ok"


def test_a_real_departure_is_detected():
    result = assess(0.32, 0.08, baseline_stddev=0.02, region_area_km2=1900.0)
    assert result.severity in (flood.MAJOR, flood.SEVERE)
    assert result.new_water_fraction == pytest.approx(0.24, abs=0.001)
    # 24% of 1900 km2.
    assert result.new_flooded_area_km2 == pytest.approx(456.0, abs=1.0)


def test_severity_rises_with_new_water():
    severities = [
        assess(base + delta, base).severity
        for base, delta in [(0.05, 0.01), (0.05, 0.07), (0.05, 0.15), (0.05, 0.25), (0.05, 0.40)]
    ]
    assert severities == [flood.NONE, flood.MINOR, flood.MODERATE, flood.MAJOR, flood.SEVERE]


# --- refusing to guess ----------------------------------------------------


def test_no_baseline_is_unknown_not_none():
    """UNKNOWN means 'cannot tell'; NONE means 'checked and clear'."""
    result = assess(0.4, None)
    assert result.severity == flood.UNKNOWN
    assert result.severity != flood.NONE
    assert result.status == "insufficient_data"


def test_thin_baseline_is_unknown():
    result = assess(0.4, 0.05, baseline_years=1)
    assert result.severity == flood.UNKNOWN
    assert "1 year" in result.reason


def test_no_observation_is_unknown():
    assert assess(None, 0.05).severity == flood.UNKNOWN


def test_poor_data_quality_withholds_a_detection():
    result = assess(0.4, 0.05, data_quality=10.0)
    assert result.severity == flood.UNKNOWN
    assert "quality" in result.reason


def test_area_is_omitted_rather_than_guessed_without_region_area():
    result = assess(0.3, 0.05, region_area_km2=None)
    assert result.new_flooded_area_km2 is None
    assert result.severity != flood.UNKNOWN, "severity does not need the area"


# --- standardised departure ----------------------------------------------


def test_a_small_but_very_unusual_rise_is_caught():
    """A region with a tight baseline can flood without a large absolute rise."""
    result = assess(0.09, 0.05, baseline_stddev=0.005)
    assert result.z_score is not None and result.z_score >= PARAMS.z_score_alert
    assert result.severity != flood.NONE


def test_parameters_are_reported_with_every_assessment():
    """Thresholds must travel with the result, not be folklore."""
    result = assess(0.3, 0.05)
    assert result.parameters["water_threshold_db"] == PARAMS.water_threshold_db
    assert "severity_bands" in result.parameters
    assert "Sentinel-1" in result.to_json()["method"]


# --- progression ----------------------------------------------------------


def test_progression_detects_expansion_and_recession():
    small = assess(0.15, 0.05)
    large = assess(0.30, 0.05)
    assert flood.progression(large, small)["direction"] == "EXPANDING"
    assert flood.progression(small, large)["direction"] == "RECEDING"
    assert flood.progression(large, large)["direction"] == "STABLE"


def test_progression_without_history_is_unknown():
    assert flood.progression(assess(0.3, 0.05), None)["direction"] == "UNKNOWN"


# --- hotspots -------------------------------------------------------------

ADJACENCY = {
    "A": ["B"], "B": ["A", "C"], "C": ["B"],
    "X": ["Y"], "Y": ["X"],
    "Z": [],
}


def test_contiguous_regions_form_one_hotspot():
    hotspots = patterns.detect_hotspots(
        hazard_type="drought",
        scores={"A": 70.0, "B": 65.0, "C": 60.0},
        adjacency=ADJACENCY,
    )
    assert len(hotspots) == 1
    assert hotspots[0].region_ids == ["A", "B", "C"]


def test_scattered_regions_do_not_form_a_hotspot():
    """Seven scattered stressed districts are seven local problems."""
    hotspots = patterns.detect_hotspots(
        hazard_type="drought",
        scores={"A": 70.0, "X": 70.0, "Z": 70.0},
        adjacency=ADJACENCY,
    )
    assert hotspots == []


def test_a_pair_is_below_the_minimum_cluster_size():
    hotspots = patterns.detect_hotspots(
        hazard_type="drought", scores={"X": 80.0, "Y": 80.0}, adjacency=ADJACENCY
    )
    assert hotspots == []


def test_regions_below_threshold_are_excluded():
    hotspots = patterns.detect_hotspots(
        hazard_type="drought",
        scores={"A": 70.0, "B": 10.0, "C": 65.0},
        adjacency=ADJACENCY,
    )
    # B is healthy, so A and C are no longer connected to each other.
    assert hotspots == []


def test_cluster_id_is_stable_for_the_same_members():
    a = patterns.build_cluster_id("drought", ["A", "B", "C"])
    b = patterns.build_cluster_id("drought", ["C", "B", "A"])
    assert a == b


def test_growth_counts_newly_added_regions():
    hotspot = patterns.detect_hotspots(
        hazard_type="drought",
        scores={"A": 70.0, "B": 65.0, "C": 60.0},
        adjacency=ADJACENCY,
    )[0]
    added, rate = patterns.hotspot_growth(hotspot, {"A", "B"})
    assert added == 1
    assert rate == pytest.approx(0.5)


# --- recovery -------------------------------------------------------------


def recovery(pre, low, current, **kwargs):
    return patterns.assess_recovery(
        region_id="R1", metric="ndvi", pre_event_value=pre,
        minimum_value=low, current_value=current, **kwargs
    )


def test_recovery_is_measured_from_the_loss_not_from_zero():
    """0.48 -> 0.29 -> 0.36 recovered 37% of what it lost, not 75% of NDVI."""
    result = recovery(0.48, 0.29, 0.36)
    assert result.recovery_pct == pytest.approx(36.8, abs=0.2)


def test_recovery_states_span_the_range():
    assert recovery(0.48, 0.29, 0.30).status == patterns.STALLED
    assert recovery(0.48, 0.29, 0.35).status == patterns.SLOW_RECOVERY
    assert recovery(0.48, 0.29, 0.42).status == patterns.RECOVERING
    assert recovery(0.48, 0.29, 0.47).status == patterns.RECOVERED


def test_no_measurable_decline_is_unknown_not_recovered():
    result = recovery(0.48, 0.48, 0.48)
    assert result.status == patterns.UNKNOWN
    assert "no loss to recover from" in result.reason


def test_missing_inputs_are_unknown():
    assert recovery(None, 0.29, 0.36).status == patterns.UNKNOWN
    assert recovery(0.48, None, 0.36).status == patterns.UNKNOWN
    assert recovery(0.48, 0.29, None).status == patterns.UNKNOWN


def test_recovery_is_clamped_and_never_negative():
    assert recovery(0.48, 0.29, 0.60).recovery_pct == 100.0
    assert recovery(0.48, 0.29, 0.20).recovery_pct == 0.0


def test_recovery_is_labelled_as_a_satellite_indicator():
    """NDVI returning is not a livelihood restored."""
    payload = recovery(0.48, 0.29, 0.36).to_json()
    assert payload["interpretation"] == "satellite-derived recovery indicator"


# --- geometry -------------------------------------------------------------

SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]}


def test_area_of_a_one_degree_box_is_about_right():
    # ~12,308 km2 at the equator; the spherical-excess form is within ~0.5%.
    assert flood is not None
    assert geo.polygon_area_km2(SQUARE) == pytest.approx(12308, rel=0.01)


def test_area_ignores_winding_order():
    reversed_square = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
    }
    assert geo.polygon_area_km2(reversed_square) == pytest.approx(
        geo.polygon_area_km2(SQUARE)
    )


def test_non_areal_geometry_has_no_area():
    assert geo.polygon_area_km2({"type": "Point", "coordinates": [0, 0]}) is None
    assert geo.polygon_area_km2(None) is None


def test_multipolygon_area_sums_its_parts():
    multi = {
        "type": "MultiPolygon",
        "coordinates": [SQUARE["coordinates"], SQUARE["coordinates"]],
    }
    assert geo.polygon_area_km2(multi) == pytest.approx(
        2 * geo.polygon_area_km2(SQUARE), rel=1e-6
    )


def test_centroid_and_bbox():
    assert geo.centroid(SQUARE) == pytest.approx((0.5, 0.5))
    assert geo.bounding_box(SQUARE) == (0, 0, 1, 1)


def test_adjacency_is_symmetric():
    facts = {
        "A": geo.RegionGeometryFacts("A", bbox=(0, 0, 1, 1)),
        "B": geo.RegionGeometryFacts("B", bbox=(1, 1, 2, 2)),
        "C": geo.RegionGeometryFacts("C", bbox=(50, 50, 51, 51)),
    }
    adjacency = geo.build_adjacency(facts)
    assert "B" in adjacency["A"] and "A" in adjacency["B"]
    assert adjacency["C"] == []


def test_connected_components_groups_only_the_contiguous():
    clusters = geo.connected_components(["A", "B", "C", "Z"], ADJACENCY)
    assert ["A", "B", "C"] in clusters
    assert ["Z"] in clusters
    # Largest first, so the headline cluster leads.
    assert len(clusters[0]) >= len(clusters[-1])
