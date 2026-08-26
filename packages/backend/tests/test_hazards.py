# packages/backend/tests/test_hazards.py
"""Phases 7/10/13/14 — hazard scoring, fusion and explainability.

These pin the scientific commitments, not just the arithmetic:

  - temperature and rainfall are scored by anomaly, never by absolute value
  - a score built on thin evidence carries lower confidence
  - too few components produces insufficient_data, never a confident number
  - contributors are always visible
"""
from __future__ import annotations

import pytest

from app.intelligence.hazards import (
    DROUGHT_WEIGHTS,
    HAZARD_CROP,
    HAZARD_DROUGHT,
    HAZARD_FLOOD,
    HAZARD_HEAT,
    HAZARD_MULTI,
    LEVEL_EXTREME,
    LEVEL_INSUFFICIENT,
    LEVEL_NORMAL,
    MIN_COMPONENTS,
    MULTI_HAZARD_WEIGHTS,
    RISK_CRITICAL,
    RISK_LOW,
    crop_health,
    drought,
    explain,
    heat_stress,
    multi_hazard,
)


# --- drought --------------------------------------------------------------


def test_severe_deficit_produces_a_high_drought_score():
    result = drought(rainfall_z=-2.5, ndvi_z=-2.0, lst_z=1.8, baseline_years=10)
    assert result.score > 55
    assert result.level in ("SEVERE", "EXTREME")
    assert result.primary_driver == "Rainfall deficit"


def test_normal_conditions_score_low():
    result = drought(rainfall_z=0.1, ndvi_z=0.0, lst_z=-0.2, baseline_years=10)
    assert result.score < 20
    assert result.level == LEVEL_NORMAL


def test_wet_anomaly_is_not_drought():
    """Sign matters: an unusually WET season must not read as drought."""
    result = drought(rainfall_z=2.5, ndvi_z=1.5, lst_z=-1.0, baseline_years=10)
    assert result.score < 20
    assert result.level == LEVEL_NORMAL


def test_single_component_refuses_to_score():
    """One input is not a composite index, and must not pretend to be."""
    result = drought(rainfall_z=-3.0)
    assert result.score is None
    assert result.level == LEVEL_INSUFFICIENT
    assert result.status == "insufficient_data"
    # The reason must say what was missing, not just that something was.
    assert "1 component" in result.reason
    assert MIN_COMPONENTS == 2


def test_drought_weights_sum_to_one():
    assert sum(DROUGHT_WEIGHTS.values()) == pytest.approx(1.0)


def test_missing_component_renormalises_rather_than_dragging_down():
    """An absent input must not act like a zero-stress input."""
    partial = drought(rainfall_z=-3.0, ndvi_z=-3.0, baseline_years=10)
    full = drought(rainfall_z=-3.0, ndvi_z=-3.0, lst_z=3.0, baseline_years=10)
    # Both extreme; the two-component version must not be dramatically lower
    # merely because a third input was missing.
    assert partial.score > 90
    assert full.score > 90


# --- heat -----------------------------------------------------------------


def test_heat_uses_anomaly_not_absolute_temperature():
    """45 C is unremarkable in Sibi and extreme in Skardu.

    The score must come from the departure, so an absolute value alone cannot
    produce one.
    """
    no_anomaly = heat_stress(lst_day_value=45.0)
    assert no_anomaly.score is None
    assert no_anomaly.status == "insufficient_data"

    with_anomaly = heat_stress(lst_day_z=3.0, baseline_years=10)
    assert with_anomaly.score is not None
    assert with_anomaly.score > 80


def test_cool_anomaly_is_not_heat_stress():
    result = heat_stress(lst_day_z=-2.5, lst_night_z=-2.0, baseline_years=10)
    assert result.score == 0.0
    assert result.level == LEVEL_NORMAL


# --- crop health ----------------------------------------------------------


def test_crop_health_combines_level_and_anomalies():
    stressed = crop_health(
        ndvi=0.15, ndvi_z=-2.0, rainfall_z=-2.0, lst_z=2.0, baseline_years=10
    )
    healthy = crop_health(
        ndvi=0.65, ndvi_z=0.5, rainfall_z=0.3, lst_z=-0.2, baseline_years=10
    )
    assert stressed.score > healthy.score
    assert healthy.score < 20


def test_crop_health_scores_stress_not_health():
    """Direction matters for composition: every hazard scores severity."""
    result = crop_health(ndvi=0.1, ndvi_z=-2.5, baseline_years=10)
    assert result.score > 60, "poor vegetation must produce a HIGH stress score"


def test_crop_health_reports_its_primary_driver():
    result = crop_health(ndvi=0.5, ndvi_z=-0.1, rainfall_z=-3.0, baseline_years=10)
    assert result.primary_driver is not None
    assert result.contributors


# --- confidence -----------------------------------------------------------


def test_thin_baseline_lowers_confidence():
    thin = drought(rainfall_z=-2.0, ndvi_z=-2.0, lst_z=1.0, baseline_years=3)
    thick = drought(rainfall_z=-2.0, ndvi_z=-2.0, lst_z=1.0, baseline_years=10)
    assert thin.confidence < thick.confidence


def test_poor_data_quality_lowers_confidence():
    poor = drought(rainfall_z=-2.0, ndvi_z=-2.0, baseline_years=10, data_quality=40)
    good = drought(rainfall_z=-2.0, ndvi_z=-2.0, baseline_years=10, data_quality=95)
    assert poor.confidence < good.confidence


def test_fewer_components_lowers_confidence():
    two = drought(rainfall_z=-2.0, ndvi_z=-2.0, baseline_years=10, data_quality=95)
    three = drought(
        rainfall_z=-2.0, ndvi_z=-2.0, lst_z=1.0, baseline_years=10, data_quality=95
    )
    assert two.confidence < three.confidence


def test_confidence_is_a_probability():
    result = drought(rainfall_z=-2.0, ndvi_z=-2.0, lst_z=1.0, baseline_years=10)
    assert 0.0 <= result.confidence <= 1.0


# --- multi-hazard fusion --------------------------------------------------


def _fusable():
    return {
        HAZARD_DROUGHT: drought(rainfall_z=-2.5, ndvi_z=-2.0, lst_z=1.5, baseline_years=10),
        HAZARD_CROP: crop_health(ndvi=0.15, ndvi_z=-2.0, rainfall_z=-2.5, baseline_years=10),
        HAZARD_HEAT: heat_stress(lst_day_z=1.5, lst_night_z=1.2, baseline_years=10),
    }


def test_fusion_produces_a_risk_level():
    result = multi_hazard({**_fusable(), HAZARD_FLOOD: None})
    assert result.score is not None
    assert result.level in (RISK_LOW, "MEDIUM", "HIGH", RISK_CRITICAL)


def test_absent_hazard_is_excluded_not_treated_as_zero():
    """Absent evidence is not evidence of absence.

    Scoring an unassessed flood risk as 0 would drag the fused score down and
    imply the region had been checked and found safe.
    """
    without_flood = multi_hazard({**_fusable(), HAZARD_FLOOD: None})
    only_three = multi_hazard(_fusable())
    assert without_flood.score == only_three.score


def test_fusion_needs_at_least_two_hazards():
    lonely = multi_hazard(
        {HAZARD_DROUGHT: drought(rainfall_z=-2.5, ndvi_z=-2.0, baseline_years=10)}
    )
    assert lonely.score is None
    assert lonely.status == "insufficient_data"


def test_partial_coverage_lowers_fused_confidence():
    three = multi_hazard({**_fusable(), HAZARD_FLOOD: None})
    two = multi_hazard(
        {
            HAZARD_DROUGHT: _fusable()[HAZARD_DROUGHT],
            HAZARD_CROP: _fusable()[HAZARD_CROP],
        }
    )
    assert two.confidence < three.confidence


def test_exposure_amplifies_but_cannot_invent_risk():
    calm = {
        HAZARD_DROUGHT: drought(rainfall_z=0.0, ndvi_z=0.0, lst_z=0.0, baseline_years=10),
        HAZARD_HEAT: heat_stress(lst_day_z=0.0, lst_night_z=0.0, baseline_years=10),
    }
    result = multi_hazard(calm, exposure_factor=1.0)
    assert result.score == 0.0, "exposure must not create hazard where there is none"


def test_exposure_is_a_visible_modifier_not_a_hidden_multiplier():
    result = multi_hazard({**_fusable(), HAZARD_FLOOD: None}, exposure_factor=0.8)
    modifiers = [c for c in result.contributors if c.component == "exposure"]
    assert modifiers, "the exposure adjustment must be shown, not applied silently"


def test_multi_hazard_weights_sum_to_one():
    assert sum(MULTI_HAZARD_WEIGHTS.values()) == pytest.approx(1.0)


# --- explainability (Phase 14) -------------------------------------------


def test_explain_ranks_contributors_by_influence():
    result = multi_hazard({**_fusable(), HAZARD_FLOOD: None})
    payload = explain(result)
    contributions = [c["contribution"] for c in payload["contributors"]]
    assert contributions == sorted(contributions, reverse=True)


def test_explain_never_claims_official_authority():
    payload = explain(multi_hazard({**_fusable(), HAZARD_FLOOD: None}))
    assert payload["is_official_warning"] is False
    assert "satellite-derived" in payload["classification"]


def test_every_scored_hazard_exposes_its_contributors():
    """A score that cannot be interrogated will not be trusted."""
    for result in (
        drought(rainfall_z=-2.0, ndvi_z=-1.0, baseline_years=10),
        crop_health(ndvi=0.2, ndvi_z=-1.5, baseline_years=10),
        heat_stress(lst_day_z=2.0, lst_night_z=1.0, baseline_years=10),
    ):
        assert result.contributors
        for contributor in result.contributors:
            payload = contributor.to_json()
            assert payload["component"] and payload["label"]
            assert payload["weight"] is not None
            assert payload["contribution"] is not None
