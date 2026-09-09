# packages/backend/app/intelligence/hazards.py
"""Phases 7, 10, 13, 14 — hazard scoring and multi-hazard fusion.

Pure scoring logic. Every function takes already-computed inputs and returns a
score with its contributors; none of them touch the database, so the science
is testable without fixtures and the formulas can be argued with directly.

Three commitments run through the module.

**Anomalies, not absolutes.** Rainfall and temperature are scored by departure
from the region's own seasonal normal, never against a universal threshold.
There is no rainfall figure that means "drought" everywhere: 30 mm in a week is
a failed monsoon in Punjab and unremarkable in Chagai. Vegetation indices are
the exception — NDVI has conventional vigour bands that hold across regions —
and even those are combined with anomalies rather than used alone.

**Confidence travels with the score.** A score built on two components and a
three-year baseline must not look as solid as one built on five components and
a decade. Confidence falls with missing inputs, thin baselines and stale data,
and the UI shows it next to the number.

**Contributors are never hidden.** Each score returns the components that
produced it, their values, weights and signed contributions. An operational
score that cannot be interrogated will not be trusted, and should not be.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Bumped whenever weights or thresholds change, so stored scores stay
# interpretable against the formula that produced them.
HAZARD_VERSION = "h1"

# --- hazard identifiers ---------------------------------------------------
HAZARD_DROUGHT = "drought"
HAZARD_HEAT = "heat_stress"
HAZARD_CROP = "crop_stress"
HAZARD_FLOOD = "flood"
HAZARD_MULTI = "multi_hazard"

# --- severity levels ------------------------------------------------------
LEVEL_NORMAL = "NORMAL"
LEVEL_WATCH = "WATCH"
LEVEL_MODERATE = "MODERATE"
LEVEL_SEVERE = "SEVERE"
LEVEL_EXTREME = "EXTREME"
LEVEL_INSUFFICIENT = "INSUFFICIENT_DATA"

# Multi-hazard uses the coarser vocabulary the brief specifies for risk.
RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
RISK_CRITICAL = "CRITICAL"

DROUGHT_BANDS: tuple[tuple[float, str], ...] = (
    (80.0, LEVEL_EXTREME),
    (60.0, LEVEL_SEVERE),
    (40.0, LEVEL_MODERATE),
    (20.0, LEVEL_WATCH),
    (0.0, LEVEL_NORMAL),
)

RISK_BANDS: tuple[tuple[float, str], ...] = (
    (75.0, RISK_CRITICAL),
    (50.0, RISK_HIGH),
    (25.0, RISK_MEDIUM),
    (0.0, RISK_LOW),
)

# Below this many contributing components a score is not reported at all.
# Averaging one input and calling it a multi-hazard index would be a
# presentation of confidence the evidence does not support.
MIN_COMPONENTS = 2


@dataclass
class Contributor:
    """One input to a score, and what it did to the result."""

    component: str
    label: str
    value: Optional[float]
    anomaly: Optional[float]
    weight: float
    # Signed points this component added to the 0-100 score.
    contribution: float
    direction: str
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "label": self.label,
            "value": self.value,
            "anomaly": self.anomaly,
            "weight": round(self.weight, 3),
            "contribution": round(self.contribution, 1),
            "direction": self.direction,
            "detail": self.detail,
        }


@dataclass
class HazardResult:
    hazard: str
    score: Optional[float]
    level: str
    confidence: Optional[float]
    contributors: list[Contributor] = field(default_factory=list)
    primary_driver: Optional[str] = None
    reason: str = ""
    status: str = "ok"
    version: str = HAZARD_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "hazard": self.hazard,
            "score": None if self.score is None else round(self.score, 1),
            "level": self.level,
            "confidence": None if self.confidence is None else round(self.confidence, 2),
            "contributors": [c.to_json() for c in self.contributors],
            "primary_driver": self.primary_driver,
            "reason": self.reason,
            "status": self.status,
            "calculation_version": self.version,
        }


def classify(score: float, bands: tuple[tuple[float, str], ...]) -> str:
    for threshold, level in bands:
        if score >= threshold:
            return level
    return bands[-1][1]


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _z_to_stress(z: float, *, invert: bool = False) -> float:
    """Map a standardised departure onto 0-100 stress.

    z = 0 -> 0, |z| = 3 -> 100, linear between and clipped beyond. Three
    standard deviations is treated as the practical ceiling because that is
    where the alert engine already calls something critical; keeping the two
    consistent means a district scoring 100 here is a district that alerts.

    `invert` is for metrics where the STRESSFUL direction is negative — low
    NDVI, low rainfall — so that a deficit produces a high stress number.
    """
    signed = -z if invert else z
    if signed <= 0:
        return 0.0
    return _clamp((signed / 3.0) * 100.0)


# ---------------------------------------------------------------------------
# Phase 7 — crop health
# ---------------------------------------------------------------------------

# Weights sum to 1.0 when every component is present; missing components are
# renormalised so an absent input does not silently drag the score toward zero.
CROP_WEIGHTS = {
    "ndvi_level": 0.30,
    "ndvi_anomaly": 0.25,
    "moisture_anomaly": 0.15,
    "rainfall_anomaly": 0.15,
    "lst_anomaly": 0.15,
}


def crop_health(
    *,
    ndvi: Optional[float] = None,
    ndvi_z: Optional[float] = None,
    moisture_z: Optional[float] = None,
    rainfall_z: Optional[float] = None,
    lst_z: Optional[float] = None,
    baseline_years: Optional[int] = None,
    data_quality: Optional[float] = None,
) -> HazardResult:
    """Satellite-derived crop/vegetation condition indicator.

    NOT ground truth and not an agronomic diagnosis — NDVI cannot distinguish a
    fallow field from a failed one, and nothing here observes a crop directly.
    It combines a vegetation level, its seasonal anomaly, and the moisture,
    rainfall and temperature anomalies that plausibly drive it.

    Returns a STRESS score: 0 is healthy, 100 is critical. That direction is
    chosen so it composes with the other hazards, all of which score severity.
    """
    contributors: list[Contributor] = []
    weighted_sum = 0.0
    weight_total = 0.0

    def add(key: str, label: str, stress: float, value, anomaly, detail: str) -> None:
        nonlocal weighted_sum, weight_total
        weight = CROP_WEIGHTS[key]
        weighted_sum += stress * weight
        weight_total += weight
        contributors.append(
            Contributor(
                component=key,
                label=label,
                value=value,
                anomaly=anomaly,
                weight=weight,
                contribution=stress * weight,
                direction="worsening" if stress > 50 else "stable",
                detail=detail,
            )
        )

    # Absolute vigour: conventional NDVI bands, the one place an absolute
    # threshold is defensible across regions.
    if ndvi is not None:
        # 0.6+ healthy -> 0 stress; 0.1 or below -> 100.
        stress = _clamp((0.6 - ndvi) / 0.5 * 100.0)
        add("ndvi_level", "NDVI level", stress, round(ndvi, 3), None,
            f"NDVI {ndvi:.3f} against conventional vigour bands")

    if ndvi_z is not None:
        add("ndvi_anomaly", "NDVI anomaly", _z_to_stress(ndvi_z, invert=True),
            None, ndvi_z, f"{ndvi_z:+.1f} SD vs seasonal normal")

    if moisture_z is not None:
        add("moisture_anomaly", "Vegetation moisture anomaly",
            _z_to_stress(moisture_z, invert=True), None, moisture_z,
            f"{moisture_z:+.1f} SD vs seasonal normal")

    if rainfall_z is not None:
        add("rainfall_anomaly", "Rainfall anomaly",
            _z_to_stress(rainfall_z, invert=True), None, rainfall_z,
            f"{rainfall_z:+.1f} SD vs seasonal normal")

    if lst_z is not None:
        # Heat stresses vegetation, so a POSITIVE departure is the harmful one.
        add("lst_anomaly", "Land surface temperature anomaly",
            _z_to_stress(lst_z), None, lst_z,
            f"{lst_z:+.1f} SD vs seasonal normal")

    if len(contributors) < MIN_COMPONENTS or weight_total <= 0:
        return HazardResult(
            hazard=HAZARD_CROP,
            score=None,
            level=LEVEL_INSUFFICIENT,
            confidence=None,
            contributors=contributors,
            status="insufficient_data",
            reason=(
                f"needs at least {MIN_COMPONENTS} components; "
                f"{len(contributors)} available"
            ),
        )

    # Renormalise over the weights actually present.
    score = _clamp(weighted_sum / weight_total)
    confidence = _confidence(
        component_count=len(contributors),
        max_components=len(CROP_WEIGHTS),
        baseline_years=baseline_years,
        data_quality=data_quality,
    )
    primary = max(contributors, key=lambda c: c.contribution)

    return HazardResult(
        hazard=HAZARD_CROP,
        score=score,
        level=classify(score, DROUGHT_BANDS),
        confidence=confidence,
        contributors=contributors,
        primary_driver=primary.label,
        reason=(
            f"Stress {score:.0f}/100, driven mainly by {primary.label.lower()} "
            f"({primary.detail})."
        ),
    )


# ---------------------------------------------------------------------------
# Phase 10 — drought
# ---------------------------------------------------------------------------

DROUGHT_WEIGHTS = {
    "rainfall_deficit": 0.40,
    "vegetation_stress": 0.35,
    "thermal_stress": 0.25,
}

# Persistence: how many consecutive periods a level must hold before the alert
# engine treats it as established. Prevents a single noisy composite from
# escalating a whole district.
DROUGHT_PERSISTENCE_PERIODS = 2


def drought(
    *,
    rainfall_z: Optional[float] = None,
    ndvi_z: Optional[float] = None,
    lst_z: Optional[float] = None,
    previous_level: Optional[str] = None,
    consecutive_periods: int = 1,
    baseline_years: Optional[int] = None,
    data_quality: Optional[float] = None,
) -> HazardResult:
    """Composite drought score from rainfall, vegetation and thermal anomalies.

    This is a standardised-anomaly composite in the spirit of a combined
    drought index — precipitation deficit is weighted highest because it is the
    driver, with vegetation and thermal response as corroboration. It is NOT
    SPI: SPI requires fitting a gamma distribution to a long precipitation
    record, which needs the full historical backfill and is a fair next step
    once that exists. Calling this SPI would misrepresent the method.

    Hysteresis lives with the caller (the alert engine) via
    `consecutive_periods`; this function reports the instantaneous state and
    how long it has held.
    """
    contributors: list[Contributor] = []
    weighted_sum = 0.0
    weight_total = 0.0

    def add(key: str, label: str, stress: float, z: float, detail: str) -> None:
        nonlocal weighted_sum, weight_total
        weight = DROUGHT_WEIGHTS[key]
        weighted_sum += stress * weight
        weight_total += weight
        contributors.append(
            Contributor(
                component=key,
                label=label,
                value=None,
                anomaly=z,
                weight=weight,
                contribution=stress * weight,
                direction="worsening" if stress > 50 else "stable",
                detail=detail,
            )
        )

    if rainfall_z is not None:
        add("rainfall_deficit", "Rainfall deficit",
            _z_to_stress(rainfall_z, invert=True), rainfall_z,
            f"{rainfall_z:+.1f} SD vs seasonal normal")
    if ndvi_z is not None:
        add("vegetation_stress", "Vegetation stress",
            _z_to_stress(ndvi_z, invert=True), ndvi_z,
            f"{ndvi_z:+.1f} SD vs seasonal normal")
    if lst_z is not None:
        add("thermal_stress", "Thermal stress", _z_to_stress(lst_z), lst_z,
            f"{lst_z:+.1f} SD vs seasonal normal")

    if len(contributors) < MIN_COMPONENTS or weight_total <= 0:
        return HazardResult(
            hazard=HAZARD_DROUGHT,
            score=None,
            level=LEVEL_INSUFFICIENT,
            confidence=None,
            contributors=contributors,
            status="insufficient_data",
            reason=(
                "drought scoring needs rainfall plus at least one response "
                f"indicator; {len(contributors)} component(s) available"
            ),
        )

    score = _clamp(weighted_sum / weight_total)
    level = classify(score, DROUGHT_BANDS)
    confidence = _confidence(
        component_count=len(contributors),
        max_components=len(DROUGHT_WEIGHTS),
        baseline_years=baseline_years,
        data_quality=data_quality,
    )
    primary = max(contributors, key=lambda c: c.contribution)

    persistence = ""
    if previous_level and previous_level == level and consecutive_periods > 1:
        persistence = f" Held at {level} for {consecutive_periods} periods."
    elif previous_level and previous_level != level:
        persistence = f" Changed from {previous_level}."

    return HazardResult(
        hazard=HAZARD_DROUGHT,
        score=score,
        level=level,
        confidence=confidence,
        contributors=contributors,
        primary_driver=primary.label,
        reason=(
            f"Drought score {score:.0f}/100 ({level}); primary driver "
            f"{primary.label.lower()} at {primary.detail}.{persistence}"
        ),
    )


# ---------------------------------------------------------------------------
# Heat stress
# ---------------------------------------------------------------------------


def heat_stress(
    *,
    lst_day_z: Optional[float] = None,
    lst_night_z: Optional[float] = None,
    lst_day_value: Optional[float] = None,
    baseline_years: Optional[int] = None,
    data_quality: Optional[float] = None,
) -> HazardResult:
    """Thermal anomaly severity.

    Night-time departure is weighted alongside daytime deliberately: sustained
    warm nights prevent overnight recovery and are a recognised component of
    heat stress for both people and crops, so a hot day that cools off is not
    the same hazard as a hot day that does not.
    """
    contributors: list[Contributor] = []
    weights = {"lst_day": 0.6, "lst_night": 0.4}
    weighted_sum = 0.0
    weight_total = 0.0

    if lst_day_z is not None:
        stress = _z_to_stress(lst_day_z)
        weighted_sum += stress * weights["lst_day"]
        weight_total += weights["lst_day"]
        contributors.append(
            Contributor("lst_day", "Daytime LST anomaly", lst_day_value, lst_day_z,
                        weights["lst_day"], stress * weights["lst_day"],
                        "worsening" if stress > 50 else "stable",
                        f"{lst_day_z:+.1f} SD vs seasonal normal")
        )
    if lst_night_z is not None:
        stress = _z_to_stress(lst_night_z)
        weighted_sum += stress * weights["lst_night"]
        weight_total += weights["lst_night"]
        contributors.append(
            Contributor("lst_night", "Nighttime LST anomaly", None, lst_night_z,
                        weights["lst_night"], stress * weights["lst_night"],
                        "worsening" if stress > 50 else "stable",
                        f"{lst_night_z:+.1f} SD vs seasonal normal")
        )

    if not contributors or weight_total <= 0:
        return HazardResult(
            hazard=HAZARD_HEAT, score=None, level=LEVEL_INSUFFICIENT,
            confidence=None, contributors=[], status="insufficient_data",
            reason="no land-surface-temperature anomaly available",
        )

    score = _clamp(weighted_sum / weight_total)
    primary = max(contributors, key=lambda c: c.contribution)
    return HazardResult(
        hazard=HAZARD_HEAT,
        score=score,
        level=classify(score, DROUGHT_BANDS),
        confidence=_confidence(
            component_count=len(contributors), max_components=2,
            baseline_years=baseline_years, data_quality=data_quality,
        ),
        contributors=contributors,
        primary_driver=primary.label,
        reason=f"Heat stress {score:.0f}/100; {primary.label.lower()} {primary.detail}.",
    )


# ---------------------------------------------------------------------------
# Phases 13/14 — multi-hazard fusion with explainability
# ---------------------------------------------------------------------------

# Configurable weights for the fused index. Drought carries most weight for a
# Pakistan-focused agricultural context; flood is weighted high because its
# onset is fast and its consequences immediate.
MULTI_HAZARD_WEIGHTS = {
    HAZARD_DROUGHT: 0.35,
    HAZARD_FLOOD: 0.30,
    HAZARD_CROP: 0.20,
    HAZARD_HEAT: 0.15,
}


def multi_hazard(
    components: dict[str, Optional[HazardResult]],
    *,
    exposure_factor: Optional[float] = None,
    weights: Optional[dict[str, float]] = None,
) -> HazardResult:
    """Fuse individual hazard scores into one risk index.

    Missing hazards are excluded and the remaining weights renormalised, so a
    region with no flood assessment is not scored as though its flood risk were
    zero — absent evidence is not evidence of absence.

    `exposure_factor` (0-1) scales the hazard by what is actually at stake. It
    is applied as a modifier rather than a component so that hazard severity
    and exposure stay separately visible: a severe hazard over empty desert and
    a moderate one over a populated district are different situations, and
    collapsing them into one number hides that.
    """
    active_weights = dict(weights or MULTI_HAZARD_WEIGHTS)
    contributors: list[Contributor] = []
    weighted_sum = 0.0
    weight_total = 0.0
    confidences: list[float] = []

    for hazard, result in components.items():
        if result is None or result.score is None:
            continue
        weight = active_weights.get(hazard, 0.0)
        if weight <= 0:
            continue
        weighted_sum += result.score * weight
        weight_total += weight
        if result.confidence is not None:
            confidences.append(result.confidence)
        contributors.append(
            Contributor(
                component=hazard,
                label=hazard.replace("_", " ").title(),
                value=round(result.score, 1),
                anomaly=None,
                weight=weight,
                contribution=result.score * weight,
                direction="worsening" if result.score > 50 else "stable",
                detail=result.primary_driver or result.level,
            )
        )

    if len(contributors) < MIN_COMPONENTS or weight_total <= 0:
        return HazardResult(
            hazard=HAZARD_MULTI,
            score=None,
            level=LEVEL_INSUFFICIENT,
            confidence=None,
            contributors=contributors,
            status="insufficient_data",
            reason=(
                f"multi-hazard fusion needs at least {MIN_COMPONENTS} scored "
                f"hazards; {len(contributors)} available"
            ),
        )

    score = _clamp(weighted_sum / weight_total)

    if exposure_factor is not None:
        # Exposure can amplify by up to 25% but never invents risk where the
        # hazard score is zero.
        modifier = 1.0 + 0.25 * _clamp(exposure_factor, 0.0, 1.0)
        score = _clamp(score * modifier)
        contributors.append(
            Contributor(
                component="exposure",
                label="Exposure modifier",
                value=round(exposure_factor, 3),
                anomaly=None,
                weight=0.0,
                contribution=0.0,
                direction="amplifying",
                detail=f"hazard score scaled by ×{modifier:.2f} for exposure",
            )
        )

    # Fused confidence is the mean of contributing confidences, discounted for
    # every hazard we could not assess: a two-hazard fusion is less informative
    # than a four-hazard one even when both are confident individually.
    base_confidence = sum(confidences) / len(confidences) if confidences else 0.5
    coverage = weight_total / sum(active_weights.values())
    confidence = round(base_confidence * (0.6 + 0.4 * coverage), 2)

    scored = [c for c in contributors if c.weight > 0]
    primary = max(scored, key=lambda c: c.contribution)

    return HazardResult(
        hazard=HAZARD_MULTI,
        score=score,
        level=classify(score, RISK_BANDS),
        confidence=confidence,
        contributors=contributors,
        primary_driver=primary.label,
        reason=(
            f"Risk {score:.0f}/100 ({classify(score, RISK_BANDS)}). "
            f"Largest contribution from {primary.label.lower()} "
            f"({primary.contribution:.0f} of {score:.0f} points). "
            f"Assessed on {len(scored)} of {len(active_weights)} hazards."
        ),
    )


def explain(result: HazardResult) -> dict[str, Any]:
    """Phase 14 — the "why is this region at risk?" payload.

    Contributors sorted by contribution, so the answer leads with what actually
    drove the score rather than with whatever happened to be computed first.
    """
    ranked = sorted(
        (c for c in result.contributors if c.weight > 0),
        key=lambda c: c.contribution,
        reverse=True,
    )
    modifiers = [c.to_json() for c in result.contributors if c.weight <= 0]

    return {
        "hazard": result.hazard,
        "score": None if result.score is None else round(result.score, 1),
        "level": result.level,
        "confidence": result.confidence,
        "status": result.status,
        "primary_driver": result.primary_driver,
        "reason": result.reason,
        "contributors": [c.to_json() for c in ranked],
        "modifiers": modifiers,
        "calculation_version": result.version,
        # Restated on every explanation: this is analysis, not authority.
        "classification": "satellite-derived analytical indicator",
        "is_official_warning": False,
    }


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def _confidence(
    *,
    component_count: int,
    max_components: int,
    baseline_years: Optional[int],
    data_quality: Optional[float],
) -> float:
    """How much weight this score deserves, from 0 to 1.

    Three independent discounts, multiplied:

      completeness — fraction of the intended components actually present
      baseline     — how many years the anomalies rest on, saturating at 10
      quality      — mean data-quality score of the underlying observations

    Multiplicative rather than averaged on purpose: a score with excellent data
    quality but a three-year baseline should not be rescued by the good half.
    Any single weak leg should pull the whole thing down, because it does.
    """
    completeness = component_count / max(1, max_components)

    if baseline_years is None:
        baseline_factor = 0.5
    else:
        baseline_factor = _clamp(baseline_years / 10.0, 0.3, 1.0)

    quality_factor = 1.0 if data_quality is None else _clamp(data_quality / 100.0, 0.3, 1.0)

    return round(_clamp(completeness * baseline_factor * quality_factor, 0.0, 1.0), 2)
