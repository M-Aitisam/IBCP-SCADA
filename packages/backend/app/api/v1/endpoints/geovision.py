# packages/backend/app/api/v1/endpoints/geovision.py
"""GeoVision AI endpoints.

Vegetation, rainfall and temperature read real acquired observations out of
timestampdb (see app/ingestion). They return an empty series with
`data_source: "no_data"` when the ingestion pipeline has not run yet, rather
than inventing numbers.

Drought severity and prediction remain explicit placeholders: the drought index
and the XGBoost model are later modules that do not exist yet, and labelling
them as such is more useful than fabricating a score.
"""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import CATALOG_TTL, OBSERVATION_PREFIX, OVERVIEW_TTL, cache
from app.core.deps import get_current_user
from app.databases.timestampdb.models import SatelliteObservation
from app.db.database import get_db
from app.db.models import User
from app.ingestion.config import ingestion_settings
from app.services import geovision_service as gv
from app.services import region_geometry as region_geometry_service

router = APIRouter()

DEFAULT_WINDOW_DAYS = 90


async def _latest_per_region(
    db: AsyncSession,
    metric: str,
    dataset: Optional[str] = None,
    since: Optional[date] = None,
) -> list[dict]:
    """Most recent observation of `metric` for each region.

    Uses DISTINCT ON, which on Postgres returns the first row per partition
    cheaply — no window function or self-join needed.
    """
    stmt = (
        select(SatelliteObservation)
        .where(
            SatelliteObservation.metric == metric,
            SatelliteObservation.value.isnot(None),
        )
        .order_by(
            SatelliteObservation.region_id,
            desc(SatelliteObservation.observation_date),
        )
        .distinct(SatelliteObservation.region_id)
    )
    if dataset:
        stmt = stmt.where(SatelliteObservation.dataset == dataset)
    if since:
        stmt = stmt.where(SatelliteObservation.observation_date >= since)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "region_id": row.region_id,
            "region_type": row.region_type,
            "province": row.province,
            "district": row.district,
            "tehsil": row.tehsil,
            "value": row.value,
            "unit": row.unit,
            "observation_date": row.observation_date.isoformat(),
            "dataset": row.dataset,
            "source_image_id": row.source_image_id,
            "pixel_count": row.pixel_count,
        }
        for row in rows
    ]


def _envelope(metric: str, records: list[dict], **extra) -> dict:
    return {
        "status": "success",
        "metric": metric,
        "data_source": "timestampdb" if records else "no_data",
        "count": len(records),
        "data": records,
        **extra,
    }


@router.get("/vegetation")
async def get_vegetation_data(
    days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=3650),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Latest NDVI per region from acquired MODIS observations."""
    since = date.today() - timedelta(days=days)
    records = await _latest_per_region(db, "ndvi", dataset="mod13q1", since=since)

    # NDVI thresholds are conventional vegetation-vigour bands, not a modelled
    # index; they describe the measurement rather than predicting anything.
    for record in records:
        value = record["value"]
        record["health_status"] = (
            "Good" if value >= 0.5
            else "Moderate" if value >= 0.3
            else "Stressed" if value >= 0.15
            else "Bare/Very stressed"
        )
    return _envelope("ndvi", records, window_days=days)


@router.get("/rainfall")
async def get_rainfall_data(
    days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=3650),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Accumulated CHIRPS rainfall per region over the window.

    Summed over time here (not over space) — the stored value is already an
    areal-average daily depth in mm.
    """
    since = date.today() - timedelta(days=days)
    stmt = (
        select(
            SatelliteObservation.region_id,
            SatelliteObservation.province,
            SatelliteObservation.district,
            SatelliteObservation.tehsil,
            func.sum(SatelliteObservation.value).label("total_mm"),
            func.count().label("observations"),
            func.max(SatelliteObservation.observation_date).label("latest"),
        )
        .where(
            SatelliteObservation.metric == "rainfall_mm",
            SatelliteObservation.value.isnot(None),
            SatelliteObservation.observation_date >= since,
        )
        .group_by(
            SatelliteObservation.region_id,
            SatelliteObservation.province,
            SatelliteObservation.district,
            SatelliteObservation.tehsil,
        )
        .order_by(desc("total_mm"))
    )
    rows = (await db.execute(stmt)).all()
    records = [
        {
            "region_id": r.region_id,
            "province": r.province,
            "district": r.district,
            "tehsil": r.tehsil,
            "total_rainfall_mm": round(float(r.total_mm), 2),
            "observations": r.observations,
            "latest_observation": r.latest.isoformat() if r.latest else None,
            "unit": "mm",
        }
        for r in rows
    ]
    return _envelope("rainfall_mm", records, window_days=days)


@router.get("/temperature")
async def get_temperature_data(
    days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=3650),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Latest MODIS land surface temperature per region, in Celsius."""
    since = date.today() - timedelta(days=days)
    records = await _latest_per_region(db, "lst_day_c", dataset="mod11a2", since=since)
    return _envelope("lst_day_c", records, window_days=days)


@router.get("/timeseries/{region_id}")
async def get_region_timeseries(
    region_id: str,
    metric: str = Query(..., description="e.g. ndvi, rainfall_mm, lst_day_c"),
    days: int = Query(365, ge=1, le=3650),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Full observation history for one region and metric.

    This is the endpoint downstream charting and ML feature extraction should
    use — it preserves each product's native temporal resolution.
    """
    since = date.today() - timedelta(days=days)
    stmt = (
        select(SatelliteObservation)
        .where(
            SatelliteObservation.region_id == region_id,
            SatelliteObservation.metric == metric,
            SatelliteObservation.observation_date >= since,
        )
        .order_by(SatelliteObservation.observation_date)
    )
    rows = (await db.execute(stmt)).scalars().all()
    series = [
        {
            "observation_date": row.observation_date.isoformat(),
            "observation_timestamp": row.observation_timestamp.isoformat(),
            "value": row.value,
            "unit": row.unit,
            "dataset": row.dataset,
            "source_image_id": row.source_image_id,
            "cloud_percentage": row.cloud_percentage,
            "processing_status": row.processing_status,
        }
        for row in rows
    ]
    return {
        "status": "success",
        "region_id": region_id,
        "metric": metric,
        "data_source": "timestampdb" if series else "no_data",
        "count": len(series),
        "window_days": days,
        "series": series,
    }


@router.get("/coverage")
async def get_ingestion_coverage(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """What the acquisition pipeline has actually stored, per dataset.

    Lets the dashboard show real coverage instead of implying data exists.
    """
    stmt = (
        select(
            SatelliteObservation.dataset,
            func.count().label("observations"),
            func.count(func.distinct(SatelliteObservation.region_id)).label("regions"),
            func.min(SatelliteObservation.observation_date).label("earliest"),
            func.max(SatelliteObservation.observation_date).label("latest"),
        )
        .group_by(SatelliteObservation.dataset)
        .order_by(SatelliteObservation.dataset)
    )
    rows = (await db.execute(stmt)).all()
    return {
        "status": "success",
        "datasets": [
            {
                "dataset": r.dataset,
                "observations": r.observations,
                "regions": r.regions,
                "earliest": r.earliest.isoformat() if r.earliest else None,
                "latest": r.latest.isoformat() if r.latest else None,
            }
            for r in rows
        ],
    }


@router.get("/drought")
async def get_drought_data(_user: User = Depends(get_current_user)):
    """Drought severity — NOT YET IMPLEMENTED.

    The drought index is a downstream module that has not been built. Returning
    an explicit not-implemented marker rather than invented severity scores.
    """
    return {
        "status": "not_implemented",
        "data_source": "none",
        "detail": (
            "Drought index computation is a downstream module. Raw NDVI, "
            "rainfall and LST observations are available from /vegetation, "
            "/rainfall and /temperature."
        ),
        "data": [],
    }


@router.get("/predict")
async def get_prediction(
    region_id: Optional[str] = Query(None, description="Region to forecast; required once a model exists"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """2-week-ahead drought severity prediction.

    Delegates to the real trained model at app.services.ml.predictor once one
    exists (see packages/ml-pipeline). Kept as an honest "not_implemented"
    until then — this endpoint's earlier hardcoded accuracy/confidence figures
    described a model that did not exist, which is exactly what this must not
    regress back into.
    """
    from app.services.ml.predictor import ModelNotAvailable, get_predictor

    try:
        get_predictor()
    except ModelNotAvailable as exc:
        return {
            "status": "not_implemented",
            "data_source": "none",
            "detail": str(exc),
            "predictions": None,
        }

    if not region_id:
        raise HTTPException(
            status_code=422,
            detail="A trained model is available — pass ?region_id=... to forecast, "
            "or use POST /api/v1/ml/predict/drought directly.",
        )

    from app.api.v1.endpoints.ml import PredictRequest, predict_drought

    result = await predict_drought(PredictRequest(region_id=region_id), db=db, _user=_user)
    return {"status": "ok", "data_source": "model", "predictions": [result]}


# ===========================================================================
# GeoVision AI command-centre endpoints
# ===========================================================================
#
# The endpoints above are the original per-metric feeds and stay as they are —
# the existing dashboard and any external consumer still call them. What
# follows serves the command centre, where the defining constraint is that a
# ten-year view must not ship ten years of rows to the browser. Everything here
# aggregates in Postgres and returns a bounded payload.
#
# Every response distinguishes measurement from derivation and reports
# `data_source: "no_data"` rather than substituting a plausible-looking number.


def _region_filter(
    province: Optional[str] = Query(None, description="Exact province name as stored"),
    district: Optional[str] = Query(None),
    tehsil: Optional[str] = Query(None),
    region_id: Optional[str] = Query(None, description="Most specific; overrides the rest"),
) -> gv.RegionFilter:
    """Shared cascading geography filter.

    One dependency used by every endpoint below, so the map, KPIs, charts and
    tables cannot end up filtered differently from one another.
    """
    return gv.RegionFilter(
        province=province, district=district, tehsil=tehsil, region_id=region_id
    )


def _window(
    range: str = Query(
        "3m",
        description="Named range: 7d, 30d, 3m, 6m, 1y, 5y, 10y. Ignored if start/end given.",
    ),
    start: Optional[date] = Query(None, description="Custom range start (inclusive)"),
    end: Optional[date] = Query(None, description="Custom range end (inclusive)"),
) -> gv.Window:
    try:
        return gv.resolve_window(range_key=range, start=start, end=end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/overview")
async def get_overview(
    window: gv.Window = Depends(_window),
    filters: gv.RegionFilter = Depends(_region_filter),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """KPI command centre: every headline figure with its provenance.

    Cached briefly. Observations only change when the pipeline runs, so a short
    TTL bounds staleness while absorbing the burst of requests a dashboard load
    produces.
    """
    key = (
        f"{OBSERVATION_PREFIX}overview:{window.start}:{window.end}:"
        f"{filters.cache_key()}"
    )
    return await cache.get_or_set(
        key,
        lambda: gv.overview(db, window, filters=filters),
        ttl=OVERVIEW_TTL,
    )


@router.get("/regions")
async def get_regions(
    window: gv.Window = Depends(_window),
    filters: gv.RegionFilter = Depends(_region_filter),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Every region with its latest value for each summary metric.

    This is what the choropleth and the vegetation table both read, so the map
    and the table are guaranteed to agree.
    """
    matrix = await gv.region_metric_matrix(db, filters=filters, window=window)
    regions = sorted(matrix.values(), key=lambda r: (r["province"] or "", r["name"]))
    return {
        "status": "success",
        "data_source": "timestampdb" if regions else "no_data",
        "count": len(regions),
        "period": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "metrics": list(gv.SUMMARY_METRICS),
        "regions": regions,
        # Sent with the data so the map legend renders the thresholds actually
        # applied, instead of a copy that can drift.
        "classification": {
            "ndvi": [
                {"min": threshold, "label": label} for threshold, label in gv.NDVI_CLASSES
            ],
            "crop_condition": [
                {"min": threshold, "label": label}
                for threshold, label in gv.CROP_CONDITION_CLASSES
            ],
        },
        # The traffic-light scale, plus the rule behind each metric's colour.
        # Two different rules are in play and the UI must be able to say which:
        # vegetation indices are classified from the reading, while temperature
        # and rainfall are classified from their departure from that region's
        # own seasonal normal — there is no context-free "too hot".
        "condition_scale": {
            "levels": [
                {"level": level, "label": gv.CONDITION_LABELS[level], "rank": rank}
                for level, rank in sorted(
                    gv.CONDITION_RANK.items(), key=lambda kv: kv[1]
                )
            ],
            "value_classified_metrics": list(gv.VALUE_CLASSIFIED_METRICS),
            "anomaly_classified_metrics": list(gv.ANOMALY_CLASSIFIED_METRICS),
            "value_bands": [
                {"min": threshold, "level": level}
                for threshold, level in gv.VEGETATION_CONDITION_BANDS
            ],
            "anomaly_thresholds": {
                "watch_z": gv.WATCH_Z_ADVISORY,
                "stressed_z": gv.WATCH_Z_HIGH,
                "critical_z": gv.WATCH_Z_CRITICAL,
                "min_baseline_years": gv.MIN_BASELINE_YEARS,
            },
        },
    }


@router.get("/regions/hierarchy")
async def get_region_hierarchy(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Pakistan -> province -> district -> tehsil tree for the cascading filters.

    Built from stored observations, so the filter can never offer a region the
    database has no data for.
    """
    key = f"{OBSERVATION_PREFIX}hierarchy"
    payload = await cache.get_or_set(
        key, lambda: gv.region_hierarchy(db), ttl=CATALOG_TTL
    )
    return {"status": "success", **payload}


@router.get("/regions/geometry")
async def get_region_geometry(
    response: Response,
    simplify_metres: float = Query(
        region_geometry_service.DEFAULT_SIMPLIFY_METRES,
        ge=region_geometry_service.MIN_SIMPLIFY_METRES,
        le=region_geometry_service.MAX_SIMPLIFY_METRES,
        description="Server-side simplification tolerance in metres",
    ),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Simplified region boundaries as GeoJSON, for the GIS map.

    Exported from the same source the ingestion ROI uses, so every feature's
    region_id joins directly to the observations.

    Cached in three tiers — process memory, then the database, then Earth
    Engine — and additionally at the browser, because this is by far the
    largest response the dashboard fetches (~620 KB for 119 districts) and
    boundaries change on the order of years. `private` rather than `public`:
    the response is behind authentication and must not be held by a shared
    proxy.
    """
    try:
        payload = await region_geometry_service.region_geometry(simplify_metres, db=db)
        response.headers["Cache-Control"] = "private, max-age=86400"
        return payload
    except region_geometry_service.GeometryUnavailable as exc:
        # 503, not 500: the API is fine, the boundary source is not reachable,
        # and the map should say so rather than render an empty world.
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.get("/region/{region_id}")
async def get_region_detail(
    region_id: str,
    window: gv.Window = Depends(_window),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Everything the region detail panel shows for one region.

    Current health, per-dataset latest observation, data quality, and the trend
    for each summary metric — in one round-trip rather than one per card.
    """
    filters = gv.RegionFilter(region_id=region_id)
    matrix = await gv.region_metric_matrix(db, filters=filters, window=window)
    region = matrix.get(region_id)

    if region is None:
        # A region with no observations is a legitimate answer, not a 404: the
        # region may exist in the ROI and simply not have been ingested yet.
        return {
            "status": "success",
            "region_id": region_id,
            "data_source": "no_data",
            "region": None,
            "detail": "No observations stored for this region.",
        }

    # One query for all five metrics and both periods, rather than two per
    # metric — see batch_window_stats. This endpoint was the slowest on the
    # dashboard purely from sequential round trips.
    stats = await gv.batch_window_stats(
        db, gv.SUMMARY_METRICS, window, filters=filters
    )
    trends = {
        metric: gv.build_trend(
            metric,
            window,
            stats.get(metric, {}).get("current"),
            stats.get(metric, {}).get("previous"),
        )
        for metric in gv.SUMMARY_METRICS
    }

    # Per-dataset latest, so the panel can show "Latest Sentinel-2 / CHIRPS /
    # MOD13Q1 ..." with the real acquisition dates.
    dataset_rows = (
        await db.execute(
            select(
                SatelliteObservation.dataset,
                func.max(SatelliteObservation.observation_date).label("latest"),
                func.count().label("observations"),
                func.avg(SatelliteObservation.cloud_percentage).label("mean_cloud"),
            )
            .where(SatelliteObservation.region_id == region_id)
            .group_by(SatelliteObservation.dataset)
        )
    ).all()

    return {
        "status": "success",
        "data_source": "timestampdb",
        "region_id": region_id,
        "period": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "region": region,
        "trends": trends,
        "datasets": [
            {
                "dataset": r.dataset,
                "latest_observation": r.latest.isoformat() if r.latest else None,
                "observations": r.observations,
                "mean_cloud_percentage": round(float(r.mean_cloud), 1)
                if r.mean_cloud is not None
                else None,
            }
            for r in sorted(dataset_rows, key=lambda x: x.dataset)
        ],
    }


@router.get("/trends")
async def get_trends(
    metric: str = Query("ndvi", description="ndvi, evi, rainfall_mm, lst_day_c, ..."),
    dataset: Optional[str] = Query(None, description="Override the default source"),
    window: gv.Window = Depends(_window),
    filters: gv.RegionFilter = Depends(_region_filter),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Bucketed time series plus the period-over-period comparison.

    The bucket is chosen from the span (daily / weekly / monthly), so a 10-year
    request returns ~120 points rather than millions of rows. Buckets with no
    source observation are absent, never zero-filled.
    """
    try:
        payload = await gv.series(
            db, metric, window, dataset=dataset, filters=filters
        )
        payload["trend"] = await gv.trend(
            db, metric, window, dataset=dataset, filters=filters
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"status": "success", **payload}


@router.get("/anomaly")
async def get_anomaly(
    metric: str = Query("ndvi"),
    window: gv.Window = Depends(_window),
    filters: gv.RegionFilter = Depends(_region_filter),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Departure from the same calendar season in prior years.

    A DERIVED quantity, labelled as such, reported only when enough prior years
    exist to form a baseline. Below that it returns `insufficient_data` with the
    reason instead of a number that would look authoritative and mean nothing.
    """
    try:
        payload = await gv.anomaly(db, metric, window, filters=filters)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"status": "success", **payload}


@router.get("/datasets")
async def get_dataset_catalog(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Data-source catalogue: configuration joined to measured coverage.

    Provider, collection, resolution and cadence are declared configuration;
    counts and date spans are measured from the database. Nothing is estimated.
    """
    key = f"{OBSERVATION_PREFIX}catalog"
    catalog = await cache.get_or_set(
        key, lambda: gv.dataset_catalog(db), ttl=CATALOG_TTL
    )
    return {
        "status": "success",
        "count": len(catalog),
        "datasets": catalog,
        "boundary_source": {
            "asset": ingestion_settings.GEE_ROI_ASSET_ID,
            "region_type": ingestion_settings.GEE_ROI_REGION_TYPE,
            # Stated plainly because it affects what users see on the map: GAUL
            # 2015 predates the 2018 FATA/KP merger, so it still lists
            # "North-West Frontier" and FATA separately, and has no
            # Gilgit-Baltistan or AJK under the Pakistan country filter.
            "vintage_note": (
                "FAO GAUL 2015 administrative boundaries. Province names reflect "
                "the pre-2018 arrangement; Gilgit-Baltistan and Azad Jammu & "
                "Kashmir are not included in this source."
            ),
        },
    }


@router.get("/ingestion-status")
async def get_ingestion_status(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Per-dataset ingestion health for the monitoring panel.

    A dataset with nothing new is reported as NO NEW DATA, which is a normal
    state for a 16-day composite and must not read as a system failure.
    """
    catalog = await gv.dataset_catalog(db)
    runs = await gv.recent_runs(db, limit=10)
    return {
        "status": "success",
        "datasets": [
            {
                "dataset": d["dataset"],
                "state": d["health"]["state"],
                "detail": d["health"]["detail"],
                "latest_observation": d["coverage"]["latest_observation"],
                "earliest_observation": d["coverage"]["earliest_observation"],
                "last_ingested_at": d["coverage"]["last_ingested_at"],
                "last_run_at": d["last_run_at"],
                "last_status": d["last_status"],
                "last_error": d["last_error"],
                "observations": d["coverage"]["observations"],
                "regions": d["coverage"]["regions"],
                "native_cadence": d["native_cadence"],
                "requested_until": d["requested_until"],
                "latest_available_at_source": d["latest_available_at_source"],
                "availability_status": d["availability_status"],
                "backfill_complete": d["backfill_complete"],
                "backfill_cursor": d["backfill_cursor"],
            }
            for d in catalog
        ],
        "recent_runs": runs,
        "states": ["HEALTHY", "WARNING", "NO NEW DATA", "NO DATA", "FAILED"],
    }


@router.get("/watch")
async def get_satellite_watch(
    window: gv.Window = Depends(_window),
    filters: gv.RegionFilter = Depends(_region_filter),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Satellite Watch indicators.

    These are analytical indicators derived from the stored observations. They
    are NOT official disaster warnings, and every item says so along with the
    rule and evidence behind it.
    """
    payload = await gv.watch_indicators(db, window, filters=filters)
    return {"status": "success", **payload}


@router.get("/freshness")
async def get_data_freshness(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Newest real observation timestamp, and how far behind now it is.

    Always the observation date, never today's date — the gap is the point.
    """
    return {"status": "success", **await gv.data_freshness(db)}
