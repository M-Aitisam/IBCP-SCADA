# packages/backend/app/intelligence/flood.py
"""Deterministic Sentinel-1 SAR flood detection.

Sentinel-1 is preferred over optical for flooding because C-band radar sees
through cloud, and floods arrive with weather. An optical flood detector is
blind exactly when it is needed.

**The central design decision: absolute water fraction is not used to detect
flooding — only its departure from the region's own baseline.**

That is not fastidiousness, it is required for correctness. Open water is a
specular reflector and returns very low backscatter, so thresholding VV finds
water. But smooth *dry* surfaces return low backscatter too: sand sheets, dry
lake beds, bare rock, tarmac. Measured on this database, arid Balochistan
districts show a baseline water fraction around 0.2, and one district reads
0.61 in a dry August. Treating 0.61 as "61% flooded" would be nonsense.

Differencing against each region's own seasonal baseline cancels the persistent
part of that error: a sand sheet is dark in the baseline too, so it contributes
~0 to the anomaly. What survives is *newly* dark area — which is what a flood
is.

    new_water_fraction = current_fraction - baseline_fraction
    flooded_area_km2   = new_water_fraction × region_area_km2

Everything here is a documented rule over stored numbers. No model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

FLOOD_VERSION = "fl1"

# --- severity levels ------------------------------------------------------
NONE = "NONE"
MINOR = "MINOR"
MODERATE = "MODERATE"
MAJOR = "MAJOR"
SEVERE = "SEVERE"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FloodParameters:
    """Configurable detection parameters (§9: document, do not hardcode).

    Stated as data so they can be tuned per deployment and asserted in tests,
    rather than being magic numbers inside branches.
    """

    # The VV cut used at ingestion time, carried here for provenance only —
    # changing it requires re-ingesting, so it is not tunable at scoring time.
    water_threshold_db: float = -15.0

    # Minimum increase in water fraction over baseline that counts as flooding
    # at all. Below this the change is within the noise of speckle, incidence
    # angle and soil-moisture variation, none of which is a flood.
    min_fraction_increase: float = 0.05

    # Severity bands on the *new* water fraction (not the absolute fraction).
    minor_fraction: float = 0.05
    moderate_fraction: float = 0.10
    major_fraction: float = 0.20
    severe_fraction: float = 0.35

    # A standardised departure is used when the baseline has enough years to
    # provide a spread; it catches unusual flooding in regions that are always
    # somewhat wet, which a fixed fraction threshold would miss.
    z_score_alert: float = 2.0

    # Baseline years below which detection is reported as UNKNOWN rather than
    # guessed. Two years of August is not a dry-season normal.
    min_baseline_years: int = 3

    # Data quality below which a detection is withheld. SAR needs no cloud
    # screening, but a scene covering 3% of a district is not evidence.
    min_data_quality: float = 35.0


DEFAULT_PARAMETERS = FloodParameters()


@dataclass
class FloodAssessment:
    """One region's flood state for one cycle."""

    region_id: str
    severity: str
    score: Optional[float]
    current_fraction: Optional[float]
    baseline_fraction: Optional[float]
    new_water_fraction: Optional[float]
    z_score: Optional[float]
    pre_event_area_km2: Optional[float]
    current_water_area_km2: Optional[float]
    new_flooded_area_km2: Optional[float]
    fraction_change_pct: Optional[float]
    region_area_km2: Optional[float]
    baseline_years: Optional[int]
    data_quality: Optional[float]
    status: str
    reason: str
    parameters: dict[str, Any] = field(default_factory=dict)
    version: str = FLOOD_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "severity": self.severity,
            "score": self.score,
            "current_water_fraction": self.current_fraction,
            "baseline_water_fraction": self.baseline_fraction,
            "new_water_fraction": self.new_water_fraction,
            "z_score": self.z_score,
            "pre_event_water_area_km2": self.pre_event_area_km2,
            "current_water_area_km2": self.current_water_area_km2,
            "new_flooded_area_km2": self.new_flooded_area_km2,
            "flood_percentage_change": self.fraction_change_pct,
            "region_area_km2": self.region_area_km2,
            "baseline_years": self.baseline_years,
            "data_quality": self.data_quality,
            "status": self.status,
            "reason": self.reason,
            "parameters": self.parameters,
            "calculation_version": self.version,
            "method": (
                "Sentinel-1 C-band VV backscatter thresholded at "
                f"{self.parameters.get('water_threshold_db')} dB, differenced "
                "against the region's own seasonal baseline water fraction"
            ),
        }


def classify_severity(
    new_fraction: float, params: FloodParameters = DEFAULT_PARAMETERS
) -> str:
    if new_fraction >= params.severe_fraction:
        return SEVERE
    if new_fraction >= params.major_fraction:
        return MAJOR
    if new_fraction >= params.moderate_fraction:
        return MODERATE
    if new_fraction >= params.minor_fraction:
        return MINOR
    return NONE


def _score_from(new_fraction: float, params: FloodParameters) -> float:
    """Map new water fraction onto a 0-100 hazard score.

    Linear from the minor threshold to the severe threshold, clipped. Chosen so
    the score reaches the event engine's flood onset threshold (35) at roughly
    the MODERATE band — the two are tuned to agree, so a district the flood
    module calls MODERATE is a district that opens an event.
    """
    if new_fraction <= params.minor_fraction:
        return 0.0
    span = params.severe_fraction - params.minor_fraction
    if span <= 0:
        return 100.0
    ratio = (new_fraction - params.minor_fraction) / span
    return max(0.0, min(100.0, ratio * 100.0))


def assess(
    *,
    region_id: str,
    current_fraction: Optional[float],
    baseline_fraction: Optional[float],
    baseline_stddev: Optional[float] = None,
    baseline_years: Optional[int] = None,
    region_area_km2: Optional[float] = None,
    data_quality: Optional[float] = None,
    params: FloodParameters = DEFAULT_PARAMETERS,
) -> FloodAssessment:
    """Assess one region for flooding this cycle.

    Returns UNKNOWN — never NONE — when the inputs cannot support a judgement.
    The difference matters operationally: NONE says "we looked and there is no
    flood", UNKNOWN says "we cannot tell", and a district that has not been
    assessed must never be presented as safe.
    """
    parameters = {
        "water_threshold_db": params.water_threshold_db,
        "min_fraction_increase": params.min_fraction_increase,
        "severity_bands": {
            "minor": params.minor_fraction,
            "moderate": params.moderate_fraction,
            "major": params.major_fraction,
            "severe": params.severe_fraction,
        },
        "z_score_alert": params.z_score_alert,
        "min_baseline_years": params.min_baseline_years,
    }

    def unknown(reason: str) -> FloodAssessment:
        return FloodAssessment(
            region_id=region_id, severity=UNKNOWN, score=None,
            current_fraction=current_fraction, baseline_fraction=baseline_fraction,
            new_water_fraction=None, z_score=None,
            pre_event_area_km2=None, current_water_area_km2=None,
            new_flooded_area_km2=None, fraction_change_pct=None,
            region_area_km2=region_area_km2, baseline_years=baseline_years,
            data_quality=data_quality, status="insufficient_data",
            reason=reason, parameters=parameters,
        )

    if current_fraction is None:
        return unknown("no Sentinel-1 water observation for this region in the period")
    if baseline_fraction is None:
        return unknown(
            "no seasonal baseline water fraction; absolute SAR water fraction "
            "cannot distinguish flooding from permanently dark dry surfaces"
        )
    if baseline_years is not None and baseline_years < params.min_baseline_years:
        return unknown(
            f"baseline rests on {baseline_years} year(s); "
            f"{params.min_baseline_years} required before a departure is meaningful"
        )
    if data_quality is not None and data_quality < params.min_data_quality:
        return unknown(
            f"data quality {data_quality:.0f} below the "
            f"{params.min_data_quality:.0f} needed to report a detection"
        )

    new_fraction = current_fraction - baseline_fraction
    z = None
    if baseline_stddev and baseline_stddev > 1e-9:
        z = round(new_fraction / baseline_stddev, 2)

    pct_change = (
        round((new_fraction / baseline_fraction) * 100.0, 1)
        if baseline_fraction > 1e-9
        else None
    )

    pre_area = (
        round(baseline_fraction * region_area_km2, 2) if region_area_km2 else None
    )
    current_area = (
        round(current_fraction * region_area_km2, 2) if region_area_km2 else None
    )
    new_area = (
        round(max(0.0, new_fraction) * region_area_km2, 2) if region_area_km2 else None
    )

    # Below the noise floor: report NONE, which is a real finding.
    if new_fraction < params.min_fraction_increase:
        # Unless the standardised departure is large — that catches a region
        # whose baseline is stable enough that a small absolute rise is still
        # highly unusual for it.
        if z is None or z < params.z_score_alert:
            return FloodAssessment(
                region_id=region_id, severity=NONE,
                score=0.0, current_fraction=round(current_fraction, 4),
                baseline_fraction=round(baseline_fraction, 4),
                new_water_fraction=round(new_fraction, 4), z_score=z,
                pre_event_area_km2=pre_area, current_water_area_km2=current_area,
                new_flooded_area_km2=new_area, fraction_change_pct=pct_change,
                region_area_km2=region_area_km2, baseline_years=baseline_years,
                data_quality=data_quality, status="ok",
                reason=(
                    f"water fraction {current_fraction:.3f} is within normal range "
                    f"for this region (baseline {baseline_fraction:.3f})"
                ),
                parameters=parameters,
            )

    severity = classify_severity(new_fraction, params)
    score = _score_from(new_fraction, params)

    # The standardised-departure escape hatch.
    #
    # Reaching here with a sub-threshold fraction means the early return was
    # skipped because |z| cleared the alert level — a region whose baseline is
    # tight enough that a small absolute rise is still highly unusual for it.
    # Without this floor the escape was dead code: classify_severity looks only
    # at the fraction and would hand back NONE, discarding the very finding
    # that got us here.
    if severity == NONE and z is not None and z >= params.z_score_alert:
        severity = MINOR
        score = max(score, _score_from(params.minor_fraction * 1.2, params), 20.0)

    area_text = (
        f" over approximately {new_area:,.1f} km²" if new_area else ""
    )
    z_text = f", {z:+.1f} SD from its seasonal normal" if z is not None else ""

    return FloodAssessment(
        region_id=region_id, severity=severity, score=round(score, 1),
        current_fraction=round(current_fraction, 4),
        baseline_fraction=round(baseline_fraction, 4),
        new_water_fraction=round(new_fraction, 4), z_score=z,
        pre_event_area_km2=pre_area, current_water_area_km2=current_area,
        new_flooded_area_km2=new_area, fraction_change_pct=pct_change,
        region_area_km2=region_area_km2, baseline_years=baseline_years,
        data_quality=data_quality, status="ok",
        reason=(
            f"surface water covers {current_fraction:.1%} of the district against a "
            f"{baseline_fraction:.1%} seasonal baseline — an increase of "
            f"{new_fraction:.1%}{area_text}{z_text}"
        ),
        parameters=parameters,
    )


def progression(
    current: FloodAssessment, previous: Optional[FloodAssessment]
) -> dict[str, Any]:
    """Direction and rate of change between two cycles (§13).

    Reported separately from severity because an expanding MODERATE flood and a
    receding MAJOR one call for opposite responses, and a severity label alone
    cannot express that.
    """
    if previous is None or current.new_flooded_area_km2 is None:
        return {"direction": "UNKNOWN", "reason": "no previous assessment to compare"}

    before = previous.new_flooded_area_km2
    if before is None:
        return {"direction": "UNKNOWN", "reason": "previous cycle had no measured extent"}

    delta = current.new_flooded_area_km2 - before
    pct = (delta / before * 100.0) if before > 1e-6 else None

    if pct is None:
        direction = "EXPANDING" if delta > 0 else "STABLE"
    elif pct >= 15:
        direction = "EXPANDING"
    elif pct <= -15:
        direction = "RECEDING"
    else:
        direction = "STABLE"

    return {
        "direction": direction,
        "previous_area_km2": before,
        "current_area_km2": current.new_flooded_area_km2,
        "change_km2": round(delta, 2),
        "change_pct": round(pct, 1) if pct is not None else None,
    }
