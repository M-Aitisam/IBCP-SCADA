# packages/backend/tests/test_alerts.py
"""Phase 18 — alert rules: persistence, hysteresis, cooldown, deduplication.

The hard problem is not detecting a bad value, it is not crying wolf. A
three-month drought must produce one alert, not ninety, or operators stop
reading them.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.intelligence import alerts as rules
from app.intelligence.hazards import (
    HAZARD_DROUGHT,
    HAZARD_FLOOD,
    HazardResult,
    LEVEL_EXTREME,
    LEVEL_MODERATE,
    LEVEL_NORMAL,
    LEVEL_SEVERE,
    LEVEL_WATCH,
)

TODAY = date(2026, 8, 25)


def hazard(level: str, score: float = 65.0, confidence: float = 0.8, status: str = "ok"):
    return HazardResult(
        hazard=HAZARD_DROUGHT, score=score, level=level, confidence=confidence,
        contributors=[], primary_driver="Rainfall deficit",
        reason="test", status=status,
    )


# --- persistence ----------------------------------------------------------


def test_first_sighting_does_not_alert():
    """One noisy composite must not escalate a district."""
    decision = rules.evaluate(
        hazard(LEVEL_SEVERE), region_id="R1", reference_date=TODAY,
        consecutive_periods=1,
    )
    assert decision.action == "none"
    assert decision.suppressed_by == "persistence"


def test_alert_raises_once_persistence_is_met():
    decision = rules.evaluate(
        hazard(LEVEL_SEVERE), region_id="R1", reference_date=TODAY,
        consecutive_periods=2,
    )
    assert decision.action == "raise"
    assert decision.severity == rules.SEVERITY_WARNING


def test_flood_alerts_immediately():
    """Flood onset is genuinely sudden; a confirmation cycle could cost a day."""
    flood = HazardResult(
        hazard=HAZARD_FLOOD, score=70.0, level=LEVEL_SEVERE, confidence=0.8,
        contributors=[], reason="test",
    )
    decision = rules.evaluate(
        flood, region_id="R1", reference_date=TODAY, consecutive_periods=1
    )
    assert decision.action == "raise"


def test_persistence_gate_applies_only_to_escalation():
    """De-escalating should not have to wait."""
    decision = rules.evaluate(
        hazard(LEVEL_WATCH, score=25.0), region_id="R1", reference_date=TODAY,
        consecutive_periods=1, existing_severity=rules.SEVERITY_CRITICAL,
        existing_status=rules.STATUS_ACTIVE,
    )
    assert decision.action == "deescalate"


# --- deduplication --------------------------------------------------------


def test_ongoing_hazard_updates_rather_than_re_raising():
    """A persistent drought is one situation, not one per night."""
    decision = rules.evaluate(
        hazard(LEVEL_SEVERE), region_id="R1", reference_date=TODAY,
        consecutive_periods=30, existing_severity=rules.SEVERITY_WARNING,
        existing_status=rules.STATUS_ACTIVE,
    )
    assert decision.action == "update"
    assert decision.rule_id.endswith(".ongoing")


def test_dedupe_key_is_stable_across_cycles():
    assert rules.dedupe_key(HAZARD_DROUGHT, "R1", "WARNING") == rules.dedupe_key(
        HAZARD_DROUGHT, "R1", "WARNING"
    )


def test_dedupe_key_separates_regions_hazards_and_severities():
    base = rules.dedupe_key(HAZARD_DROUGHT, "R1", "WARNING")
    assert base != rules.dedupe_key(HAZARD_DROUGHT, "R2", "WARNING")
    assert base != rules.dedupe_key(HAZARD_FLOOD, "R1", "WARNING")
    assert base != rules.dedupe_key(HAZARD_DROUGHT, "R1", "CRITICAL")


def test_alert_id_is_deterministic():
    a = rules.build_alert_id(HAZARD_DROUGHT, "R1", TODAY)
    b = rules.build_alert_id(HAZARD_DROUGHT, "R1", TODAY)
    assert a == b
    assert a.startswith("GV-DROUGHT-20260825-")


# --- escalation and de-escalation ----------------------------------------


def test_worsening_escalates():
    decision = rules.evaluate(
        hazard(LEVEL_EXTREME, score=90.0), region_id="R1", reference_date=TODAY,
        consecutive_periods=2, existing_severity=rules.SEVERITY_ADVISORY,
        existing_status=rules.STATUS_ACTIVE,
    )
    assert decision.action == "escalate"
    assert decision.severity == rules.SEVERITY_CRITICAL
    assert decision.previous_severity == rules.SEVERITY_ADVISORY


def test_return_to_normal_closes_the_alert():
    decision = rules.evaluate(
        hazard(LEVEL_NORMAL, score=5.0), region_id="R1", reference_date=TODAY,
        consecutive_periods=3, existing_severity=rules.SEVERITY_WARNING,
        existing_status=rules.STATUS_ACTIVE,
    )
    assert decision.action == "deescalate"
    assert "normal" in decision.reason.lower()


def test_normal_with_no_open_alert_does_nothing():
    decision = rules.evaluate(
        hazard(LEVEL_NORMAL, score=5.0), region_id="R1", reference_date=TODAY,
        consecutive_periods=5,
    )
    assert decision.action == "none"


# --- hysteresis -----------------------------------------------------------


def test_hysteresis_requires_clearing_the_boundary_by_a_margin():
    """A score sitting on a threshold must not flip the alert every cycle."""
    assert not rules.should_deescalate(current_score=58.0, band_floor=60.0)
    assert not rules.should_deescalate(current_score=53.0, band_floor=60.0)
    assert rules.should_deescalate(current_score=50.0, band_floor=60.0)


# --- cooldown -------------------------------------------------------------


def test_cooldown_blocks_immediate_re_raise():
    decision = rules.evaluate(
        hazard(LEVEL_SEVERE), region_id="R1", reference_date=TODAY,
        consecutive_periods=5, last_resolved_on=date(2026, 8, 24),
    )
    assert decision.action == "none"
    assert decision.suppressed_by == "cooldown"


def test_cooldown_expires():
    decision = rules.evaluate(
        hazard(LEVEL_SEVERE), region_id="R1", reference_date=TODAY,
        consecutive_periods=5, last_resolved_on=date(2026, 8, 1),
    )
    assert decision.action == "raise"


# --- confidence floor -----------------------------------------------------


def test_low_confidence_is_reported_but_not_alerted():
    """Alerting from thin evidence is noise wearing the costume of a warning."""
    decision = rules.evaluate(
        hazard(LEVEL_EXTREME, score=95.0, confidence=0.2), region_id="R1",
        reference_date=TODAY, consecutive_periods=5,
    )
    assert decision.action == "none"
    assert decision.suppressed_by == "low_confidence"


def test_unscored_hazard_never_alerts():
    decision = rules.evaluate(
        hazard(LEVEL_SEVERE, status="insufficient_data"), region_id="R1",
        reference_date=TODAY, consecutive_periods=5,
    )
    assert decision.action == "none"
    assert decision.suppressed_by == "insufficient_data"


def test_insufficient_data_hazard_with_no_score_never_alerts():
    result = HazardResult(
        hazard=HAZARD_DROUGHT, score=None, level="INSUFFICIENT_DATA",
        confidence=None, contributors=[], status="insufficient_data",
    )
    decision = rules.evaluate(
        result, region_id="R1", reference_date=TODAY, consecutive_periods=9
    )
    assert decision.action == "none"


# --- severity mapping and metadata ---------------------------------------


@pytest.mark.parametrize(
    "level,expected",
    [
        (LEVEL_WATCH, rules.SEVERITY_WATCH),
        (LEVEL_MODERATE, rules.SEVERITY_ADVISORY),
        (LEVEL_SEVERE, rules.SEVERITY_WARNING),
        (LEVEL_EXTREME, rules.SEVERITY_CRITICAL),
        (LEVEL_NORMAL, None),
    ],
)
def test_level_maps_to_severity(level, expected):
    assert rules.severity_for(level) == expected


def test_severity_ranks_are_ordered():
    assert (
        rules.SEVERITY_RANK[rules.SEVERITY_WATCH]
        < rules.SEVERITY_RANK[rules.SEVERITY_ADVISORY]
        < rules.SEVERITY_RANK[rules.SEVERITY_WARNING]
        < rules.SEVERITY_RANK[rules.SEVERITY_CRITICAL]
    )


def test_alerts_expire_so_the_board_does_not_fill_with_stale_items():
    assert rules.expiry_for(TODAY) > TODAY


def test_disclaimer_denies_official_authority():
    assert "not an official disaster warning" in rules.DISCLAIMER.lower()
    assert "ndma" in rules.DISCLAIMER.lower()


def test_evidence_is_attached_to_actionable_decisions():
    decision = rules.evaluate(
        hazard(LEVEL_SEVERE), region_id="R1", reference_date=TODAY,
        consecutive_periods=2,
    )
    assert decision.should_write
    assert decision.evidence["score"] == 65.0
    assert decision.evidence["consecutive_periods"] == 2
