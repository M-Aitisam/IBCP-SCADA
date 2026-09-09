# packages/backend/app/intelligence/baselines.py
"""Phase 9 — reproducible historical baselines.

Builds a climatology per region, metric and period-of-year from stored
observations, and persists it so downstream anomaly, drought and risk
computations read one indexed row instead of re-deriving a decade of history
on every request.

Why persist rather than compute on demand: a national map needs a baseline for
every region and every metric simultaneously. At 119 districts × 5 metrics
that is 595 climatologies per page load, each scanning years of rows. Computing
them nightly and reading them back is the difference between a dashboard and a
timeout.

**The honesty rule.** A baseline over two years is not a climatology, and this
module refuses to pretend otherwise: rows below `MIN_BASELINE_YEARS` are stored
with `is_sufficient=False` and every consumer must check that flag. The row is
kept rather than skipped so the UI can say *why* a region has no anomaly, which
is far more useful than the region silently vanishing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.databases.timestampdb.intelligence import MetricBaseline
from app.databases.timestampdb.models import SatelliteObservation as Observation

logger = logging.getLogger(__name__)

# Bumped when the statistics or windowing change.
BASELINE_VERSION = "b1"

# Below this the sample is weather, not climate. Three is the minimum at which
# a standard deviation carries any information at all; it is a floor, not a
# recommendation.
MIN_BASELINE_YEARS = 3

PERIOD_MONTH = "month"
PERIOD_DOY_WINDOW = "doy_window"

# Half-width of the day-of-year window, in days. ±15 gives a ~31-day seasonal
# window: wide enough that a 16-day composite contributes at least one
# observation per year, narrow enough not to smear across a season.
DOY_HALF_WINDOW = 15


@dataclass
class BaselineResult:
    computed: int = 0
    sufficient: int = 0
    insufficient: int = 0
    skipped_no_data: int = 0

    def to_json(self) -> dict[str, int]:
        return {
            "computed": self.computed,
            "sufficient": self.sufficient,
            "insufficient": self.insufficient,
            "skipped_no_data": self.skipped_no_data,
        }


def _period_key_for(reference: date, period_type: str) -> int:
    return reference.month if period_type == PERIOD_MONTH else reference.timetuple().tm_yday


async def compute_monthly_baselines(
    db: AsyncSession,
    *,
    metric: str,
    dataset: str,
    aggregation: str = "mean",
    region_ids: Optional[Sequence[str]] = None,
    up_to: Optional[date] = None,
) -> list[dict[str, Any]]:
    """Monthly climatology for one metric, computed entirely in SQL.

    Two-stage, matching the rest of the codebase: collapse over time within
    each (region, month, year) first, then take statistics across years. For
    rainfall the inner stage is a SUM — monthly rainfall totals are what you
    compare across years; averaging daily depths would answer a different
    question. For state variables like NDVI it is a mean.

    `up_to` excludes the period being assessed, so a baseline never contains
    the observation it is about to be compared against.
    """
    inner_agg = (
        func.sum(Observation.value)
        if aggregation == "sum"
        else func.avg(Observation.value)
    )

    month = func.extract("month", Observation.observation_date).label("month")
    year = func.extract("year", Observation.observation_date).label("year")

    conditions = [
        Observation.metric == metric,
        Observation.dataset == dataset,
        Observation.value.isnot(None),
    ]
    if up_to is not None:
        conditions.append(Observation.observation_date < up_to)
    if region_ids:
        conditions.append(Observation.region_id.in_(list(region_ids)))

    per_year = (
        select(
            Observation.region_id.label("region_id"),
            month,
            year,
            inner_agg.label("value"),
        )
        .where(and_(*conditions))
        .group_by(Observation.region_id, "month", "year")
        .subquery()
    )

    stmt = select(
        per_year.c.region_id,
        per_year.c.month,
        func.avg(per_year.c.value).label("mean_value"),
        func.percentile_cont(0.5).within_group(per_year.c.value).label("median_value"),
        # Sample SD (n-1): these yearly figures are a sample of the climate,
        # not the whole population of it. Returns NULL for a single year,
        # which is correct — one year has no spread.
        func.stddev_samp(per_year.c.value).label("stddev_value"),
        func.min(per_year.c.value).label("min_value"),
        func.max(per_year.c.value).label("max_value"),
        func.percentile_cont(0.10).within_group(per_year.c.value).label("p10_value"),
        func.percentile_cont(0.25).within_group(per_year.c.value).label("p25_value"),
        func.percentile_cont(0.75).within_group(per_year.c.value).label("p75_value"),
        func.percentile_cont(0.90).within_group(per_year.c.value).label("p90_value"),
        func.count().label("sample_count"),
        func.count(func.distinct(per_year.c.year)).label("sample_years"),
        func.min(per_year.c.year).label("first_year"),
        func.max(per_year.c.year).label("last_year"),
    ).group_by(per_year.c.region_id, per_year.c.month)

    rows = (await db.execute(stmt)).all()

    return [
        {
            "region_id": row.region_id,
            "metric": metric,
            "dataset": dataset,
            "period_type": PERIOD_MONTH,
            "period_key": int(row.month),
            "mean_value": _f(row.mean_value),
            "median_value": _f(row.median_value),
            "stddev_value": _f(row.stddev_value),
            "min_value": _f(row.min_value),
            "max_value": _f(row.max_value),
            "p10_value": _f(row.p10_value),
            "p25_value": _f(row.p25_value),
            "p75_value": _f(row.p75_value),
            "p90_value": _f(row.p90_value),
            "sample_count": int(row.sample_count or 0),
            "sample_years": int(row.sample_years or 0),
            "first_year": int(row.first_year) if row.first_year is not None else None,
            "last_year": int(row.last_year) if row.last_year is not None else None,
            "is_sufficient": int(row.sample_years or 0) >= MIN_BASELINE_YEARS,
            "calculation_version": BASELINE_VERSION,
        }
        for row in rows
    ]


async def persist_baselines(
    db: AsyncSession, baselines: list[dict[str, Any]]
) -> BaselineResult:
    """Upsert baselines on their natural key.

    Idempotent by construction: recomputing the same period overwrites rather
    than accumulating, so the nightly job can run as often as it likes.
    """
    result = BaselineResult()
    if not baselines:
        return result

    # Chunked to stay well under the Postgres 65535-bind-parameter limit.
    batch = 400
    for start in range(0, len(baselines), batch):
        chunk = baselines[start : start + batch]
        stmt = pg_insert(MetricBaseline).values(chunk)
        update_cols = {
            col: stmt.excluded[col]
            for col in chunk[0]
            if col
            not in (
                "region_id",
                "metric",
                "dataset",
                "period_type",
                "period_key",
                "calculation_version",
            )
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_baseline_identity", set_=update_cols
        )
        await db.execute(stmt)

    await db.commit()

    result.computed = len(baselines)
    result.sufficient = sum(1 for b in baselines if b["is_sufficient"])
    result.insufficient = result.computed - result.sufficient
    logger.info(
        "baselines: %d computed (%d sufficient, %d below %d-year minimum)",
        result.computed,
        result.sufficient,
        result.insufficient,
        MIN_BASELINE_YEARS,
    )
    return result


async def load_baseline(
    db: AsyncSession,
    *,
    region_id: str,
    metric: str,
    reference: date,
    period_type: str = PERIOD_MONTH,
) -> Optional[MetricBaseline]:
    """The stored climatology for one region/metric/period, if any."""
    stmt = select(MetricBaseline).where(
        MetricBaseline.region_id == region_id,
        MetricBaseline.metric == metric,
        MetricBaseline.period_type == period_type,
        MetricBaseline.period_key == _period_key_for(reference, period_type),
        MetricBaseline.calculation_version == BASELINE_VERSION,
    )
    return (await db.execute(stmt)).scalars().first()


async def load_baselines_bulk(
    db: AsyncSession,
    *,
    metric: str,
    period_key: int,
    period_type: str = PERIOD_MONTH,
) -> dict[str, MetricBaseline]:
    """Every region's baseline for one metric and period, keyed by region.

    The bulk form exists so the map can colour 119 districts from one query
    rather than 119 — the N+1 this codebase consistently avoids.
    """
    stmt = select(MetricBaseline).where(
        MetricBaseline.metric == metric,
        MetricBaseline.period_type == period_type,
        MetricBaseline.period_key == period_key,
        MetricBaseline.calculation_version == BASELINE_VERSION,
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {row.region_id: row for row in rows}


def z_score(
    value: Optional[float], baseline: Optional[MetricBaseline]
) -> Optional[float]:
    """Standardised departure, or None when it would not be meaningful.

    Returns None — never 0.0 — when the baseline is missing, too short, or has
    no spread. Zero would say "exactly normal", which is a specific and
    confident claim; the honest answer is that we cannot tell.
    """
    if value is None or baseline is None:
        return None
    if not baseline.is_sufficient:
        return None
    if baseline.mean_value is None or baseline.stddev_value is None:
        return None
    if baseline.stddev_value <= 1e-9:
        # A metric that never varies has no meaningful z-score; a departure
        # divided by ~zero spread would explode into nonsense.
        return None
    return round((value - baseline.mean_value) / baseline.stddev_value, 2)


def percentile_rank(
    value: Optional[float], baseline: Optional[MetricBaseline]
) -> Optional[float]:
    """Approximate percentile from the stored quantiles.

    Linear interpolation between p10/p25/median/p75/p90 and the observed
    min/max. Deliberately coarse: storing five quantiles rather than the full
    distribution keeps the baseline table small, and an operational display
    does not need more resolution than "roughly which decile".
    """
    if value is None or baseline is None or not baseline.is_sufficient:
        return None

    points: list[tuple[float, float]] = []
    for stat, pct in (
        ("min_value", 0.0),
        ("p10_value", 10.0),
        ("p25_value", 25.0),
        ("median_value", 50.0),
        ("p75_value", 75.0),
        ("p90_value", 90.0),
        ("max_value", 100.0),
    ):
        stat_value = getattr(baseline, stat, None)
        if stat_value is not None:
            points.append((float(stat_value), pct))

    if len(points) < 2:
        return None
    points.sort(key=lambda p: p[0])

    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]

    for (low_v, low_p), (high_v, high_p) in zip(points, points[1:]):
        if low_v <= value <= high_v:
            if high_v - low_v <= 1e-12:
                return round(low_p, 1)
            fraction = (value - low_v) / (high_v - low_v)
            return round(low_p + fraction * (high_p - low_p), 1)
    return None


def _f(value: Any) -> Optional[float]:
    return None if value is None else float(value)
