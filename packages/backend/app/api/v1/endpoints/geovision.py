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

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.databases.timestampdb.models import SatelliteObservation
from app.db.database import get_db
from app.db.models import User

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
async def get_prediction(_user: User = Depends(get_current_user)):
    """Model predictions — NOT YET IMPLEMENTED.

    No model has been trained. The previous hardcoded accuracy and confidence
    figures described a model that does not exist.
    """
    return {
        "status": "not_implemented",
        "data_source": "none",
        "detail": "No prediction model has been trained yet.",
        "predictions": None,
    }
