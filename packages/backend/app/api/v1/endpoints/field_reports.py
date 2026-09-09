# packages/backend/app/api/v1/endpoints/field_reports.py
"""Field verification (§20) and the alert lifecycle (§18/§28).

Backend support for ground truth. The Flutter client is future work; these
endpoints exist now so that when it arrives the loop closes without a schema
change, and so a satellite-derived detection can be contradicted by someone who
was actually standing there.

A REJECTED report is the most valuable thing in this file. It is the only
mechanism by which the system can learn that a detection rule is wrong — an
automated pipeline with no path for "you got this wrong" cannot be corrected.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.events import (
    ALERT_ACKNOWLEDGED,
    ALERT_CONFIRMED,
    ALERT_DISMISSED,
    ALERT_INVESTIGATING,
    ALERT_RESOLVED,
    ALERT_TRANSITIONS,
    _report_json,
)
from app.core.deps import get_current_user, require_operator
from app.databases.timestampdb.events import AlertHistory, FieldReport, HazardEvent
from app.databases.timestampdb.intelligence import Alert, AuditLog
from app.databases.timestampdb.models import utcnow
from app.db.database import get_db
from app.db.models import User

router = APIRouter()

VERIFICATION_STATES = ("PENDING", "CONFIRMED", "REJECTED", "NEEDS_INVESTIGATION")


# ---------------------------------------------------------------------------
# Field reports
# ---------------------------------------------------------------------------


class CreateFieldReport(BaseModel):
    region_id: str = Field(..., max_length=64)
    observation: str = Field(..., min_length=3, max_length=4000)
    alert_id: Optional[str] = Field(default=None, max_length=64)
    event_id: Optional[str] = Field(default=None, max_length=48)
    hazard_type: Optional[str] = Field(default=None, max_length=32)
    severity: Optional[str] = Field(default=None, max_length=24)
    notes: Optional[str] = Field(default=None, max_length=4000)
    photo_reference: Optional[str] = Field(default=None, max_length=512)
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @field_validator("latitude")
    @classmethod
    def _valid_latitude(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not -90.0 <= v <= 90.0:
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("longitude")
    @classmethod
    def _valid_longitude(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not -180.0 <= v <= 180.0:
            raise ValueError("longitude must be between -180 and 180")
        return v


@router.post("", status_code=201)
async def submit_report(
    request: CreateFieldReport,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit a ground observation.

    Any signed-in user may submit — a field officer is not an operator, and
    requiring elevated privilege to report what you can see would defeat the
    purpose. Reviewing a report is operator-gated; submitting one is not.
    """
    if request.event_id:
        exists = await db.scalar(
            select(func.count()).select_from(HazardEvent).where(
                HazardEvent.event_id == request.event_id
            )
        )
        if not exists:
            raise HTTPException(status_code=404, detail=f"no event {request.event_id!r}")

    report = FieldReport(
        report_id=f"FR-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8]}",
        alert_id=request.alert_id,
        event_id=request.event_id,
        region_id=request.region_id,
        reported_by=user.username,
        latitude=request.latitude,
        longitude=request.longitude,
        hazard_type=request.hazard_type,
        observation=request.observation,
        severity=request.severity,
        notes=request.notes,
        photo_reference=request.photo_reference,
        verification_status="PENDING",
    )
    db.add(report)
    db.add(
        AuditLog(
            actor=user.username, actor_role=user.role, action="field_report.submit",
            entity_type="field_report", entity_id=report.report_id,
            detail={"region_id": request.region_id, "event_id": request.event_id},
        )
    )
    await db.commit()
    return {"status": "success", "report": _report_json(report)}


@router.get("")
async def list_reports(
    status: Optional[str] = Query(None),
    region_id: Optional[str] = Query(None),
    event_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    stmt = select(FieldReport)
    if status:
        stmt = stmt.where(FieldReport.verification_status == status)
    if region_id:
        stmt = stmt.where(FieldReport.region_id == region_id)
    if event_id:
        stmt = stmt.where(FieldReport.event_id == event_id)

    rows = (
        await db.execute(stmt.order_by(desc(FieldReport.reported_at)).limit(limit))
    ).scalars().all()

    return {
        "status": "success",
        "data_source": "timestampdb" if rows else "no_data",
        "count": len(rows),
        "reports": [_report_json(r) for r in rows],
        "verification_states": list(VERIFICATION_STATES),
        "detail": None if rows else "no field reports submitted yet",
    }


class ReviewFieldReport(BaseModel):
    verification_status: str
    review_notes: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("verification_status")
    @classmethod
    def _known_state(cls, v: str) -> str:
        if v not in VERIFICATION_STATES:
            raise ValueError(f"verification_status must be one of {list(VERIFICATION_STATES)}")
        return v


@router.post("/{report_id}/review")
async def review_report(
    report_id: str,
    request: ReviewFieldReport,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_operator),
):
    """Adjudicate a field report. Audited."""
    report = await db.scalar(
        select(FieldReport).where(FieldReport.report_id == report_id)
    )
    if report is None:
        raise HTTPException(status_code=404, detail=f"no report {report_id!r}")

    previous = report.verification_status
    report.verification_status = request.verification_status
    report.reviewed_by = user.username
    report.reviewed_at = utcnow()
    report.review_notes = request.review_notes
    report.updated_at = utcnow()

    db.add(
        AuditLog(
            actor=user.username, actor_role=user.role, action="field_report.review",
            entity_type="field_report", entity_id=report_id,
            detail={
                "from": previous,
                "to": request.verification_status,
                "notes": request.review_notes,
            },
        )
    )
    await db.commit()
    return {"status": "success", "report": _report_json(report)}


# ---------------------------------------------------------------------------
# Alert lifecycle
# ---------------------------------------------------------------------------


class AlertTransition(BaseModel):
    to_status: str
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/alerts/{alert_id}/transition")
async def transition_alert(
    alert_id: str,
    request: AlertTransition,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_operator),
):
    """Move an alert through its lifecycle.

    Transitions are constrained by a state machine rather than accepting any
    status write: a dismissed alert must not be reopened as "investigating",
    and an audit trail is only meaningful if the moves were legal. Every
    transition is written to `gv_alert_history` and the audit log.
    """
    alert = await db.scalar(select(Alert).where(Alert.alert_id == alert_id))
    if alert is None:
        raise HTTPException(status_code=404, detail=f"no alert {alert_id!r}")

    current = alert.status
    allowed = ALERT_TRANSITIONS.get(current)
    if allowed is None:
        raise HTTPException(
            status_code=409, detail=f"alert is in unrecognised state {current!r}"
        )
    if request.to_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot move an alert from {current} to {request.to_status}. "
                f"Legal transitions: {list(allowed) or 'none (terminal state)'}"
            ),
        )

    now = utcnow()
    alert.status = request.to_status
    alert.updated_at = now

    # Each state stamps its own timestamp, so the alert's own row carries the
    # timeline without needing a join for the common case.
    if request.to_status == ALERT_ACKNOWLEDGED:
        alert.acknowledged_at = now
        alert.acknowledged_by = user.username
    elif request.to_status == ALERT_INVESTIGATING:
        alert.investigating_at = now
    elif request.to_status == ALERT_CONFIRMED:
        alert.confirmed_at = now
    elif request.to_status == ALERT_RESOLVED:
        alert.resolved_at = now
    elif request.to_status == ALERT_DISMISSED:
        alert.dismissed_at = now
        alert.dismissed_reason = request.note

    db.add(
        AlertHistory(
            alert_id=alert_id,
            from_status=current,
            to_status=request.to_status,
            from_severity=alert.severity,
            to_severity=alert.severity,
            actor=user.username,
            note=request.note,
        )
    )
    db.add(
        AuditLog(
            actor=user.username, actor_role=user.role,
            action=f"alert.{request.to_status.lower()}",
            entity_type="alert", entity_id=alert_id,
            detail={"from": current, "to": request.to_status, "note": request.note},
        )
    )
    await db.commit()

    return {
        "status": "success",
        "alert_id": alert_id,
        "from_status": current,
        "to_status": request.to_status,
        "legal_next": list(ALERT_TRANSITIONS.get(request.to_status, ())),
    }


@router.get("/alerts/{alert_id}/history")
async def alert_history(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Every state change this alert has been through."""
    rows = (
        await db.execute(
            select(AlertHistory)
            .where(AlertHistory.alert_id == alert_id)
            .order_by(AlertHistory.occurred_at)
        )
    ).scalars().all()

    return {
        "status": "success",
        "alert_id": alert_id,
        "count": len(rows),
        "history": [
            {
                "occurred_at": r.occurred_at.isoformat(),
                "from_status": r.from_status,
                "to_status": r.to_status,
                "actor": r.actor,
                "note": r.note,
                "detail": r.detail,
            }
            for r in rows
        ],
        "lifecycle": list(ALERT_TRANSITIONS),
    }
