# packages/backend/tests/test_events.py
"""Hazard event lifecycle (§10/§11).

The rules these protect, in order of how badly they would hurt if broken:

  1. A missing observation NEVER resolves an open event.
  2. One open event per (hazard, region) — observations update, not duplicate.
  3. Onset and recovery thresholds differ (hysteresis), so an event cannot
     flap open and closed on a score sitting at the boundary.
  4. Escalation is visible as its own state, not buried in a severity label.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.intelligence import events as ev

TODAY = date(2026, 8, 25)


def state(
    status=ev.DETECTED, score=45.0, peak=45.0, first=TODAY - timedelta(days=3),
    periods=1, below=0,
):
    return ev.EventState(
        event_id="FLOOD-2026-0001", hazard_type=ev.FLOOD, region_id="R1",
        status=status, severity="MODERATE", current_score=score, peak_score=peak,
        onset_score=45.0, first_detected_at=first, last_updated_at=TODAY,
        consecutive_periods=periods, below_recovery_periods=below,
    )


def decide(score, existing=None, hazard=ev.FLOOD, quality=85.0, status="ok"):
    return ev.evaluate(
        hazard_type=hazard, region_id="R1", score=score, severity="MODERATE",
        reference_date=TODAY, existing=existing, data_quality=quality,
        hazard_status=status,
    )


# --- opening --------------------------------------------------------------


def test_below_onset_opens_nothing():
    d = decide(20.0)
    assert d.action == "none"
    assert "onset threshold" in d.reason


def test_crossing_onset_opens_an_event():
    d = decide(50.0)
    assert d.action == "open"
    assert d.status == ev.DETECTED
    assert d.transition == "NONE->DETECTED"


def test_onset_thresholds_differ_per_hazard():
    """Flood opens sooner than crop stress: onset speed differs by hazard."""
    assert ev.rules_for(ev.FLOOD).onset_score < ev.rules_for(ev.CROP).onset_score


def test_flood_confirms_faster_than_drought():
    """Flood onset is sudden; waiting a second cycle could be a day too late."""
    assert (
        ev.rules_for(ev.FLOOD).confirm_after_periods
        < ev.rules_for(ev.DROUGHT).confirm_after_periods
    )


# --- the safety rule ------------------------------------------------------


def test_missing_observation_never_resolves_an_open_event():
    """The most dangerous possible bug in this module.

    A gap in satellite coverage is not evidence a flood ended. Resolving on
    absent data would quietly close real emergencies.
    """
    open_event = state(status=ev.CONFIRMED)
    d = decide(None, existing=open_event, status="insufficient_data")
    assert d.action == "none"
    assert d.status == ev.CONFIRMED, "the event must keep its state"
    assert "rather than resolved on missing data" in d.reason


def test_missing_observation_opens_nothing():
    d = decide(None, status="insufficient_data")
    assert d.action == "none"
    assert d.suppressed_by == "insufficient_data"


# --- deduplication --------------------------------------------------------


def test_an_open_event_is_updated_not_duplicated():
    d = decide(50.0, existing=state(status=ev.CONFIRMED, score=48.0))
    assert d.action == "update"
    assert d.action != "open"


# --- hysteresis -----------------------------------------------------------


def test_onset_and_recovery_thresholds_differ():
    """Without a gap, a score at the boundary flips the event every cycle."""
    for hazard in (ev.FLOOD, ev.DROUGHT, ev.HEAT, ev.CROP, ev.MULTI):
        rules = ev.rules_for(hazard)
        assert rules.recovery_score < rules.onset_score


def test_a_single_quiet_cycle_does_not_resolve():
    rules = ev.rules_for(ev.FLOOD)
    d = decide(rules.recovery_score - 5, existing=state(status=ev.CONFIRMED, below=0))
    assert d.action == "update"
    assert d.status == ev.DECLINING


def test_sustained_recovery_resolves():
    rules = ev.rules_for(ev.FLOOD)
    below = rules.resolve_after_periods - 1
    d = decide(rules.recovery_score - 5, existing=state(status=ev.DECLINING, below=below))
    assert d.action == "resolve"
    assert d.status == ev.RESOLVED


# --- lifecycle transitions ------------------------------------------------


def test_a_sharp_rise_escalates():
    d = decide(70.0, existing=state(status=ev.CONFIRMED, score=50.0, peak=50.0))
    assert d.status == ev.ESCALATING
    assert d.change_from_previous == pytest.approx(20.0)


def test_a_new_high_is_peak():
    d = decide(52.0, existing=state(status=ev.ESCALATING, score=50.0, peak=51.0))
    assert d.status == ev.PEAK
    assert d.is_peak


def test_a_sharp_fall_declines():
    d = decide(45.0, existing=state(status=ev.PEAK, score=70.0, peak=70.0))
    assert d.status == ev.DECLINING


def test_persistence_promotes_detected_to_confirmed():
    rules = ev.rules_for(ev.DROUGHT)
    existing = state(status=ev.DETECTED, score=50.0, peak=50.0,
                     periods=rules.confirm_after_periods - 1)
    d = ev.evaluate(
        hazard_type=ev.DROUGHT, region_id="R1", score=50.0, severity="MODERATE",
        reference_date=TODAY, existing=existing, data_quality=85.0,
    )
    assert d.status == ev.CONFIRMED
    assert d.transition == "DETECTED->CONFIRMED"


def test_poor_data_quality_holds_an_event_at_detected():
    """Acting on a badly observed signal is how false emergencies happen."""
    rules = ev.rules_for(ev.DROUGHT)
    existing = state(status=ev.DETECTED, score=50.0, peak=50.0, periods=5)
    d = ev.evaluate(
        hazard_type=ev.DROUGHT, region_id="R1", score=50.0, severity="MODERATE",
        reference_date=TODAY, existing=existing,
        data_quality=rules.min_quality_to_confirm - 10,
    )
    assert d.status == ev.DETECTED
    assert "data quality" in d.reason


def test_noise_does_not_change_state():
    """A 1-point wobble must not move the lifecycle."""
    d = decide(50.5, existing=state(status=ev.CONFIRMED, score=50.0, peak=60.0))
    assert d.status == ev.CONFIRMED


# --- ids and windows ------------------------------------------------------


def test_event_ids_are_readable_and_sortable():
    """An operator has to say this out loud; a UUID cannot be read aloud."""
    assert ev.build_event_id(ev.FLOOD, TODAY, 7) == "FLOOD-2026-0007"
    assert ev.build_event_id(ev.CROP, TODAY, 12) == "CROPSTRESS-2026-0012"


def test_impact_windows_do_not_overlap():
    """A 'before' window containing the event would understate the impact."""
    windows = ev.impact_window(date(2026, 8, 10), date(2026, 8, 20), window_days=30)
    before_start, before_end = windows["before"]
    during_start, during_end = windows["during"]
    after_start, _ = windows["after"]

    assert before_end < during_start
    assert during_end < after_start
    assert (before_end - before_start).days == 29


def test_impact_window_handles_an_unresolved_event():
    windows = ev.impact_window(date(2026, 8, 10), None, window_days=10)
    assert windows["during"][1] > windows["during"][0]
    assert windows["after"][0] > windows["during"][1]


def test_every_decision_states_a_reason():
    """A lifecycle change nobody can explain is not auditable."""
    for score, existing in [
        (10.0, None), (50.0, None),
        (70.0, state(status=ev.CONFIRMED, score=50.0)),
        (20.0, state(status=ev.DECLINING, below=5)),
    ]:
        assert decide(score, existing=existing).reason
