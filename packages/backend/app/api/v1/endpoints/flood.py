# packages/backend/app/api/v1/endpoints/flood.py
"""Flood SCADA endpoints.

The read endpoints currently return placeholder data; the gate-control
endpoints are stubs that echo the command back. They are authenticated anyway
— this is the API contract the frontend and any future PLC bridge will be
written against, and barrage actuation must never be reachable anonymously.
"""
from enum import Enum

from fastapi import APIRouter, Depends, Path

from app.core.deps import get_current_user, require_operator
from app.db.models import User

router = APIRouter()


class GateAction(str, Enum):
    """Closed set of commands, so an arbitrary string can never reach a PLC."""

    OPEN = "open"
    CLOSE = "close"
    HOLD = "hold"


@router.get("/status")
async def get_flood_status(_user: User = Depends(get_current_user)):
    """Flood SCADA system status. Placeholder data pending telemetry wiring."""
    return {
        "status": "success",
        "message": "Flood SCADA system operational",
        "data_source": "placeholder",
        "gates": [
            {"id": 1, "name": "Barrage A", "location": "Tarbela", "status": "closed", "water_level": 12.5},
            {"id": 2, "name": "Barrage B", "location": "Mangla", "status": "open", "water_level": 8.3},
            {"id": 3, "name": "Barrage C", "location": "Chashma", "status": "closed", "water_level": 6.7},
        ],
    }


@router.get("/gates")
async def get_gates(_user: User = Depends(get_current_user)):
    """All gate positions. Placeholder data pending telemetry wiring."""
    return {
        "status": "success",
        "data_source": "placeholder",
        "gates": [
            {"id": 1, "name": "Gate 1", "position": 0, "target": 0, "mode": "auto"},
            {"id": 2, "name": "Gate 2", "position": 50, "target": 50, "mode": "manual"},
            {"id": 3, "name": "Gate 3", "position": 100, "target": 100, "mode": "auto"},
        ],
    }


@router.post("/gates/{gate_id}/control")
async def control_gate(
    gate_id: int = Path(ge=1, description="Gate identifier"),
    action: GateAction = ...,
    user: User = Depends(require_operator),
):
    """Send a gate command. Restricted to operators and administrators.

    Not yet connected to hardware — it records intent and echoes it back.
    """
    return {
        "status": "success",
        "message": f"Gate {gate_id} {action.value} command accepted",
        "gate_id": gate_id,
        "action": action.value,
        "requested_by": user.username,
        "dispatched": False,
        "detail": "Control plane not yet connected to hardware",
    }
