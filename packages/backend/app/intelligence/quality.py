# packages/backend/app/intelligence/quality.py
"""Phase 4 — per-observation data quality scoring.

Produces a 0-100 trust score for a single stored observation, plus the flags
and prose that explain it. Pure functions: no database, no network, so the
scoring rules are testable in isolation and cannot drift with the environment.

Two rules govern the design.

**A missing observation is never imputed.** A null value scores badly and is
flagged; it is never replaced with zero, a baseline, or an interpolation. The
score describes what was measured, and "nothing was measured" is a legitimate
thing to describe.

**Penalties are additive and named.** Every deduction carries a flag, so the
UI can answer "why is this 62?" without re-deriving the arithmetic. A bare
number nobody can interrogate is not a quality score, it is a decoration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

# Bumped whenever the rules below change, so historical scores stay
# interpretable rather than silently meaning something new.
QUALITY_VERSION = "q1"

# Status bands. Deliberately coarse: the difference between 71 and 74 is not
# meaningful, the difference between "usable" and "not" is.
STATUS_GOOD = "GOOD"
STATUS_FAIR = "FAIR"
STATUS_POOR = "POOR"
STATUS_UNUSABLE = "UNUSABLE"

GOOD_THRESHOLD = 80.0
FAIR_THRESHOLD = 60.0
POOR_THRESHOLD = 35.0

# --- flags ---------------------------------------------------------------
FLAG_NO_VALUE = "no_value"
FLAG_NO_PIXELS = "no_valid_pixels"
FLAG_LOW_COVERAGE = "low_spatial_coverage"
FLAG_CLOUD = "cloud_contamination"
FLAG_HIGH_CLOUD = "high_cloud_contamination"
FLAG_STALE = "stale_for_cadence"
FLAG_QUALITY_BAND = "quality_bitfield"
FLAG_REJECTED = "rejected_by_validator"
FLAG_SPARSE_PIXELS = "sparse_pixel_sample"

# --- penalty weights -----------------------------------------------------
#
# A null value dominates everything else: whatever the cloud cover was, we did
# not obtain a measurement.
PENALTY_NO_VALUE = 70.0
PENALTY_NO_PIXELS = 30.0
# Cloud scales linearly with cover. At the pipeline's 20% scene threshold this
# costs 10 points — noticeable but still GOOD, which is the intent: a lightly
# clouded scene is usable, not suspect.
CLOUD_PENALTY_PER_PERCENT = 0.5
# Beyond this, residual cloud is likely to have survived masking.
HIGH_CLOUD_PERCENT = 40.0
PENALTY_HIGH_CLOUD = 10.0
# A regional mean over a handful of pixels is noise, whatever its cloud score.
SPARSE_PIXEL_COUNT = 30
PENALTY_SPARSE_PIXELS = 15.0
# Full penalty when spatial coverage falls to zero, scaled by the shortfall.
MAX_COVERAGE_PENALTY = 25.0
COVERAGE_CONCERN_RATIO = 0.6
# Age is measured in publication cycles, not days, so a 16-day composite is
# not punished for behaving like a 16-day composite.
STALE_CYCLE_MULTIPLE = 3.0
PENALTY_STALE = 10.0


@dataclass
class QualityAssessment:
    """The score, the reasons, and the numbers behind them."""

    score: float
    status: str
    flags: list[str] = field(default_factory=list)
    reason: str = ""
    components: dict[str, Any] = field(default_factory=dict)
    version: str = QUALITY_VERSION

    def to_row(self) -> dict[str, Any]:
        """Shape written onto the observation row."""
        return {
            "quality_score": round(self.score, 1),
            "quality_status": self.status,
            "quality_flags": {
                "flags": self.flags,
                "components": self.components,
                "version": self.version,
            },
            "quality_reason": self.reason,
        }


def status_for(score: float) -> str:
    if score >= GOOD_THRESHOLD:
        return STATUS_GOOD
    if score >= FAIR_THRESHOLD:
        return STATUS_FAIR
    if score >= POOR_THRESHOLD:
        return STATUS_POOR
    return STATUS_UNUSABLE


def score_observation(
    *,
    value: Optional[float],
    cloud_percentage: Optional[float] = None,
    pixel_count: Optional[int] = None,
    reference_pixel_count: Optional[int] = None,
    observation_date: Optional[date] = None,
    as_of: Optional[date] = None,
    cadence_days: Optional[int] = None,
    is_quality_band: bool = False,
    processing_status: Optional[str] = None,
) -> QualityAssessment:
    """Score one observation.

    `reference_pixel_count` is the region's typical pixel count for this
    dataset — normally its median across the record. Spatial coverage is
    judged relative to that rather than against an absolute number, because
    what counts as "enough pixels" depends entirely on the region's area and
    the sensor's resolution. A 250 m MODIS product over a small district
    legitimately yields far fewer pixels than 10 m Sentinel-2 over a large one.
    """
    flags: list[str] = []
    components: dict[str, Any] = {}
    reasons: list[str] = []
    score = 100.0

    # A QA bitfield is provenance, not a measurement: a mean over packed bits
    # has no physical meaning, so scoring it as if it did would be misleading.
    if is_quality_band:
        return QualityAssessment(
            score=100.0,
            status=STATUS_GOOD,
            flags=[FLAG_QUALITY_BAND],
            reason="Quality bitfield stored for provenance; not a measurement.",
            components={"scored": False},
        )

    if processing_status == "rejected":
        return QualityAssessment(
            score=0.0,
            status=STATUS_UNUSABLE,
            flags=[FLAG_REJECTED],
            reason="Rejected by the ingestion validator; not usable.",
            components={"processing_status": processing_status},
        )

    # --- value present? ---
    if value is None:
        score -= PENALTY_NO_VALUE
        flags.append(FLAG_NO_VALUE)
        # This is the normal outcome when every pixel in the region was masked
        # (all cloud, or outside the swath). It is recorded, never filled in.
        reasons.append("no valid value returned for this region")
    components["has_value"] = value is not None

    # --- cloud ---
    if cloud_percentage is not None:
        components["cloud_percentage"] = round(float(cloud_percentage), 1)
        penalty = float(cloud_percentage) * CLOUD_PENALTY_PER_PERCENT
        if penalty > 0:
            score -= penalty
            flags.append(FLAG_CLOUD)
        if cloud_percentage >= HIGH_CLOUD_PERCENT:
            score -= PENALTY_HIGH_CLOUD
            flags.append(FLAG_HIGH_CLOUD)
            reasons.append(f"{cloud_percentage:.0f}% scene cloud cover")

    # --- pixels / spatial coverage ---
    if pixel_count is None or pixel_count <= 0:
        # Only meaningful when a value was expected; a null value already
        # explains itself and should not be charged twice.
        if value is not None:
            score -= PENALTY_NO_PIXELS
            flags.append(FLAG_NO_PIXELS)
            reasons.append("no valid pixels contributed")
    else:
        components["pixel_count"] = int(pixel_count)
        if pixel_count < SPARSE_PIXEL_COUNT:
            score -= PENALTY_SPARSE_PIXELS
            flags.append(FLAG_SPARSE_PIXELS)
            reasons.append(f"only {pixel_count} pixels in the regional mean")

        if reference_pixel_count and reference_pixel_count > 0:
            coverage = min(1.0, pixel_count / reference_pixel_count)
            components["coverage_ratio"] = round(coverage, 3)
            if coverage < COVERAGE_CONCERN_RATIO:
                shortfall = (COVERAGE_CONCERN_RATIO - coverage) / COVERAGE_CONCERN_RATIO
                score -= MAX_COVERAGE_PENALTY * shortfall
                flags.append(FLAG_LOW_COVERAGE)
                reasons.append(
                    f"spatial coverage {coverage:.0%} of this region's typical extent"
                )

    # --- temporal freshness ---
    if observation_date and as_of and cadence_days:
        age = (as_of - observation_date).days
        components["age_days"] = age
        components["cadence_days"] = cadence_days
        if age > cadence_days * STALE_CYCLE_MULTIPLE:
            score -= PENALTY_STALE
            flags.append(FLAG_STALE)
            reasons.append(
                f"{age} days old against a {cadence_days}-day publication cycle"
            )

    score = max(0.0, min(100.0, score))
    status = status_for(score)

    if not reasons:
        reason = "No quality concerns detected."
    else:
        reason = "; ".join(reasons).capitalize() + "."

    return QualityAssessment(
        score=score,
        status=status,
        flags=flags,
        reason=reason,
        components=components,
    )


def aggregate_quality(scores: list[float]) -> Optional[float]:
    """Dataset-level quality: the mean of its observations' scores.

    Returns None for an empty list rather than 0.0 — "no observations to
    assess" and "observations that scored zero" are different states, and
    collapsing them would make an un-ingested dataset look catastrophic
    instead of absent.
    """
    usable = [s for s in scores if s is not None]
    if not usable:
        return None
    return round(sum(usable) / len(usable), 1)
