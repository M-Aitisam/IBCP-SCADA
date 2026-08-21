# packages/backend/app/databases/timestampdb/models.py
"""Time-series storage models for acquired satellite observations.

These tables live in the same Postgres database as the rest of the app and are
registered on the existing `app.db.database.Base`, so Alembic picks them up
without a second metadata registry. `gee_satellite_observations` is created as
a TimescaleDB hypertable partitioned on `observation_timestamp` where the
extension is available (see migration 0002); it degrades to a plain indexed
table otherwise, with identical application semantics.
"""
import hashlib
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_observation_key(
    dataset: str, source_image_id: str, region_id: str, metric: str
) -> str:
    """Deterministic uniqueness key for an observation.

    TimescaleDB requires every unique index on a hypertable to include the
    partitioning column, so the on-disk key is (observation_timestamp,
    observation_key). Hashing the natural key keeps that index narrow and
    fixed-width regardless of how long a source image id is.
    """
    raw = "|".join((dataset, source_image_id, region_id, metric))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SatelliteObservation(Base):
    """One reduced measurement: one metric, one region, one source image."""

    __tablename__ = "gee_satellite_observations"

    # Composite PK == the idempotency key. An upsert on these two columns is
    # what makes re-running the pipeline safe.
    observation_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    observation_key: Mapped[str] = mapped_column(String(64), primary_key=True)

    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Distinct from observation_timestamp on purpose: satellite acquisition
    # time and database write time are different events.
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_asset_id: Mapped[str] = mapped_column(String(160), nullable=False)
    dataset_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    region_type: Mapped[str] = mapped_column(String(32), nullable=False)
    region_id: Mapped[str] = mapped_column(String(64), nullable=False)
    province: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    tehsil: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # `band` is the raw GEE band this came from (null for purely derived
    # metrics); `derived_metric` is non-null only for computed products such
    # as NDVI. Raw and derived measurements are never conflated.
    band: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    derived_metric: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    scale_factor: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    min_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mean_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    median_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pixel_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    quality_flag: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    cloud_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    source_image_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_product_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    spatial_resolution: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Free-form per-dataset acquisition metadata (S1 orbit/polarisation/mode,
    # MODIS QA words, ...). Kept out of columns so adding a dataset does not
    # require a migration.
    acquisition_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="stored"
    )

    __table_args__ = (
        Index("ix_gee_obs_dataset_time", "dataset", "observation_timestamp"),
        Index("ix_gee_obs_region_time", "region_id", "observation_timestamp"),
        Index(
            "ix_gee_obs_dataset_metric_time",
            "dataset",
            "metric",
            "observation_timestamp",
        ),
        Index("ix_gee_obs_source_image", "dataset", "source_image_id"),
    )


class IngestionRun(Base):
    """One execution of the daily or backfill pipeline."""

    __tablename__ = "gee_ingestion_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")

    datasets_attempted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    datasets_succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    datasets_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    records_inserted: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    records_updated: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    records_skipped: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    records_rejected: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    dataset_stats: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    latest_observation_per_dataset: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )
    errors: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)


class IngestionCheckpoint(Base):
    """Per-dataset resume point, so a run never reprocesses the full history."""

    __tablename__ = "gee_ingestion_checkpoints"

    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)

    # Watermark: the newest observation date successfully stored so far.
    last_successful_observation_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Source-availability transparency. `requested_until` is what we asked for;
    # `latest_available_at_source` is what GEE actually holds.
    requested_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    latest_available_at_source: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )
    availability_status: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)

    # Backfill resume cursor: the chunk start that has not yet completed.
    backfill_cursor: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    backfill_complete: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
