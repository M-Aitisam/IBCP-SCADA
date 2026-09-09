# packages/backend/app/databases/timestampdb/events.py
"""Hazard events, hotspots, recovery and field verification.

The distinction this module exists to enforce: a satellite observation is not
an event. A flood that lasts nine days produces dozens of observations across
several datasets, and treating each as its own event would give an operator a
list of dozens of "floods" for one flood.

An **event** is a named, persistent thing with a lifecycle. It is created once,
updated as new observations arrive, and resolved when conditions return to
normal. Everything downstream — alerts, replay, recovery monitoring, field
reports — hangs off that identity.

No machine learning anywhere in this layer. State transitions come from
documented deterministic rules over stored values, and every event records the
rule version that produced it so a classification stays interpretable after the
rules change.
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.databases.timestampdb.models import utcnow


class HazardEvent(Base):
    """One persistent hazard occurrence in one region.

    Reusable across hazard types (§11) rather than a flood-specific table: the
    lifecycle, evidence and severity fields are identical for flood, drought,
    heat and crop stress, and a per-hazard table would mean four copies of the
    same lifecycle logic drifting apart.

    Identity for deduplication is (hazard_type, region_id) among rows that are
    not RESOLVED. A second detection while an event is open updates that event;
    it does not create a sibling.
    """

    __tablename__ = "gv_hazard_events"

    # Human-readable and sortable: FLOOD-2026-0007.
    event_id: Mapped[str] = mapped_column(String(48), primary_key=True)

    hazard_type: Mapped[str] = mapped_column(String(32), nullable=False)
    region_id: Mapped[str] = mapped_column(String(64), nullable=False)
    region_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    province: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # DETECTED | CONFIRMED | ESCALATING | PEAK | DECLINING | RESOLVED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DETECTED")
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    peak_severity: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)

    current_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    peak_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    onset_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Points per cycle. Positive means worsening; the sign is what drives the
    # ESCALATING/DECLINING transitions.
    change_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    first_detected_at: Mapped[date] = mapped_column(Date, nullable=False)
    last_updated_at: Mapped[date] = mapped_column(Date, nullable=False)
    peak_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    resolved_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Consecutive cycles the current status has held, for persistence rules.
    consecutive_periods: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Flood-specific extent, null for non-areal hazards. Kept on the shared
    # table rather than a subclass table: two nullable columns are cheaper than
    # a join, and no other hazard has needed extra fields yet.
    affected_area_km2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    peak_area_km2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # What the rules actually saw, so an event can be argued with.
    evidence: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    source_datasets: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data_quality: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Operator adjudication, distinct from the automated status.
    verification_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="UNVERIFIED"
    )
    verified_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    calculation_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_event_open", "hazard_type", "region_id", "status"),
        Index("ix_event_recent", "hazard_type", "last_updated_at"),
        Index("ix_event_region", "region_id", "first_detected_at"),
        Index("ix_event_status", "status", "severity"),
    )


class EventObservation(Base):
    """One cycle's snapshot of an event — the replay timeline (§21).

    Append-only. Storing the state at every cycle rather than only the current
    state is what makes historical replay real rather than reconstructed: the
    timeline shows what the system actually believed on each date, not what a
    later recomputation would say.
    """

    __tablename__ = "gv_event_observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    event_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("gv_hazard_events.event_id", ondelete="CASCADE"),
        nullable=False,
    )
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[str] = mapped_column(String(24), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    affected_area_km2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_from_previous: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    data_quality: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    indicators: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    transition: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # One snapshot per event per cycle, so a re-run overwrites rather than
        # appending a duplicate frame to the timeline.
        UniqueConstraint("event_id", "reference_date", name="uq_event_observation"),
        Index("ix_event_obs_timeline", "event_id", "reference_date"),
    )


class HazardHotspot(Base):
    """A cluster of neighbouring regions under the same hazard (§12).

    Spatially contiguous groups matter operationally in a way individual
    districts do not: seven adjacent districts in severe vegetation stress is a
    regional emergency, while seven scattered ones are seven local problems
    with likely different causes.
    """

    __tablename__ = "gv_hazard_hotspots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    cluster_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hazard_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)

    region_ids: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cluster_size: Mapped[int] = mapped_column(Integer, nullable=False)
    average_severity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    maximum_severity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dominant_level: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    provinces: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    first_detected: Mapped[date] = mapped_column(Date, nullable=False)
    # Regions added since the previous cycle; the operational signal is whether
    # a hotspot is spreading, not merely that it exists.
    growth_regions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    growth_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    calculation_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "cluster_id", "hazard_type", "reference_date", name="uq_hotspot_cycle"
        ),
        Index("ix_hotspot_lookup", "hazard_type", "reference_date"),
    )


class RecoveryMetric(Base):
    """Post-event recovery tracking for one region and indicator (§23).

    Recovery is reported as a *satellite-derived indicator*, never as ecological
    or economic recovery. NDVI returning to its seasonal normal says the
    vegetation signal recovered; it says nothing about whether a farmer was
    compensated or a crop was replanted, and the wording throughout keeps that
    distinction.
    """

    __tablename__ = "gv_recovery_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    event_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("gv_hazard_events.event_id", ondelete="CASCADE"),
        nullable=False,
    )
    region_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)

    baseline_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pre_event_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    minimum_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # 0-100: how far back toward pre-event the indicator has come.
    recovery_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    days_since_event: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # RECOVERING | SLOW_RECOVERY | STALLED | RECOVERED | UNKNOWN
    recovery_status: Mapped[str] = mapped_column(String(24), nullable=False)
    trend: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    calculation_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "event_id", "region_id", "metric", "reference_date",
            name="uq_recovery_identity",
        ),
        Index("ix_recovery_event", "event_id", "reference_date"),
    )


class FieldReport(Base):
    """Ground observation submitted against an alert or event (§20).

    Backend support only in this phase; the Flutter client is future work. The
    schema exists now so field verification can close the loop the moment a
    client is built, and so satellite-derived findings can be confirmed or
    contradicted by someone who was actually there.

    A rejected report is as valuable as a confirmed one: it is the only
    mechanism in the system for discovering that a detection rule is wrong.
    """

    __tablename__ = "gv_field_reports"

    report_id: Mapped[str] = mapped_column(String(48), primary_key=True)

    alert_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    region_id: Mapped[str] = mapped_column(String(64), nullable=False)

    reported_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    hazard_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    observation: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # A reference (object key / URL), never the image bytes: this database is
    # for time series, not blobs.
    photo_reference: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # PENDING | CONFIRMED | REJECTED | NEEDS_INVESTIGATION
    verification_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="PENDING"
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_field_report_alert", "alert_id"),
        Index("ix_field_report_event", "event_id"),
        Index("ix_field_report_region", "region_id", "reported_at"),
        Index("ix_field_report_status", "verification_status"),
    )


class AlertHistory(Base):
    """Every state change an alert has been through (§28/§34).

    Separate from `gv_audit_logs`: that table records operator actions across
    the whole system, this one is the alert's own biography and is what the
    alert detail view renders. Keeping them apart means the audit log stays a
    security record rather than becoming a UI data source.
    """

    __tablename__ = "gv_alert_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    alert_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    from_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    from_severity: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    to_severity: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # "system" for automated transitions, a username for operator actions.
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detail: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_alert_history_alert", "alert_id", "occurred_at"),)


class RegionGeometryStats(Base):
    """Derived geometry facts: area and centroid, per region.

    Computed once from the stored boundaries rather than asked of Earth Engine
    per request. Flood extent in km² needs region area, and re-deriving it from
    a 620 KB GeoJSON on every scoring cycle would be wasteful for a number that
    changes only when the boundary source changes.
    """

    __tablename__ = "gv_region_geometry_stats"

    region_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    region_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    province: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    area_km2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    centroid_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    centroid_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bbox_min_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bbox_min_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bbox_max_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bbox_max_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Adjacency, precomputed so hotspot clustering is a graph walk in memory
    # rather than a spatial join the database cannot do without PostGIS.
    neighbours: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
