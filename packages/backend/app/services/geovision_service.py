# packages/backend/app/services/geovision_service.py
"""Aggregation and analytics over stored satellite observations.

Everything the GeoVision dashboard displays is derived here, in SQL, from rows
the ingestion pipeline actually wrote. Nothing in this module invents a value:
when the data cannot support an answer the functions return an explicit
"insufficient data" marker and the caller surfaces it as such.

Three rules shape the whole module:

1. Aggregate in the database, never in the browser. A decade of observations
   for 119 regions is millions of rows; the dashboard needs at most a few
   hundred points. The bucket is chosen from the requested span (see
   `resolve_window`) so the payload stays roughly constant regardless of range.

2. Respect each product's native cadence. CHIRPS is daily, MOD13Q1 is a 16-day
   composite. Bucketing them identically would make MOD13Q1 look like it has
   missing days when it simply does not publish them.

3. Keep observation and derivation distinguishable. A measured NDVI and a
   computed anomaly are different kinds of claim, and the response says which
   is which.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Optional, Sequence

from sqlalchemy import Select, and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.databases.timestampdb.models import (
    IngestionCheckpoint,
    IngestionRun,
    SatelliteObservation,
)
from app.ingestion.registry import DATASETS, Cadence

logger = logging.getLogger(__name__)

Observation = SatelliteObservation


# ---------------------------------------------------------------------------
# Metric catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricSpec:
    """How one metric is aggregated, labelled and classified.

    `temporal_aggregation` is the part that is easy to get wrong. Rainfall is a
    flux: summing daily depths over a week gives weekly rainfall, and averaging
    them would understate it by a factor of seven. NDVI and LST are states:
    averaging is right and summing is meaningless.
    """

    key: str
    label: str
    unit: str
    # Dataset used unless the caller names one. Where two products carry the
    # same metric the default is the one with the longer, more regular record.
    default_dataset: str
    # Every dataset that can supply this metric, best first.
    datasets: tuple[str, ...]
    temporal_aggregation: Literal["mean", "sum"] = "mean"
    decimals: int = 3
    # Higher is better (NDVI) vs. no inherent direction (LST, rainfall).
    higher_is_better: Optional[bool] = None
    description: str = ""


METRICS: dict[str, MetricSpec] = {
    "ndvi": MetricSpec(
        key="ndvi",
        label="NDVI",
        unit="index",
        # MOD13Q1 is the atmospherically-corrected, quality-screened 16-day
        # composite: a far more stable vegetation series than individual
        # Sentinel-2 scenes, which vary with view angle and residual cloud.
        default_dataset="mod13q1",
        datasets=("mod13q1", "sentinel2"),
        higher_is_better=True,
        description="Normalized Difference Vegetation Index",
    ),
    "evi": MetricSpec(
        key="evi",
        label="EVI",
        unit="index",
        default_dataset="mod13q1",
        datasets=("mod13q1", "sentinel2"),
        higher_is_better=True,
        description="Enhanced Vegetation Index (Huete et al. 2002)",
    ),
    "rainfall_mm": MetricSpec(
        key="rainfall_mm",
        label="Rainfall",
        unit="mm",
        default_dataset="chirps",
        datasets=("chirps",),
        temporal_aggregation="sum",
        decimals=1,
        description="CHIRPS areal-average daily rainfall depth",
    ),
    "lst_day_c": MetricSpec(
        key="lst_day_c",
        label="Land surface temperature (day)",
        unit="°C",
        default_dataset="mod11a2",
        datasets=("mod11a2",),
        decimals=1,
        description="MODIS 8-day daytime land surface temperature",
    ),
    "lst_night_c": MetricSpec(
        key="lst_night_c",
        label="Land surface temperature (night)",
        unit="°C",
        default_dataset="mod11a2",
        datasets=("mod11a2",),
        decimals=1,
        description="MODIS 8-day nighttime land surface temperature",
    ),
    "backscatter_vv": MetricSpec(
        key="backscatter_vv",
        label="SAR backscatter VV",
        unit="dB",
        default_dataset="sentinel1",
        datasets=("sentinel1",),
        decimals=2,
        description="Sentinel-1 GRD sigma0, VV polarisation",
    ),
    "backscatter_vh": MetricSpec(
        key="backscatter_vh",
        label="SAR backscatter VH",
        unit="dB",
        default_dataset="sentinel1",
        datasets=("sentinel1",),
        decimals=2,
        description="Sentinel-1 GRD sigma0, VH polarisation",
    ),
}

# Metrics the region/overview summaries carry. Quality bitfields are stored for
# provenance but are not displayable measurements, so they are excluded.
SUMMARY_METRICS = ("ndvi", "evi", "rainfall_mm", "lst_day_c", "lst_night_c")


def get_metric(key: str) -> MetricSpec:
    spec = METRICS.get(key)
    if spec is None:
        raise ValueError(
            f"unknown metric {key!r}; available: {', '.join(sorted(METRICS))}"
        )
    return spec


# ---------------------------------------------------------------------------
# Vegetation / crop condition classification
# ---------------------------------------------------------------------------

# Conventional NDVI vigour bands. These describe the measurement; they are not
# a model output and carry no predictive claim. Documented here (and echoed to
# the client by /datasets) so the dashboard legend cannot drift from the
# thresholds actually applied.
NDVI_CLASSES: tuple[tuple[float, str], ...] = (
    (0.60, "Very healthy"),
    (0.45, "Healthy"),
    (0.30, "Moderate"),
    (0.15, "Low"),
    (-1.01, "Very low / bare"),
)

# Crop-condition bands over the same NDVI scale, phrased as stress. Labelled
# everywhere as a satellite-derived *indicator*, never an agronomic diagnosis:
# NDVI cannot distinguish a fallow field from a failed one.
CROP_CONDITION_CLASSES: tuple[tuple[float, str], ...] = (
    (0.45, "Healthy"),
    (0.30, "Moderate stress"),
    (0.15, "High stress"),
    (-1.01, "Critical"),
)


def classify(value: Optional[float], bands: tuple[tuple[float, str], ...]) -> Optional[str]:
    if value is None:
        return None
    for threshold, label in bands:
        if value >= threshold:
            return label
    return bands[-1][1]


def classify_ndvi(value: Optional[float]) -> Optional[str]:
    return classify(value, NDVI_CLASSES)


def classify_crop_condition(value: Optional[float]) -> Optional[str]:
    return classify(value, CROP_CONDITION_CLASSES)


# ---------------------------------------------------------------------------
# Time windows and bucketing
# ---------------------------------------------------------------------------

Bucket = Literal["day", "week", "month"]

# Named ranges the dashboard's time filter offers.
RANGE_DAYS: dict[str, int] = {
    "7d": 7,
    "30d": 30,
    "3m": 90,
    "6m": 183,
    "1y": 365,
    "5y": 1826,
    "10y": 3652,
}


@dataclass(frozen=True)
class Window:
    start: date
    end: date  # inclusive
    bucket: Bucket
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def previous(self) -> "Window":
        """The immediately preceding window of equal length, for trend deltas."""
        span = timedelta(days=self.days)
        return Window(
            start=self.start - span,
            end=self.start - timedelta(days=1),
            bucket=self.bucket,
            label=f"previous {self.label}",
        )


def choose_bucket(days: int) -> Bucket:
    """Pick a bucket that keeps the response small without hiding detail.

    The aim is a few hundred points at most, whatever the span:
      <= 92 days   -> daily        (~92 points, observation-level for CHIRPS)
      <= 400 days  -> weekly       (~57 points)
      longer       -> monthly      (10 years -> 120 points)

    Products coarser than the bucket are unaffected: a 16-day composite simply
    lands one observation in some daily buckets and none in others, which is
    the truth about that product rather than a gap to be filled.
    """
    if days <= 92:
        return "day"
    if days <= 400:
        return "week"
    return "month"


def resolve_window(
    range_key: Optional[str] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
    today: Optional[date] = None,
) -> Window:
    """Turn a named range or an explicit start/end into a concrete window."""
    today = today or datetime.now(timezone.utc).date()

    if start is not None or end is not None:
        resolved_end = end or today
        resolved_start = start or (resolved_end - timedelta(days=89))
        if resolved_start > resolved_end:
            raise ValueError("start date must not be after end date")
        span = (resolved_end - resolved_start).days + 1
        return Window(
            start=resolved_start,
            end=resolved_end,
            bucket=choose_bucket(span),
            label=f"{resolved_start.isoformat()}..{resolved_end.isoformat()}",
        )

    key = (range_key or "3m").lower()
    if key not in RANGE_DAYS:
        raise ValueError(
            f"unknown range {key!r}; available: {', '.join(RANGE_DAYS)} or start/end"
        )
    days = RANGE_DAYS[key]
    resolved_start = today - timedelta(days=days - 1)
    return Window(
        start=resolved_start,
        end=today,
        bucket=choose_bucket(days),
        label=key,
    )


# ---------------------------------------------------------------------------
# Geographic filtering
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegionFilter:
    """Cascading Pakistan -> province -> district -> tehsil selection.

    Every endpoint takes the same filter so the map, KPIs, charts and tables
    cannot disagree about what is selected.
    """

    province: Optional[str] = None
    district: Optional[str] = None
    tehsil: Optional[str] = None
    region_id: Optional[str] = None

    def apply(self, stmt: Select) -> Select:
        if self.region_id:
            # Most specific wins; the rest would be redundant.
            return stmt.where(Observation.region_id == self.region_id)
        if self.province:
            stmt = stmt.where(Observation.province == self.province)
        if self.district:
            stmt = stmt.where(Observation.district == self.district)
        if self.tehsil:
            stmt = stmt.where(Observation.tehsil == self.tehsil)
        return stmt

    @property
    def is_empty(self) -> bool:
        return not any((self.province, self.district, self.tehsil, self.region_id))

    def cache_key(self) -> str:
        return f"{self.province or ''}|{self.district or ''}|{self.tehsil or ''}|{self.region_id or ''}"


def _dataset_for(spec: MetricSpec, dataset: Optional[str]) -> str:
    if dataset is None:
        return spec.default_dataset
    if dataset not in spec.datasets:
        raise ValueError(
            f"metric {spec.key!r} is not available from dataset {dataset!r}; "
            f"available: {', '.join(spec.datasets)}"
        )
    return dataset


def _round(value: Optional[float], decimals: int) -> Optional[float]:
    return None if value is None else round(float(value), decimals)


# ---------------------------------------------------------------------------
# Latest values
# ---------------------------------------------------------------------------


async def latest_per_region(
    db: AsyncSession,
    metric: str,
    *,
    dataset: Optional[str] = None,
    filters: Optional[RegionFilter] = None,
    since: Optional[date] = None,
) -> list[dict[str, Any]]:
    """Each region's most recent non-null observation of one metric.

    DISTINCT ON returns the first row of each region partition, which with the
    matching ORDER BY is the newest — one index scan, no window function and no
    self-join.
    """
    spec = get_metric(metric)
    resolved_dataset = _dataset_for(spec, dataset)
    filters = filters or RegionFilter()

    stmt = (
        select(Observation)
        .where(
            Observation.metric == metric,
            Observation.dataset == resolved_dataset,
            Observation.value.isnot(None),
        )
        .order_by(Observation.region_id, desc(Observation.observation_date))
        .distinct(Observation.region_id)
    )
    if since is not None:
        stmt = stmt.where(Observation.observation_date >= since)
    stmt = filters.apply(stmt)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "region_id": row.region_id,
            "region_type": row.region_type,
            "province": row.province,
            "district": row.district,
            "tehsil": row.tehsil,
            "value": _round(row.value, spec.decimals),
            "unit": row.unit,
            "metric": row.metric,
            "dataset": row.dataset,
            "observation_date": row.observation_date.isoformat(),
            "observation_timestamp": row.observation_timestamp.isoformat(),
            "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
            "source_image_id": row.source_image_id,
            "cloud_percentage": _round(row.cloud_percentage, 1),
            "pixel_count": row.pixel_count,
            "processing_status": row.processing_status,
        }
        for row in rows
    ]


async def window_aggregate_per_region(
    db: AsyncSession,
    metric: str,
    window: Window,
    *,
    dataset: Optional[str] = None,
    filters: Optional[RegionFilter] = None,
) -> dict[str, dict[str, Any]]:
    """Aggregate one metric per region over a window, keyed by region_id.

    Used for rainfall totals and for the previous-period half of a trend, where
    the question is "how much / how high across this period", not "what was the
    last reading".
    """
    spec = get_metric(metric)
    resolved_dataset = _dataset_for(spec, dataset)
    filters = filters or RegionFilter()

    aggregate = (
        func.sum(Observation.value)
        if spec.temporal_aggregation == "sum"
        else func.avg(Observation.value)
    )

    stmt = (
        select(
            Observation.region_id,
            Observation.province,
            Observation.district,
            Observation.tehsil,
            aggregate.label("value"),
            func.count().label("observations"),
            func.min(Observation.observation_date).label("first_observation"),
            func.max(Observation.observation_date).label("last_observation"),
        )
        .where(
            Observation.metric == metric,
            Observation.dataset == resolved_dataset,
            Observation.value.isnot(None),
            Observation.observation_date >= window.start,
            Observation.observation_date <= window.end,
        )
        .group_by(
            Observation.region_id,
            Observation.province,
            Observation.district,
            Observation.tehsil,
        )
    )
    stmt = filters.apply(stmt)

    rows = (await db.execute(stmt)).all()
    return {
        row.region_id: {
            "region_id": row.region_id,
            "province": row.province,
            "district": row.district,
            "tehsil": row.tehsil,
            "value": _round(row.value, spec.decimals),
            "observations": row.observations,
            "first_observation": row.first_observation.isoformat(),
            "last_observation": row.last_observation.isoformat(),
            "unit": spec.unit,
            "aggregation": spec.temporal_aggregation,
        }
        for row in rows
    }


async def window_aggregate(
    db: AsyncSession,
    metric: str,
    window: Window,
    *,
    dataset: Optional[str] = None,
    filters: Optional[RegionFilter] = None,
) -> Optional[dict[str, Any]]:
    """One number for the whole selection over a window, or None if no data.

    Note the two-stage aggregation for rainfall: total over time per region,
    then averaged across regions. Summing every region's rainfall together
    would produce a meaningless figure that grows with how many districts
    happen to be selected.
    """
    spec = get_metric(metric)
    resolved_dataset = _dataset_for(spec, dataset)
    filters = filters or RegionFilter()

    # Two-stage for BOTH aggregations: collapse over time within each region,
    # then across regions.
    #
    # For rainfall the reason is dimensional — sum over time, average over
    # space. For state variables like NDVI and LST the reason is that a flat
    # average over every row weights each district by how many observations it
    # happens to have, and that count is an artifact of orbit geometry and
    # cloud cover, not of geography. A district with 40 clear scenes would
    # quietly outvote one with 4. Averaging the per-region means gives each
    # region one vote, which is what "regional average" should mean.
    #
    # This also keeps the KPI consistent with `series()`, which has always
    # aggregated in two stages — before this they could disagree.
    time_aggregate = (
        func.sum(Observation.value)
        if spec.temporal_aggregation == "sum"
        else func.avg(Observation.value)
    )

    inner = (
        select(
            Observation.region_id.label("region_id"),
            time_aggregate.label("region_value"),
            func.count().label("observations"),
            func.max(Observation.observation_date).label("last_observation"),
        )
        .where(
            Observation.metric == metric,
            Observation.dataset == resolved_dataset,
            Observation.value.isnot(None),
            Observation.observation_date >= window.start,
            Observation.observation_date <= window.end,
        )
        .group_by(Observation.region_id)
    )
    inner = filters.apply(inner).subquery()

    stmt = select(
        func.avg(inner.c.region_value).label("value"),
        func.sum(inner.c.observations).label("observations"),
        func.count().label("regions"),
        func.max(inner.c.last_observation).label("last_observation"),
    )
    row = (await db.execute(stmt)).one_or_none()

    if row is None or row.value is None:
        return None
    return {
        "value": _round(row.value, spec.decimals),
        "unit": spec.unit,
        "observations": int(row.observations or 0),
        "regions": int(row.regions or 0),
        "last_observation": row.last_observation.isoformat()
        if row.last_observation
        else None,
        "aggregation": spec.temporal_aggregation,
        "dataset": resolved_dataset,
    }


# ---------------------------------------------------------------------------
# Batched multi-metric statistics
# ---------------------------------------------------------------------------


async def batch_window_stats(
    db: AsyncSession,
    metrics: Sequence[str],
    window: Window,
    *,
    filters: Optional[RegionFilter] = None,
) -> dict[str, dict[str, Optional[dict[str, Any]]]]:
    """Current- and previous-period aggregates for several metrics in ONE query.

    Why this exists: the overview needs five metrics, each compared against its
    preceding period. Done the obvious way that is ten round trips, and against
    a managed database in another region each one costs ~200ms of pure latency —
    the endpoint spent most of its time waiting, not computing.

    Conditional aggregation collapses all of it into a single statement:
    FILTER (WHERE ...) computes the current and previous window side by side in
    one pass, and GROUP BY covers every metric at once.

    The two-stage shape (per-region CTE, then across regions) is preserved
    because it is load-bearing for rainfall: total over TIME per region, then
    averaged across regions. Summing every region's rainfall into one number
    would produce a figure that grows with how many districts are selected.

    Returns {metric: {"current": {...} | None, "previous": {...} | None}}.
    """
    filters = filters or RegionFilter()
    wanted = [m for m in metrics if m in METRICS]
    if not wanted:
        return {}

    previous = window.previous

    pair_filter = None
    for metric in wanted:
        spec = METRICS[metric]
        clause = and_(
            Observation.metric == metric,
            Observation.dataset == spec.default_dataset,
        )
        pair_filter = clause if pair_filter is None else (pair_filter | clause)

    in_current = and_(
        Observation.observation_date >= window.start,
        Observation.observation_date <= window.end,
    )
    in_previous = and_(
        Observation.observation_date >= previous.start,
        Observation.observation_date <= previous.end,
    )

    per_region = (
        select(
            Observation.metric.label("metric"),
            Observation.dataset.label("dataset"),
            Observation.region_id.label("region_id"),
            func.avg(Observation.value).filter(in_current).label("cur_mean"),
            func.sum(Observation.value).filter(in_current).label("cur_sum"),
            func.count().filter(in_current).label("cur_n"),
            func.max(Observation.observation_date).filter(in_current).label("cur_latest"),
            func.avg(Observation.value).filter(in_previous).label("prev_mean"),
            func.sum(Observation.value).filter(in_previous).label("prev_sum"),
            func.count().filter(in_previous).label("prev_n"),
        )
        .where(
            pair_filter,
            Observation.value.isnot(None),
            # One bounded scan spanning both windows.
            Observation.observation_date >= previous.start,
            Observation.observation_date <= window.end,
        )
        .group_by(Observation.metric, Observation.dataset, Observation.region_id)
    )
    per_region = filters.apply(per_region).subquery()

    stmt = select(
        per_region.c.metric,
        per_region.c.dataset,
        func.avg(per_region.c.cur_mean).label("cur_mean"),
        func.avg(per_region.c.cur_sum).label("cur_sum"),
        func.sum(per_region.c.cur_n).label("cur_n"),
        func.max(per_region.c.cur_latest).label("cur_latest"),
        func.count().filter(per_region.c.cur_n > 0).label("cur_regions"),
        func.avg(per_region.c.prev_mean).label("prev_mean"),
        func.avg(per_region.c.prev_sum).label("prev_sum"),
        func.sum(per_region.c.prev_n).label("prev_n"),
        func.count().filter(per_region.c.prev_n > 0).label("prev_regions"),
    ).group_by(per_region.c.metric, per_region.c.dataset)

    rows = (await db.execute(stmt)).all()

    out: dict[str, dict[str, Optional[dict[str, Any]]]] = {
        m: {"current": None, "previous": None} for m in wanted
    }

    for row in rows:
        spec = METRICS.get(row.metric)
        if spec is None:
            continue
        use_sum = spec.temporal_aggregation == "sum"

        current_value = row.cur_sum if use_sum else row.cur_mean
        if current_value is not None and (row.cur_n or 0) > 0:
            out[row.metric]["current"] = {
                "value": _round(current_value, spec.decimals),
                "unit": spec.unit,
                "observations": int(row.cur_n or 0),
                "regions": int(row.cur_regions or 0),
                "last_observation": row.cur_latest.isoformat() if row.cur_latest else None,
                "aggregation": spec.temporal_aggregation,
                "dataset": row.dataset,
            }

        previous_value = row.prev_sum if use_sum else row.prev_mean
        if previous_value is not None and (row.prev_n or 0) > 0:
            out[row.metric]["previous"] = {
                "value": _round(previous_value, spec.decimals),
                "unit": spec.unit,
                "observations": int(row.prev_n or 0),
                "regions": int(row.prev_regions or 0),
                "last_observation": None,
                "aggregation": spec.temporal_aggregation,
                "dataset": row.dataset,
            }

    return out


def build_trend(
    metric: str,
    window: Window,
    current: Optional[dict[str, Any]],
    previous: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble a trend result from already-fetched aggregates.

    Split out from `trend()` so the batched path and the single-metric path
    apply exactly the same comparison rules — including the stable band and the
    zero-baseline case — instead of two copies that can drift.
    """
    spec = get_metric(metric)
    previous_window = window.previous

    base: dict[str, Any] = {
        "metric": metric,
        "label": spec.label,
        "unit": spec.unit,
        "aggregation": spec.temporal_aggregation,
        "current": current,
        "previous": previous,
        "period": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "previous_period": {
            "start": previous_window.start.isoformat(),
            "end": previous_window.end.isoformat(),
        },
    }

    if current is None or previous is None:
        return {
            **base,
            "status": "insufficient_data",
            "change_pct": None,
            "change_absolute": None,
            "direction": None,
            "reason": (
                "no observations in the current period"
                if current is None
                else "no observations in the comparison period"
            ),
        }

    current_value = current["value"]
    previous_value = previous["value"]
    change_absolute = current_value - previous_value

    if previous_value == 0:
        return {
            **base,
            "status": "ok",
            "change_pct": None,
            "change_absolute": _round(change_absolute, spec.decimals),
            "direction": "up" if change_absolute > 0 else "stable",
            "reason": "previous period was zero; percentage change is undefined",
        }

    change_pct = (change_absolute / abs(previous_value)) * 100.0

    if abs(change_pct) < TREND_STABLE_BAND_PCT:
        direction = "stable"
    elif change_pct > 0:
        direction = "up"
    else:
        direction = "down"

    if spec.higher_is_better is None or direction == "stable":
        interpretation = None
    elif (direction == "up") == spec.higher_is_better:
        interpretation = "improving"
    else:
        interpretation = "declining"

    return {
        **base,
        "status": "ok",
        "change_pct": round(change_pct, 1),
        "change_absolute": _round(change_absolute, spec.decimals),
        "direction": direction,
        "interpretation": interpretation,
    }


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------

# A change smaller than this is reported as "stable". Satellite retrievals carry
# real retrieval noise, and calling a 0.3% wobble an improvement would be
# reading signal into it.
TREND_STABLE_BAND_PCT = 2.0


async def trend(
    db: AsyncSession,
    metric: str,
    window: Window,
    *,
    dataset: Optional[str] = None,
    filters: Optional[RegionFilter] = None,
) -> dict[str, Any]:
    """Current window versus the preceding window of equal length.

    Returns `status: "insufficient_data"` rather than a misleading comparison
    when either side is missing — which is the normal case for a product that
    only started publishing recently, or a 7-day window over a 16-day composite.
    """
    current = await window_aggregate(
        db, metric, window, dataset=dataset, filters=filters
    )
    previous = await window_aggregate(
        db, metric, window.previous, dataset=dataset, filters=filters
    )
    return build_trend(metric, window, current, previous)


# ---------------------------------------------------------------------------
# Bucketed series
# ---------------------------------------------------------------------------


async def series(
    db: AsyncSession,
    metric: str,
    window: Window,
    *,
    dataset: Optional[str] = None,
    filters: Optional[RegionFilter] = None,
) -> dict[str, Any]:
    """Bucketed time series for the current selection.

    Buckets with no source observation are simply absent from the result. They
    are not zero-filled and not interpolated: a gap in a satellite record is
    information, and inventing points across it would be fabricating data.
    """
    spec = get_metric(metric)
    resolved_dataset = _dataset_for(spec, dataset)
    filters = filters or RegionFilter()
    config = DATASETS.get(resolved_dataset)

    # Postgres casts date -> timestamp for date_trunc, so the column goes in
    # directly; the result comes back as a timestamp at the bucket boundary.
    bucket_expr = func.date_trunc(window.bucket, Observation.observation_date).label(
        "bucket"
    )

    # Two-stage again: collapse over time within each region first, then across
    # regions. For rainfall this is sum-then-average; for state variables both
    # stages are means, and doing it in two stages keeps a region with many
    # observations from dominating the regional average.
    time_aggregate = (
        func.sum(Observation.value)
        if spec.temporal_aggregation == "sum"
        else func.avg(Observation.value)
    )

    inner = (
        select(
            bucket_expr,
            Observation.region_id.label("region_id"),
            time_aggregate.label("region_value"),
            func.count().label("observations"),
            func.min(Observation.value).label("min_value"),
            func.max(Observation.value).label("max_value"),
        )
        .where(
            Observation.metric == metric,
            Observation.dataset == resolved_dataset,
            Observation.value.isnot(None),
            Observation.observation_date >= window.start,
            Observation.observation_date <= window.end,
        )
        .group_by("bucket", Observation.region_id)
    )
    inner = filters.apply(inner).subquery()

    stmt = (
        select(
            inner.c.bucket,
            func.avg(inner.c.region_value).label("value"),
            func.min(inner.c.min_value).label("min_value"),
            func.max(inner.c.max_value).label("max_value"),
            func.sum(inner.c.observations).label("observations"),
            func.count().label("regions"),
        )
        .group_by(inner.c.bucket)
        .order_by(inner.c.bucket)
    )

    rows = (await db.execute(stmt)).all()
    points = [
        {
            "bucket_start": row.bucket.date().isoformat()
            if hasattr(row.bucket, "date")
            else str(row.bucket),
            "value": _round(row.value, spec.decimals),
            "min": _round(row.min_value, spec.decimals),
            "max": _round(row.max_value, spec.decimals),
            "observations": int(row.observations or 0),
            "regions": int(row.regions or 0),
        }
        for row in rows
    ]

    return {
        "metric": metric,
        "label": spec.label,
        "unit": spec.unit,
        "dataset": resolved_dataset,
        "dataset_asset_id": config.asset_id if config else None,
        "native_cadence": config.cadence.value if config else None,
        "bucket": window.bucket,
        "aggregation": spec.temporal_aggregation,
        "period": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "data_source": "timestampdb" if points else "no_data",
        "count": len(points),
        "series": points,
    }


# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------

# Minimum distinct years of history before a climatological baseline is
# reported. Below this the "normal" is one or two years of weather, not a
# climatology, and an anomaly against it would be noise dressed up as signal.
MIN_BASELINE_YEARS = 3


async def anomaly(
    db: AsyncSession,
    metric: str,
    window: Window,
    *,
    dataset: Optional[str] = None,
    filters: Optional[RegionFilter] = None,
) -> dict[str, Any]:
    """Departure of the current window from the same calendar period in prior years.

    The baseline is built from the same day-of-year span in every earlier year
    on record, so a July NDVI is compared against other Julys rather than
    against the annual mean — which would just re-measure the seasonal cycle.

    This is a DERIVED quantity. The response labels it as such and reports how
    many years the baseline rests on, so a thin baseline is visible rather than
    implied to be a climate normal.
    """
    spec = get_metric(metric)
    resolved_dataset = _dataset_for(spec, dataset)
    filters = filters or RegionFilter()

    observed = await window_aggregate(
        db, metric, window, dataset=dataset, filters=filters
    )

    start_doy = window.start.timetuple().tm_yday
    end_doy = window.end.timetuple().tm_yday
    doy = func.extract("doy", Observation.observation_date)
    year = func.extract("year", Observation.observation_date)

    # A window spanning the new year wraps, so the day-of-year test flips from
    # "between" to "outside" — without this, a December-January window would
    # silently match nothing.
    if start_doy <= end_doy:
        season = and_(doy >= start_doy, doy <= end_doy)
    else:
        season = (doy >= start_doy) | (doy <= end_doy)

    baseline_stmt = (
        select(
            year.label("year"),
            func.avg(Observation.value).label("value"),
        )
        .where(
            Observation.metric == metric,
            Observation.dataset == resolved_dataset,
            Observation.value.isnot(None),
            Observation.observation_date < window.start,
            season,
        )
        .group_by("year")
    )
    baseline_stmt = filters.apply(baseline_stmt)
    baseline_rows = (await db.execute(baseline_stmt)).all()

    base: dict[str, Any] = {
        "metric": metric,
        "label": spec.label,
        "unit": spec.unit,
        # The distinction the brief insists on: what was measured, versus what
        # was computed from it.
        "kind": "derived_anomaly",
        "observed": observed,
        "baseline_years": len(baseline_rows),
        "baseline_period": f"day-of-year {start_doy}..{end_doy}, all years before {window.start.year}",
        "min_baseline_years": MIN_BASELINE_YEARS,
    }

    if observed is None:
        return {
            **base,
            "status": "insufficient_data",
            "reason": "no observations in the requested period",
            "baseline": None,
            "anomaly": None,
        }
    if len(baseline_rows) < MIN_BASELINE_YEARS:
        return {
            **base,
            "status": "insufficient_data",
            "reason": (
                f"baseline needs {MIN_BASELINE_YEARS} prior years for this season; "
                f"{len(baseline_rows)} available"
            ),
            "baseline": None,
            "anomaly": None,
        }

    values = [float(r.value) for r in baseline_rows if r.value is not None]
    baseline_mean = sum(values) / len(values)
    # Population SD over the yearly means: with a handful of years this is a
    # rough spread indicator, and it is reported alongside the year count so it
    # is never mistaken for a rigorous climate normal.
    variance = sum((v - baseline_mean) ** 2 for v in values) / len(values)
    baseline_sd = variance ** 0.5

    departure = observed["value"] - baseline_mean
    # Standardised departure, but only when the baseline actually varies.
    z_score = round(departure / baseline_sd, 2) if baseline_sd > 1e-9 else None
    pct = (
        round((departure / abs(baseline_mean)) * 100.0, 1)
        if abs(baseline_mean) > 1e-9
        else None
    )

    return {
        **base,
        "status": "ok",
        "baseline": {
            "value": _round(baseline_mean, spec.decimals),
            "standard_deviation": _round(baseline_sd, spec.decimals),
            "years": sorted(int(r.year) for r in baseline_rows),
        },
        "anomaly": {
            "absolute": _round(departure, spec.decimals),
            "percent": pct,
            "z_score": z_score,
        },
    }


# ---------------------------------------------------------------------------
# Per-region condition (the map's traffic-light colouring)
# ---------------------------------------------------------------------------

# Condition levels, worst-first ordering handled by CONDITION_RANK below.
CONDITION_HEALTHY = "healthy"
CONDITION_WATCH = "watch"
CONDITION_STRESSED = "stressed"
CONDITION_CRITICAL = "critical"
CONDITION_UNKNOWN = "unknown"

CONDITION_RANK = {
    CONDITION_HEALTHY: 0,
    CONDITION_WATCH: 1,
    CONDITION_STRESSED: 2,
    CONDITION_CRITICAL: 3,
    CONDITION_UNKNOWN: -1,
}

# Human labels, so the legend and the tooltip read from one place.
CONDITION_LABELS = {
    CONDITION_HEALTHY: "Healthy / normal",
    CONDITION_WATCH: "Watch",
    CONDITION_STRESSED: "Stressed / high",
    CONDITION_CRITICAL: "Critical",
    CONDITION_UNKNOWN: "No basis",
}

# Vegetation indices carry their own meaning, so they are classified directly
# from the value using the same conventional bands as crop condition.
VEGETATION_CONDITION_BANDS: tuple[tuple[float, str], ...] = (
    (0.45, CONDITION_HEALTHY),
    (0.30, CONDITION_WATCH),
    (0.15, CONDITION_STRESSED),
    (-1.01, CONDITION_CRITICAL),
)


def condition_from_value(value: Optional[float]) -> str:
    """Condition for a vegetation index, straight from its value."""
    if value is None:
        return CONDITION_UNKNOWN
    return classify(value, VEGETATION_CONDITION_BANDS) or CONDITION_UNKNOWN


def condition_from_anomaly(z_score: Optional[float]) -> str:
    """Condition for a metric with no context-free 'bad' value.

    Temperature and rainfall cannot be classified from the reading alone: 40 °C
    is an ordinary June in Sibi and alarming in Skardu, and 30 mm/week is a
    drought in monsoon Punjab and a deluge in Chagai. Colouring those against a
    fixed threshold would assert something the data does not support.

    The departure from the region's OWN seasonal normal does carry meaning
    anywhere, so that is what drives the colour. Thresholds are the same ones
    Satellite Watch uses, so a district painted red on the map is a district
    that would raise an indicator.

    Returns `unknown` — not `healthy` — when there is no baseline. Painting an
    unknown region green would assert normality we have not established.
    """
    if z_score is None:
        return CONDITION_UNKNOWN
    magnitude = abs(z_score)
    if magnitude >= WATCH_Z_CRITICAL:
        return CONDITION_CRITICAL
    if magnitude >= WATCH_Z_HIGH:
        return CONDITION_STRESSED
    if magnitude >= WATCH_Z_ADVISORY:
        return CONDITION_WATCH
    return CONDITION_HEALTHY


# Which rule applies to which metric.
VALUE_CLASSIFIED_METRICS = ("ndvi", "evi")
ANOMALY_CLASSIFIED_METRICS = ("rainfall_mm", "lst_day_c", "lst_night_c")


async def region_anomalies(
    db: AsyncSession,
    metrics: Sequence[str],
    window: Window,
    *,
    filters: Optional[RegionFilter] = None,
) -> dict[str, dict[str, Any]]:
    """Per-region standardised departure from the same season in prior years.

    Structurally the same computation as `anomaly()`, but grouped by region so
    the map can colour each district on its own history rather than on the
    aggregate for the whole selection.

    Runs one query per aggregation kind (mean vs sum), not one per region:
    with 119 districts the per-region loop would be 119 round trips.

    Returns {region_id: {metric: {value, baseline, sd, z_score, years}}}.
    """
    filters = filters or RegionFilter()
    wanted = [m for m in metrics if m in METRICS]
    if not wanted:
        return {}

    start_doy = window.start.timetuple().tm_yday
    end_doy = window.end.timetuple().tm_yday
    doy = func.extract("doy", Observation.observation_date)
    year = func.extract("year", Observation.observation_date)

    # A window spanning the new year wraps, so the day-of-year test flips.
    if start_doy <= end_doy:
        season = and_(doy >= start_doy, doy <= end_doy)
    else:
        season = (doy >= start_doy) | (doy <= end_doy)

    out: dict[str, dict[str, Any]] = {}

    # Group metrics by how they collapse over time; one query per group.
    by_aggregation: dict[str, list[str]] = {}
    for metric in wanted:
        by_aggregation.setdefault(METRICS[metric].temporal_aggregation, []).append(metric)

    for aggregation, group in by_aggregation.items():
        pair_filter = None
        for metric in group:
            clause = and_(
                Observation.metric == metric,
                Observation.dataset == METRICS[metric].default_dataset,
            )
            pair_filter = clause if pair_filter is None else (pair_filter | clause)

        collapse = (
            func.sum(Observation.value)
            if aggregation == "sum"
            else func.avg(Observation.value)
        )

        # --- current window, per region ---
        current_stmt = (
            select(
                Observation.metric.label("metric"),
                Observation.region_id.label("region_id"),
                collapse.label("value"),
            )
            .where(
                pair_filter,
                Observation.value.isnot(None),
                Observation.observation_date >= window.start,
                Observation.observation_date <= window.end,
            )
            .group_by(Observation.metric, Observation.region_id)
        )
        current_rows = (await db.execute(filters.apply(current_stmt))).all()

        # --- baseline: one figure per prior year, then mean/sd across years ---
        per_year = (
            select(
                Observation.metric.label("metric"),
                Observation.region_id.label("region_id"),
                year.label("yr"),
                collapse.label("value"),
            )
            .where(
                pair_filter,
                Observation.value.isnot(None),
                Observation.observation_date < window.start,
                season,
            )
            .group_by(Observation.metric, Observation.region_id, year)
        )
        per_year_sub = filters.apply(per_year).subquery()

        baseline_stmt = select(
            per_year_sub.c.metric,
            per_year_sub.c.region_id,
            func.avg(per_year_sub.c.value).label("mean"),
            # Population SD over a handful of yearly figures: a rough spread
            # indicator, reported with the year count so it is never mistaken
            # for a rigorous climate normal.
            func.stddev_pop(per_year_sub.c.value).label("sd"),
            func.count().label("years"),
        ).group_by(per_year_sub.c.metric, per_year_sub.c.region_id)
        baseline_rows = (await db.execute(baseline_stmt)).all()

        baselines = {
            (r.metric, r.region_id): (r.mean, r.sd, int(r.years or 0))
            for r in baseline_rows
        }

        for row in current_rows:
            spec = METRICS[row.metric]
            mean, sd, years = baselines.get((row.metric, row.region_id), (None, None, 0))

            z: Optional[float] = None
            if (
                mean is not None
                and sd is not None
                and years >= MIN_BASELINE_YEARS
                and float(sd) > 1e-9
            ):
                z = round((float(row.value) - float(mean)) / float(sd), 2)

            out.setdefault(row.region_id, {})[row.metric] = {
                "value": _round(row.value, spec.decimals),
                "baseline": _round(mean, spec.decimals) if mean is not None else None,
                "standard_deviation": _round(sd, spec.decimals) if sd is not None else None,
                "baseline_years": years,
                "z_score": z,
                # Stated per region: a district with a short record must not
                # look as authoritative as one with ten years behind it.
                "status": "ok" if z is not None else "insufficient_baseline",
            }

    return out


# ---------------------------------------------------------------------------
# Region summaries
# ---------------------------------------------------------------------------


async def region_hierarchy(db: AsyncSession) -> dict[str, Any]:
    """The province -> district -> tehsil tree, built from stored observations.

    Derived from the data rather than from a second hardcoded region list, so
    the filter tree can never offer a region the database has nothing for.
    """
    stmt = (
        select(
            Observation.region_id,
            Observation.region_type,
            Observation.province,
            Observation.district,
            Observation.tehsil,
            func.count().label("observations"),
            func.max(Observation.observation_date).label("latest_observation"),
        )
        .group_by(
            Observation.region_id,
            Observation.region_type,
            Observation.province,
            Observation.district,
            Observation.tehsil,
        )
        .order_by(Observation.province, Observation.district)
    )
    rows = (await db.execute(stmt)).all()

    regions = [
        {
            "region_id": r.region_id,
            "region_type": r.region_type,
            "province": r.province,
            "district": r.district,
            "tehsil": r.tehsil,
            "name": r.tehsil or r.district or r.region_id,
            "observations": r.observations,
            "latest_observation": r.latest_observation.isoformat()
            if r.latest_observation
            else None,
        }
        for r in rows
    ]

    tree: dict[str, dict[str, set]] = {}
    for region in regions:
        province = region["province"] or "Unknown"
        district = region["district"] or "Unknown"
        tree.setdefault(province, {}).setdefault(district, set())
        if region["tehsil"]:
            tree[province][district].add(region["tehsil"])

    return {
        "regions": regions,
        "provinces": [
            {
                "province": province,
                "districts": [
                    {"district": district, "tehsils": sorted(tehsils)}
                    for district, tehsils in sorted(districts.items())
                ],
            }
            for province, districts in sorted(tree.items())
        ],
        "count": len(regions),
    }


async def region_metric_matrix(
    db: AsyncSession,
    *,
    filters: Optional[RegionFilter] = None,
    metrics: tuple[str, ...] = SUMMARY_METRICS,
    window: Optional[Window] = None,
    include_condition: bool = True,
) -> dict[str, dict[str, Any]]:
    """Latest value of each summary metric for every region, in one pass.

    One query for all metrics rather than one per metric: the map needs every
    region's full picture at once, and N round-trips per repaint is what makes
    a dashboard feel slow.
    """
    filters = filters or RegionFilter()
    wanted = [m for m in metrics if m in METRICS]
    dataset_pairs = [(m, METRICS[m].default_dataset) for m in wanted]

    pair_filter = None
    for metric, dataset in dataset_pairs:
        clause = and_(Observation.metric == metric, Observation.dataset == dataset)
        pair_filter = clause if pair_filter is None else (pair_filter | clause)
    if pair_filter is None:
        return {}

    stmt = (
        select(Observation)
        .where(pair_filter, Observation.value.isnot(None))
        .order_by(
            Observation.region_id,
            Observation.metric,
            desc(Observation.observation_date),
        )
        .distinct(Observation.region_id, Observation.metric)
    )
    if window is not None:
        stmt = stmt.where(Observation.observation_date >= window.start)
    stmt = filters.apply(stmt)

    rows = (await db.execute(stmt)).scalars().all()

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = out.setdefault(
            row.region_id,
            {
                "region_id": row.region_id,
                "region_type": row.region_type,
                "province": row.province,
                "district": row.district,
                "tehsil": row.tehsil,
                "name": row.tehsil or row.district or row.region_id,
                "metrics": {},
                "latest_observation": None,
            },
        )
        spec = METRICS[row.metric]
        entry["metrics"][row.metric] = {
            "value": _round(row.value, spec.decimals),
            "unit": row.unit,
            "dataset": row.dataset,
            "observation_date": row.observation_date.isoformat(),
            "cloud_percentage": _round(row.cloud_percentage, 1),
            "pixel_count": row.pixel_count,
            "processing_status": row.processing_status,
        }
        iso = row.observation_date.isoformat()
        if entry["latest_observation"] is None or iso > entry["latest_observation"]:
            entry["latest_observation"] = iso

    # Rainfall is a flux: "the latest daily depth" is close to meaningless for a
    # map, so the summary carries a windowed total instead when a window is
    # given. Kept under a distinct key so it is never confused with an instant
    # reading.
    if window is not None:
        totals = await window_aggregate_per_region(
            db, "rainfall_mm", window, filters=filters
        )
        for region_id, total in totals.items():
            entry = out.get(region_id)
            if entry is not None:
                entry["rainfall_window_total"] = {
                    "value": total["value"],
                    "unit": "mm",
                    "observations": total["observations"],
                    "period": {
                        "start": window.start.isoformat(),
                        "end": window.end.isoformat(),
                    },
                }

    # Per-region anomalies drive the condition colouring for metrics that have
    # no context-free "bad" value. Fetched once for the whole map, not per
    # region — see region_anomalies.
    anomalies: dict[str, dict[str, Any]] = {}
    if window is not None and include_condition:
        anomalies = await region_anomalies(
            db, ANOMALY_CLASSIFIED_METRICS, window, filters=filters
        )

    for region_id, entry in out.items():
        ndvi = entry["metrics"].get("ndvi", {}).get("value")
        entry["vegetation_status"] = classify_ndvi(ndvi)
        entry["crop_condition"] = classify_crop_condition(ndvi)

        if not include_condition:
            continue

        region_anomaly = anomalies.get(region_id, {})
        entry["anomalies"] = region_anomaly

        # One condition per metric, each by the rule appropriate to it.
        conditions: dict[str, dict[str, Any]] = {}

        for metric in VALUE_CLASSIFIED_METRICS:
            value = entry["metrics"].get(metric, {}).get("value")
            conditions[metric] = {
                "level": condition_from_value(value),
                "basis": "value",
                "detail": (
                    f"classified from the measured {METRICS[metric].label}"
                    if value is not None
                    else "no observation in this period"
                ),
            }

        for metric in ANOMALY_CLASSIFIED_METRICS:
            stats = region_anomaly.get(metric)
            z = stats.get("z_score") if stats else None
            level = condition_from_anomaly(z)
            conditions[metric] = {
                "level": level,
                "basis": "anomaly",
                "z_score": z,
                "detail": (
                    f"{abs(z):.1f} SD {'above' if z > 0 else 'below'} this region's "
                    f"seasonal normal ({stats['baseline_years']} prior years)"
                    if z is not None and stats
                    else "not enough prior years for this region and season"
                ),
            }

        # Crop condition shares NDVI's classification — it is derived from it.
        conditions["crop_condition"] = dict(conditions["ndvi"])
        conditions["crop_condition"]["detail"] = (
            "satellite-derived crop/vegetation condition indicator, from NDVI"
        )

        entry["conditions"] = conditions
        # The worst level across metrics, for a single at-a-glance readout.
        ranked = [
            c["level"] for c in conditions.values() if c["level"] != CONDITION_UNKNOWN
        ]
        entry["overall_condition"] = (
            max(ranked, key=lambda level: CONDITION_RANK[level])
            if ranked
            else CONDITION_UNKNOWN
        )

    return out


# ---------------------------------------------------------------------------
# Dataset health and catalogue
# ---------------------------------------------------------------------------

# A dataset is stale once nothing new has arrived for this many nominal
# publication cycles. Two cycles tolerates one missed publication without
# crying wolf; beyond that something is genuinely wrong upstream or with us.
STALENESS_CYCLES = 2
# Floor for scene-based products, whose "nominal" cadence is an average rather
# than a schedule: Sentinel-1 can legitimately be quiet over one area for days.
MIN_STALENESS_DAYS = 10


def staleness_threshold_days(cadence: Cadence) -> int:
    return max(MIN_STALENESS_DAYS, cadence.nominal_days * STALENESS_CYCLES)


def dataset_health(
    *,
    cadence: Cadence,
    latest_observation: Optional[date],
    last_status: Optional[str],
    last_error: Optional[str],
    today: date,
) -> dict[str, Any]:
    """Classify one dataset's state.

    The rules are stated here rather than scattered through the UI so the
    dashboard's badge and this function can never disagree:

      FAILED       last run errored
      NO DATA      nothing has ever been stored
      NO NEW DATA  stored, but nothing newer than the staleness threshold
      WARNING      last run only partly succeeded
      HEALTHY      recent data, clean last run

    "no new data" is explicitly not a failure — a 16-day composite has nothing
    new on 15 days out of 16, and reporting that as an outage would make the
    status bar meaningless.
    """
    threshold = staleness_threshold_days(cadence)

    if last_status == "failed":
        state, detail = "FAILED", last_error or "last ingestion run failed"
    elif latest_observation is None:
        state, detail = "NO DATA", "no observations stored for this dataset yet"
    else:
        age = (today - latest_observation).days
        if age > threshold:
            state = "NO NEW DATA"
            detail = (
                f"newest observation is {age} days old; this product publishes "
                f"about every {cadence.nominal_days} day(s)"
            )
        elif last_status in ("partial_success", "skipped_locked"):
            state = "WARNING"
            detail = f"last run finished as {last_status}"
        else:
            state = "HEALTHY"
            detail = f"newest observation is {age} day(s) old"

    return {
        "state": state,
        "detail": detail,
        "staleness_threshold_days": threshold,
        "age_days": (today - latest_observation).days if latest_observation else None,
    }


async def dataset_coverage(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """What is actually stored per dataset: counts, regions, real date span."""
    stmt = (
        select(
            Observation.dataset,
            func.count().label("observations"),
            func.count(func.distinct(Observation.region_id)).label("regions"),
            func.count(func.distinct(Observation.metric)).label("metrics"),
            func.min(Observation.observation_date).label("earliest"),
            func.max(Observation.observation_date).label("latest"),
            func.max(Observation.ingested_at).label("last_ingested_at"),
        )
        .group_by(Observation.dataset)
    )
    rows = (await db.execute(stmt)).all()
    return {
        r.dataset: {
            "observations": r.observations,
            "regions": r.regions,
            "metrics": r.metrics,
            # The REAL earliest/latest, which is not the same thing as the
            # requested historical window and is reported separately from it.
            "earliest_observation": r.earliest.isoformat() if r.earliest else None,
            "latest_observation": r.latest.isoformat() if r.latest else None,
            "last_ingested_at": r.last_ingested_at.isoformat()
            if r.last_ingested_at
            else None,
        }
        for r in rows
    }


async def checkpoints(db: AsyncSession) -> dict[str, IngestionCheckpoint]:
    rows = (await db.execute(select(IngestionCheckpoint))).scalars().all()
    return {row.dataset: row for row in rows}


# Human-facing provenance for each configured dataset. Provider and revisit
# figures are properties of the mission, not of our database, so they live
# beside the registry rather than being guessed at request time.
DATASET_PROVENANCE: dict[str, dict[str, str]] = {
    "sentinel2": {
        "provider": "ESA Copernicus",
        "platform": "Sentinel-2 A/B/C MSI",
        "purpose": "Vegetation, crop condition, optical spectral analysis",
        "revisit": "~5 days at the equator with two satellites; usable scenes depend on cloud",
    },
    "sentinel1": {
        "provider": "ESA Copernicus",
        "platform": "Sentinel-1 C-band SAR (GRD, IW mode)",
        "purpose": "All-weather radar monitoring; flood and surface-moisture indicators",
        "revisit": "Irregular; depends on acquisition plan and orbit over the area",
    },
    "chirps": {
        "provider": "UCSB Climate Hazards Center / USGS",
        "platform": "CHIRPS v2.0 (satellite–gauge blended)",
        "purpose": "Rainfall and precipitation anomaly monitoring",
        "revisit": "Daily",
    },
    "mod13q1": {
        "provider": "NASA LP DAAC",
        "platform": "Terra MODIS",
        "purpose": "Vegetation indices (NDVI, EVI)",
        "revisit": "16-day composite",
    },
    "mod11a2": {
        "provider": "NASA LP DAAC",
        "platform": "Terra MODIS",
        "purpose": "Land surface temperature, day and night",
        "revisit": "8-day composite",
    },
}


async def dataset_catalog(
    db: AsyncSession, today: Optional[date] = None
) -> list[dict[str, Any]]:
    """The data-source catalogue: registry configuration joined to real coverage.

    Every field is either declared configuration or measured from the database.
    Nothing here is estimated.
    """
    today = today or datetime.now(timezone.utc).date()
    coverage = await dataset_coverage(db)
    points = await checkpoints(db)

    catalog: list[dict[str, Any]] = []
    for name, config in DATASETS.items():
        stored = coverage.get(name, {})
        point = points.get(name)
        latest_iso = stored.get("latest_observation")
        latest = date.fromisoformat(latest_iso) if latest_iso else None

        health = dataset_health(
            cadence=config.cadence,
            latest_observation=latest,
            last_status=point.last_status if point else None,
            last_error=point.last_error if point else None,
            today=today,
        )
        provenance = DATASET_PROVENANCE.get(name, {})

        catalog.append(
            {
                "dataset": name,
                "gee_collection": config.asset_id,
                "version": config.version,
                "provider": provenance.get("provider"),
                "platform": provenance.get("platform"),
                "purpose": provenance.get("purpose"),
                "revisit": provenance.get("revisit"),
                "native_cadence": config.cadence.value,
                "nominal_cadence_days": config.cadence.nominal_days,
                "spatial_resolution_m": config.spatial_resolution,
                "metric_group": config.metric_group,
                "metrics": [
                    {
                        "metric": m,
                        "label": METRICS[m].label if m in METRICS else m,
                        "unit": METRICS[m].unit if m in METRICS else None,
                        "derived": config.derived_for_metric(m) is not None,
                    }
                    for m in config.all_metrics
                ],
                "cloud_masking": config.mask_strategy,
                "cloud_threshold_pct": config.cloud_threshold,
                "enabled": config.enabled,
                "coverage": {
                    "observations": stored.get("observations", 0),
                    "regions": stored.get("regions", 0),
                    "earliest_observation": stored.get("earliest_observation"),
                    "latest_observation": latest_iso,
                    "last_ingested_at": stored.get("last_ingested_at"),
                },
                # Requested vs actually available, kept apart as the brief
                # requires: a shortfall must stay visible, not be papered over.
                "requested_until": point.requested_until.isoformat()
                if point and point.requested_until
                else None,
                "latest_available_at_source": point.latest_available_at_source.isoformat()
                if point and point.latest_available_at_source
                else None,
                "availability_status": point.availability_status if point else None,
                "backfill_complete": bool(point.backfill_complete) if point else False,
                "backfill_cursor": point.backfill_cursor.isoformat()
                if point and point.backfill_cursor
                else None,
                "last_run_at": point.last_run_at.isoformat()
                if point and point.last_run_at
                else None,
                "last_status": point.last_status if point else None,
                "last_error": point.last_error if point else None,
                "health": health,
            }
        )

    catalog.sort(key=lambda d: d["dataset"])
    return catalog


async def recent_runs(db: AsyncSession, limit: int = 10) -> list[dict[str, Any]]:
    stmt = (
        select(IngestionRun).order_by(desc(IngestionRun.started_at)).limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "run_id": r.run_id,
            "mode": r.mode,
            "dry_run": r.dry_run,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_ms": int(
                (r.finished_at - r.started_at).total_seconds() * 1000
            )
            if r.finished_at and r.started_at
            else None,
            "datasets_attempted": r.datasets_attempted,
            "datasets_succeeded": r.datasets_succeeded,
            "datasets_failed": r.datasets_failed,
            "records_inserted": r.records_inserted,
            "records_updated": r.records_updated,
            "records_skipped": r.records_skipped,
            "records_rejected": r.records_rejected,
            "errors": r.errors,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Satellite Watch
# ---------------------------------------------------------------------------

# Thresholds for the watch indicators, in standard deviations from the
# seasonal baseline. Stated as constants and echoed to the client so the
# dashboard can show the rule alongside the indicator — the brief's
# "no arbitrary risk scores" requirement.
WATCH_Z_ADVISORY = 1.5
WATCH_Z_HIGH = 2.0
WATCH_Z_CRITICAL = 3.0

PRIORITY_INFO = "INFO"
PRIORITY_WATCH = "WATCH"
PRIORITY_HIGH = "HIGH"
PRIORITY_CRITICAL = "CRITICAL"


def _priority_for_z(z: float) -> Optional[str]:
    magnitude = abs(z)
    if magnitude >= WATCH_Z_CRITICAL:
        return PRIORITY_CRITICAL
    if magnitude >= WATCH_Z_HIGH:
        return PRIORITY_HIGH
    if magnitude >= WATCH_Z_ADVISORY:
        return PRIORITY_WATCH
    return None


@dataclass
class WatchIndicator:
    """One satellite-derived indicator.

    Explicitly NOT an official warning. Every instance carries the rule that
    produced it and the observation it rests on, so a reader can check the
    reasoning rather than trust a bare severity badge.
    """

    kind: str
    priority: str
    title: str
    detail: str
    metric: Optional[str] = None
    rule: Optional[str] = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "priority": self.priority,
            "title": self.title,
            "detail": self.detail,
            "metric": self.metric,
            "rule": self.rule,
            "evidence": self.evidence,
            # Repeated on every item so it cannot be lost in the UI.
            "classification": "satellite-derived indicator",
            "is_official_warning": False,
        }


async def watch_indicators(
    db: AsyncSession,
    window: Window,
    *,
    filters: Optional[RegionFilter] = None,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Derive watch/advisory indicators from anomalies and data freshness.

    Returns an empty list when the record is too short to support any of them —
    which is the honest answer, and is what a fresh database will produce.
    """
    today = today or datetime.now(timezone.utc).date()
    filters = filters or RegionFilter()
    indicators: list[WatchIndicator] = []

    # --- anomaly-driven indicators ---
    anomaly_rules = (
        (
            "ndvi",
            "vegetation",
            "Vegetation decline",
            "Vegetation index is below the seasonal baseline",
            "negative",
        ),
        (
            "rainfall_mm",
            "rainfall",
            "Rainfall anomaly",
            "Rainfall departs from the seasonal baseline",
            "both",
        ),
        (
            "lst_day_c",
            "temperature",
            "Temperature anomaly",
            "Daytime land surface temperature is above the seasonal baseline",
            "positive",
        ),
    )

    for metric, kind, title, detail, direction in anomaly_rules:
        try:
            result = await anomaly(db, metric, window, filters=filters)
        except ValueError:
            continue
        if result["status"] != "ok":
            continue
        z = result["anomaly"]["z_score"]
        if z is None:
            continue
        # A one-sided rule only fires in its direction: an unusually wet month
        # is not a drought indicator, and a cool spell is not a heat one.
        if direction == "negative" and z >= 0:
            continue
        if direction == "positive" and z <= 0:
            continue

        priority = _priority_for_z(z)
        if priority is None:
            continue

        spec = get_metric(metric)
        indicators.append(
            WatchIndicator(
                kind=kind,
                priority=priority,
                title=title,
                detail=(
                    f"{detail}: {result['observed']['value']} {spec.unit} against a "
                    f"{result['baseline']['value']} {spec.unit} baseline "
                    f"({result['baseline_years']} prior years)."
                ),
                metric=metric,
                rule=(
                    f"|z| >= {WATCH_Z_ADVISORY} advisory, >= {WATCH_Z_HIGH} high, "
                    f">= {WATCH_Z_CRITICAL} critical, where z is the departure from "
                    "the same-season mean in standard deviations"
                ),
                evidence={
                    "z_score": z,
                    "observed": result["observed"]["value"],
                    "baseline": result["baseline"]["value"],
                    "baseline_years": result["baseline_years"],
                    "period": result.get("observed", {}).get("last_observation"),
                },
            )
        )

    # --- data freshness ---
    catalog = await dataset_catalog(db, today=today)
    for entry in catalog:
        state = entry["health"]["state"]
        if state in ("FAILED", "NO DATA"):
            priority = PRIORITY_HIGH if state == "FAILED" else PRIORITY_INFO
        elif state == "NO NEW DATA":
            priority = PRIORITY_WATCH
        else:
            continue
        indicators.append(
            WatchIndicator(
                kind="data_freshness",
                priority=priority,
                title=f"{entry['dataset']}: {state}",
                detail=entry["health"]["detail"],
                rule=(
                    "raised when the newest stored observation is older than "
                    f"{entry['health']['staleness_threshold_days']} days, which is "
                    f"{STALENESS_CYCLES} publication cycles for this product"
                ),
                evidence={
                    "dataset": entry["dataset"],
                    "latest_observation": entry["coverage"]["latest_observation"],
                    "age_days": entry["health"]["age_days"],
                },
            )
        )

    order = {
        PRIORITY_CRITICAL: 0,
        PRIORITY_HIGH: 1,
        PRIORITY_WATCH: 2,
        PRIORITY_INFO: 3,
    }
    indicators.sort(key=lambda i: order.get(i.priority, 9))

    return {
        "indicators": [i.to_json() for i in indicators],
        "count": len(indicators),
        "period": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "priorities": [
            PRIORITY_INFO,
            PRIORITY_WATCH,
            PRIORITY_HIGH,
            PRIORITY_CRITICAL,
        ],
        "thresholds": {
            "advisory_z": WATCH_Z_ADVISORY,
            "high_z": WATCH_Z_HIGH,
            "critical_z": WATCH_Z_CRITICAL,
            "min_baseline_years": MIN_BASELINE_YEARS,
        },
        # Restated at the envelope level as well as per indicator.
        "disclaimer": (
            "These are satellite-derived analytical indicators produced by this "
            "system. They are not official disaster warnings and carry no "
            "authority from NDMA, PDMA or any government body."
        ),
    }


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


async def data_freshness(
    db: AsyncSession, today: Optional[date] = None
) -> dict[str, Any]:
    """Newest observation across all datasets, and how old it is.

    Uses the real observation timestamp, never the wall clock: the whole point
    is to show how far behind "now" the satellite record actually is.
    """
    today = today or datetime.now(timezone.utc).date()
    row = (
        await db.execute(
            select(
                func.max(Observation.observation_date).label("latest"),
                func.max(Observation.ingested_at).label("last_ingested"),
            )
        )
    ).one_or_none()

    latest = row.latest if row else None
    return {
        "latest_observation": latest.isoformat() if latest else None,
        "last_ingested_at": row.last_ingested.isoformat()
        if row and row.last_ingested
        else None,
        "age_days": (today - latest).days if latest else None,
        "as_of": today.isoformat(),
        "status": "no_data" if latest is None else "ok",
    }


async def overview(
    db: AsyncSession,
    window: Window,
    *,
    filters: Optional[RegionFilter] = None,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """The KPI command centre payload.

    Each KPI carries its value, unit, trend, the date it was observed and the
    dataset it came from, so nothing on screen is a bare number without
    provenance.
    """
    today = today or datetime.now(timezone.utc).date()
    filters = filters or RegionFilter()

    # One query for every KPI and both periods, instead of two per metric.
    # See batch_window_stats for why that matters against a remote database.
    headline = ("ndvi", "evi", "rainfall_mm", "lst_day_c", "lst_night_c")
    stats = await batch_window_stats(db, headline, window, filters=filters)

    kpis: dict[str, Any] = {}
    for metric in headline:
        pair = stats.get(metric, {"current": None, "previous": None})
        result = build_trend(metric, window, pair["current"], pair["previous"])
        spec = get_metric(metric)
        current = result["current"]
        kpis[metric] = {
            "metric": metric,
            "label": spec.label,
            "unit": spec.unit,
            "description": spec.description,
            "value": current["value"] if current else None,
            "observation_date": current["last_observation"] if current else None,
            "dataset": current["dataset"] if current else spec.default_dataset,
            "observations": current["observations"] if current else 0,
            "regions": current["regions"] if current else 0,
            "aggregation": spec.temporal_aggregation,
            "change_pct": result["change_pct"],
            "direction": result["direction"],
            "interpretation": result.get("interpretation"),
            "trend_status": result["status"],
            "status": "ok" if current else "no_data",
        }

    # Crop condition is derived from NDVI, and says so.
    ndvi_value = kpis["ndvi"]["value"]
    kpis["crop_condition"] = {
        "metric": "crop_condition",
        "label": "Crop condition",
        "unit": "class",
        "description": (
            "Satellite-derived crop/vegetation condition indicator, classified "
            "from NDVI. Not an agronomic diagnosis."
        ),
        "value": classify_crop_condition(ndvi_value),
        "numeric_basis": ndvi_value,
        "basis_metric": "ndvi",
        "observation_date": kpis["ndvi"]["observation_date"],
        "dataset": kpis["ndvi"]["dataset"],
        "direction": kpis["ndvi"]["direction"],
        "change_pct": kpis["ndvi"]["change_pct"],
        "status": "ok" if ndvi_value is not None else "no_data",
        "classes": [label for _, label in CROP_CONDITION_CLASSES],
    }

    region_stmt = select(
        func.count(func.distinct(Observation.region_id)).label("regions"),
        func.count(func.distinct(Observation.province)).label("provinces"),
        func.count().label("observations"),
    )
    region_stmt = filters.apply(region_stmt)
    region_row = (await db.execute(region_stmt)).one_or_none()

    freshness = await data_freshness(db, today=today)
    catalog = await dataset_catalog(db, today=today)

    healthy = sum(1 for d in catalog if d["health"]["state"] == "HEALTHY")
    failed = sum(1 for d in catalog if d["health"]["state"] == "FAILED")

    return {
        "period": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "label": window.label,
            "bucket": window.bucket,
        },
        "filters": {
            "province": filters.province,
            "district": filters.district,
            "tehsil": filters.tehsil,
            "region_id": filters.region_id,
        },
        "kpis": kpis,
        "coverage": {
            "regions_monitored": int(region_row.regions) if region_row else 0,
            "provinces": int(region_row.provinces) if region_row else 0,
            "observations": int(region_row.observations) if region_row else 0,
        },
        "freshness": freshness,
        "datasets": {
            "total": len(catalog),
            "healthy": healthy,
            "failed": failed,
            "states": {d["dataset"]: d["health"]["state"] for d in catalog},
        },
        "data_source": "timestampdb" if freshness["latest_observation"] else "no_data",
    }
