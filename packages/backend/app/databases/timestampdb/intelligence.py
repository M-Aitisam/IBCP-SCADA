# packages/backend/app/databases/timestampdb/intelligence.py
"""Analytics and intelligence tables.

Everything here is DERIVED from `gee_satellite_observations`. Nothing in this
module stores a satellite measurement — observations remain the single source
of truth, and every table below records how it was computed so a figure on the
dashboard can be traced back to the scenes behind it.

Three conventions hold throughout:

1. **Every derived row carries a `calculation_version`.** Change the maths and
   the version changes, so old rows stay interpretable instead of silently
   meaning something different from new ones.

2. **Every derived row records its inputs.** Contributors, feature snapshots
   and source observation counts are stored, not just the output score. A risk
   score with no visible contributors is an oracle, and an oracle is useless in
   an operational setting.

3. **Insufficient input produces an explicit state, never a default.** A region
   with no baseline gets `insufficient_data`, not a zero or a neutral score.
   Registering these tables on the existing `Base` keeps Alembic aware of them
   without a second metadata registry.
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


# ---------------------------------------------------------------------------
# Phase 1 — dataset registry
# ---------------------------------------------------------------------------


class DatasetRegistry(Base):
    """Operational state of every configured satellite dataset.

    The static half (collection id, resolution, cadence) is synchronised from
    `app.ingestion.registry`, which stays the single place a dataset is
    *defined* — this table is its runtime mirror, not a competing definition.
    Duplicating the definition here would let the two drift, and the pipeline
    would then reduce over one configuration while the dashboard described
    another.

    The dynamic half (latest observation, coverage, failure counts, quality)
    is written by the ingestion orchestrator, so the dashboard reads dataset
    status from the database rather than recomputing it per request.
    """

    __tablename__ = "gv_dataset_registry"

    dataset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    gee_collection: Mapped[str] = mapped_column(String(160), nullable=False)
    satellite: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    sensor: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    spatial_resolution_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    temporal_resolution: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    expected_update_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # `available_from` is the mission's start; `earliest_observation` is what we
    # actually hold. Kept apart so a gap in our coverage is never mistaken for a
    # gap in the archive.
    available_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    earliest_observation: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    latest_available_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    latest_observation: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    last_attempted_ingestion: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_successful_ingestion: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    freshness_state: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    lag_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    processing_method: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cloud_filter: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    bands: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    derived_indices: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    region_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    coverage_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registry_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    extra_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Phase 9 — historical baselines
# ---------------------------------------------------------------------------


class MetricBaseline(Base):
    """Reproducible climatology for one region, metric and period-of-year.

    `period_type` is 'month' or 'doy_window'. Storing baselines rather than
    recomputing them per request matters at scale: a national map needs a
    baseline for every region and metric at once, and recomputing 119 × 5
    climatologies from a decade of rows on each page load is exactly the N+1
    the rest of this codebase avoids.

    `sample_years` is stored alongside every statistic because a mean over two
    years and a mean over ten are not the same claim, and the UI must be able
    to say which it is showing.
    """

    __tablename__ = "gv_metric_baselines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    region_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # 1-12 for months; the window's centre day-of-year otherwise.
    period_key: Mapped[int] = mapped_column(Integer, nullable=False)

    mean_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    median_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stddev_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    min_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    p10_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    p25_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    p75_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    p90_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sample_years: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # False when the sample is too thin to be a climatology; the row is kept so
    # the UI can explain the gap instead of silently having nothing.
    is_sufficient: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    calculation_version: Mapped[str] = mapped_column(String(16), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # One baseline per region/metric/period/version — the upsert key.
        UniqueConstraint(
            "region_id",
            "metric",
            "dataset",
            "period_type",
            "period_key",
            "calculation_version",
            name="uq_baseline_identity",
        ),
        Index("ix_baseline_lookup", "metric", "period_type", "period_key"),
        Index("ix_baseline_region", "region_id", "metric"),
    )


# ---------------------------------------------------------------------------
# Phase 8 — derived features and anomalies
# ---------------------------------------------------------------------------


class DerivedFeature(Base):
    """Temporal intelligence for one region, metric and reference date.

    One row holds the full temporal picture — current value, short and medium
    term change, seasonal anomaly, year-over-year, percentile rank — because
    the dashboard and the ML feature builder both want all of it at once and
    splitting it across rows would mean a join per metric.

    Every field is nullable on purpose. A missing 30-day change means the
    history to compute it does not exist, and that is stored as NULL rather
    than zero, which would read as "no change".
    """

    __tablename__ = "gv_derived_features"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    region_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)

    current_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # The real acquisition date behind `current_value`; may be older than
    # reference_date for a composite product, and the gap is the point.
    observation_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    change_7d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_7d_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_30d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_30d_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_yoy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_yoy_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    baseline_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_stddev: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_years: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    seasonal_anomaly: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    seasonal_anomaly_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Only populated when the baseline actually varies and has enough years.
    z_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    percentile: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    trend_direction: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ok")
    calculation_version: Mapped[str] = mapped_column(String(16), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "region_id",
            "metric",
            "dataset",
            "reference_date",
            "calculation_version",
            name="uq_derived_feature_identity",
        ),
        Index("ix_feature_lookup", "metric", "reference_date"),
        Index("ix_feature_region_date", "region_id", "reference_date"),
    )


# ---------------------------------------------------------------------------
# Phases 10, 13 — hazard scores and multi-hazard fusion
# ---------------------------------------------------------------------------


class HazardScore(Base):
    """One hazard's score for one region on one date.

    Covers drought, flood, heat stress, crop stress and the fused multi-hazard
    score — the same shape for each, so the map, the ranking table and the
    alert engine read one table rather than five.

    `contributors` is not optional in spirit: an operational score that cannot
    show its inputs cannot be argued with, and a disaster-management tool that
    cannot be argued with will not be trusted.
    """

    __tablename__ = "gv_hazard_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    region_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hazard: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)

    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    level: Mapped[str] = mapped_column(String(24), nullable=False)
    # 0-1. Falls with thin baselines, stale inputs and missing components, so a
    # score built on little evidence cannot look as solid as one built on much.
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # [{component, value, anomaly, weight, contribution, direction}, ...]
    contributors: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    primary_driver: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Previous level and how long it has held, for hysteresis in the alert
    # engine: a score oscillating across a threshold must not emit an alert
    # every cycle.
    previous_level: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    level_since: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    consecutive_periods: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    data_quality: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ok")
    calculation_version: Mapped[str] = mapped_column(String(16), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "region_id",
            "hazard",
            "reference_date",
            "calculation_version",
            name="uq_hazard_score_identity",
        ),
        Index("ix_hazard_lookup", "hazard", "reference_date"),
        Index("ix_hazard_region", "region_id", "hazard", "reference_date"),
        Index("ix_hazard_level", "hazard", "level", "reference_date"),
    )


# ---------------------------------------------------------------------------
# Phase 18 — alerts
# ---------------------------------------------------------------------------


class Alert(Base):
    """A satellite-derived early-warning indicator.

    Explicitly NOT an official warning; the API and UI restate that on every
    item. This system has no authority to issue one, and an academic prototype
    claiming otherwise would be worse than useless.

    `dedupe_key` is what stops one persistent drought producing a new alert
    every night: an open alert for the same region/hazard/severity is updated
    rather than re-raised.
    """

    __tablename__ = "gv_alerts"

    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    hazard: Mapped[str] = mapped_column(String(32), nullable=False)
    region_id: Mapped[str] = mapped_column(String(64), nullable=False)
    region_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    province: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_severity: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rule_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    evidence: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Identity for deduplication: hazard|region|severity-band.
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    escalation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    first_detected: Mapped[date] = mapped_column(Date, nullable=False)
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    calculation_version: Mapped[str] = mapped_column(String(16), nullable=False)

    # Richer lifecycle: OPEN -> ACKNOWLEDGED -> INVESTIGATING -> CONFIRMED ->
    # RESOLVED, with DISMISSED as a terminal side exit. The earlier
    # active/resolved pair is a strict subset, so existing rows stay valid.
    event_id: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    investigating_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dismissed_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_alert_active", "status", "severity", "reference_date"),
        Index("ix_alert_event", "event_id"),
        Index("ix_alert_region", "region_id", "hazard", "status"),
        # Partial-unique behaviour is enforced in the repository rather than
        # here: Postgres cannot express "unique among open rows" in a plain
        # UniqueConstraint, and a full unique index would block re-raising an
        # alert after a genuine resolve.
        Index("ix_alert_dedupe", "dedupe_key", "status"),
    )


# ---------------------------------------------------------------------------
# Phase 19 — daily intelligence brief
# ---------------------------------------------------------------------------


class DailyBrief(Base):
    """A generated situation summary for one day.

    Stored rather than computed on demand so the brief is a record of what the
    system believed on that date — regenerating it later against updated data
    would quietly rewrite history.
    """

    __tablename__ = "gv_daily_briefs"

    brief_date: Mapped[date] = mapped_column(Date, primary_key=True)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    headline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    active_alerts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_alerts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    critical_regions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deteriorating_regions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    improving_regions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    datasets_healthy: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    datasets_degraded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    top_risk_regions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    changes: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    data_health: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    calculation_version: Mapped[str] = mapped_column(String(16), nullable=False)


# ---------------------------------------------------------------------------
# Phases 15, 16 — model registry and predictions
# ---------------------------------------------------------------------------


class ModelVersion(Base):
    """A trained forecasting model.

    Present so predictions have somewhere to point even before a model exists.
    A prediction whose model cannot be identified is unauditable, and the
    schema should make that impossible rather than rely on discipline.
    """

    __tablename__ = "gv_model_versions"

    model_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(64), nullable=False)

    trained_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    training_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    training_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    training_regions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    features: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    hyperparameters: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    f1_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precision_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recall_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    r2_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    validation_metrics: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    confusion_matrix: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    feature_importance: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Distribution summary of the training features, so drift can be measured
    # against what the model was actually fitted on.
    training_distribution: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="registered")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Prediction(Base):
    """A stored forecast, with the inputs that produced it.

    `feature_snapshot` is what makes a prediction auditable after the fact: a
    forecast that cannot be reproduced from its recorded inputs cannot be
    debugged when it turns out wrong.
    """

    __tablename__ = "gv_predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    region_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(
        String(32), ForeignKey("gv_model_versions.model_version"), nullable=False
    )

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)
    forecast_target_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)

    predicted_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    predicted_class: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    feature_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Filled in once the target date passes, so accuracy can be measured
    # against reality rather than against the validation split.
    actual_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_class: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    evaluated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "region_id",
            "target",
            "forecast_target_date",
            "model_version",
            name="uq_prediction_identity",
        ),
        Index("ix_prediction_lookup", "target", "forecast_target_date"),
        Index("ix_prediction_region", "region_id", "reference_date"),
    )


# ---------------------------------------------------------------------------
# Phases 6, 28 — lineage and audit
# ---------------------------------------------------------------------------


class PipelineStageRun(Base):
    """One stage of one automated cycle.

    The daily cycle is a chain of stages (ingest → quality → baselines →
    features → hazards → alerts → brief). Recording each separately is what
    lets a failed stage be retried without redoing the ones that already
    succeeded, and it is what the pipeline page renders.
    """

    __tablename__ = "gv_pipeline_stage_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(48), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    records_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "stage", name="uq_stage_per_run"),
        Index("ix_stage_run", "run_id"),
        Index("ix_stage_recent", "stage", "started_at"),
    )


class AuditLog(Base):
    """Operator actions and automated state changes worth explaining later."""

    __tablename__ = "gv_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    actor: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    actor_role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, default="success")
    detail: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_audit_recent", "occurred_at"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
    )
