# packages/backend/app/api/v1/endpoints/ml.py
"""Drought prediction API — serves the trained XGBoost model.

Unlike app/intelligence/*, which computes CURRENT hazard state deterministically,
this endpoint predicts FUTURE (2-week-ahead) severity from a trained model. If
no model has been trained yet, every endpoint here says so explicitly rather
than returning a plausible-looking placeholder — the same convention
geovision.py's /predict stub already established.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_operator
from app.databases.timestampdb.intelligence import DerivedFeature, HazardScore
from app.databases.timestampdb.intelligence import ModelVersion as ModelVersionRow
from app.db.database import get_db
from app.db.models import User
from app.intelligence.analytics_service import FEATURE_VERSION
from app.intelligence import hazards as hazard_engine
from app.services.ml.predictor import ModelNotAvailable, get_predictor
from app.services.ml.retrain_control import RetrainControlError, dispatch_retrain

router = APIRouter()


def _score_to_severity(score: float) -> int:
    """Must match packages/ml-pipeline/scripts/extract_training_data.py exactly —
    this is how the model's training labels were derived, so live severity
    features have to be computed the same way."""
    if score >= 80:
        return 0
    if score >= 60:
        return 1
    if score >= 40:
        return 2
    if score >= 20:
        return 3
    return 4


async def _latest_ndvi_lags(db: AsyncSession, region_id: str, n: int) -> list[Optional[float]]:
    from app.databases.timestampdb.models import SatelliteObservation

    rows = (
        await db.execute(
            select(SatelliteObservation.value, SatelliteObservation.observation_date)
            .where(
                SatelliteObservation.region_id == region_id,
                SatelliteObservation.metric == "ndvi",
                SatelliteObservation.value.isnot(None),
            )
            .order_by(SatelliteObservation.observation_date.desc())
            .limit(n)
        )
    ).all()
    values = [float(r[0]) for r in rows]
    values += [None] * (n - len(values))
    return values


async def _latest_derived_feature(
    db: AsyncSession, region_id: str, metric: str
) -> Optional[DerivedFeature]:
    return await db.scalar(
        select(DerivedFeature)
        .where(
            DerivedFeature.region_id == region_id,
            DerivedFeature.metric == metric,
            DerivedFeature.calculation_version == FEATURE_VERSION,
        )
        .order_by(DerivedFeature.reference_date.desc())
        .limit(1)
    )


async def _latest_drought_score(db: AsyncSession, region_id: str) -> Optional[HazardScore]:
    return await db.scalar(
        select(HazardScore)
        .where(
            HazardScore.region_id == region_id,
            HazardScore.hazard == "drought",
            HazardScore.calculation_version == hazard_engine.HAZARD_VERSION,
        )
        .order_by(HazardScore.reference_date.desc())
        .limit(1)
    )


class FeatureGap(BaseModel):
    field: str
    reason: str


async def build_live_features(
    db: AsyncSession, region_id: str, ndvi_lags: int = 8
) -> tuple[dict[str, float], list[FeatureGap]]:
    """Assemble a feature row for `region_id` from whatever the analytics
    cascade has already computed, matching the feature set
    feature_engineering.py builds at training time (see its FEATURE_COLUMNS).

    Returns (features, gaps) — `gaps` lists anything that could not be
    filled, so the caller can decide whether the prediction is trustworthy
    rather than silently substituting a default.
    """
    gaps: list[FeatureGap] = []
    features: dict[str, float] = {}

    ndvi_values = await _latest_ndvi_lags(db, region_id, ndvi_lags)
    for i, value in enumerate(ndvi_values, start=1):
        if value is None:
            gaps.append(FeatureGap(field=f"ndvi_t{i}", reason="no NDVI observation available"))
        else:
            features[f"ndvi_t{i}"] = value

    rainfall_feature = await _latest_derived_feature(db, region_id, "rainfall_mm")
    if rainfall_feature is not None and rainfall_feature.z_score is not None:
        features["spi_30d"] = rainfall_feature.z_score
    else:
        gaps.append(FeatureGap(field="spi_30d", reason="no rainfall derived-feature z-score yet"))

    lst_feature = await _latest_derived_feature(db, region_id, "lst_day_c")
    if lst_feature is not None and lst_feature.seasonal_anomaly is not None:
        features["lst_anomaly"] = lst_feature.seasonal_anomaly
    else:
        gaps.append(FeatureGap(field="lst_anomaly", reason="no LST derived-feature anomaly yet"))

    latest_hazard = await _latest_drought_score(db, region_id)
    if latest_hazard is not None and latest_hazard.score is not None:
        features["drought_duration"] = float(latest_hazard.consecutive_periods or 0)
        features["prev_severity"] = float(_score_to_severity(latest_hazard.score))
    else:
        gaps.append(FeatureGap(field="drought_duration", reason="no drought hazard score yet"))
        gaps.append(FeatureGap(field="prev_severity", reason="no drought hazard score yet"))

    features["month"] = float(date.today().month)

    return features, gaps


class PredictRequest(BaseModel):
    region_id: str
    # Advanced/testing override: supply the full feature row directly instead
    # of having the endpoint assemble it from the database.
    features: Optional[dict[str, float]] = None


@router.post("/predict/drought")
async def predict_drought(
    body: PredictRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        predictor = get_predictor()
    except ModelNotAvailable as exc:
        return {
            "status": "not_implemented",
            "detail": str(exc),
            "prediction": None,
        }

    if body.features is not None:
        features, gaps = body.features, []
    else:
        features, gaps = await build_live_features(db, body.region_id)

    missing_required = [f for f in predictor.features if f not in features and f != "region_id_enc"]
    if missing_required:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"cannot build a prediction for region {body.region_id!r} — "
                "required inputs are missing",
                "missing_features": missing_required,
                "gaps": [g.model_dump() for g in gaps],
            },
        )

    try:
        result = predictor.predict(body.region_id, features)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    result["forecast_target_date"] = (date.today() + timedelta(weeks=2)).isoformat()
    result["feature_gaps"] = [g.model_dump() for g in gaps]
    result["disclaimer"] = (
        "A satellite-derived model forecast, not an official warning. Treat "
        "alongside the current hazard state in /intelligence/hazards."
    )
    return result


@router.get("/model/info")
async def model_info(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        predictor = get_predictor()
    except ModelNotAvailable as exc:
        return {"status": "not_implemented", "detail": str(exc), "model": None}

    registered: Optional[ModelVersionRow] = await db.scalar(
        select(ModelVersionRow)
        .where(ModelVersionRow.model_version == predictor.metadata.get("model_version"))
        .limit(1)
    )

    return {
        "status": "ok",
        "model": {
            "version": predictor.metadata.get("model_version"),
            "trained_at": predictor.metadata.get("trained_at"),
            "f1_score": predictor.metadata.get("f1_score"),
            "r2_score": predictor.metadata.get("r2_score"),
            "rmse": predictor.metadata.get("rmse"),
            "meets_target": predictor.metadata.get("meets_target"),
            "lead_time_weeks": predictor.metadata.get("lead_time_weeks"),
            "features": predictor.features,
            "classes": predictor.classes,
            "registered_in_db": registered is not None,
        },
    }


@router.post("/retrain")
async def retrain(_user: User = Depends(require_operator)):
    """Dispatch the retraining workflow. Does not train in-process — see
    app.services.ml.retrain_control for why."""
    try:
        result = await dispatch_retrain()
    except RetrainControlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return result.to_json()
