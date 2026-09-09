# packages/backend/app/api/v1/endpoints/soil.py
"""Soil monitoring endpoints.

Read endpoints return placeholder data; pump control is a stub. Authenticated
regardless — drainage pump actuation must never be reachable anonymously.
"""
from enum import Enum

from fastapi import APIRouter, Depends, Path

from app.core.deps import get_current_user, require_operator
from app.db.models import User

router = APIRouter()


class PumpAction(str, Enum):
    START = "start"
    STOP = "stop"


@router.get("/salinity")
async def get_salinity_data(_user: User = Depends(get_current_user)):
    """Soil salinity readings. Placeholder data pending sensor ingestion."""
    return {
        "status": "success",
        "data_source": "placeholder",
        "data": [
            {"tehsil": "Sahiwal", "salinity": "Moderate", "ph": 7.8, "ec": 2.5},
            {"tehsil": "Okara", "salinity": "High", "ph": 8.5, "ec": 4.2},
            {"tehsil": "Bahawalpur", "salinity": "Low", "ph": 7.2, "ec": 1.2},
        ],
    }


@router.get("/degradation")
async def get_degradation_data(_user: User = Depends(get_current_user)):
    """Land degradation summary. Placeholder data pending analysis pipeline."""
    return {
        "status": "success",
        "data_source": "placeholder",
        "data": [
            {"tehsil": "Sahiwal", "degradation": "Moderate", "area_affected": 120.5},
            {"tehsil": "Okara", "degradation": "Severe", "area_affected": 85.3},
        ],
    }


@router.post("/pumps/{pump_id}/control")
async def control_pump(
    pump_id: int = Path(ge=1, description="Pump identifier"),
    action: PumpAction = ...,
    user: User = Depends(require_operator),
):
    """Send a drainage pump command. Operators and administrators only."""
    return {
        "status": "success",
        "message": f"Pump {pump_id} {action.value} command accepted",
        "pump_id": pump_id,
        "action": action.value,
        "requested_by": user.username,
        "dispatched": False,
        "detail": "Control plane not yet connected to hardware",
    }
