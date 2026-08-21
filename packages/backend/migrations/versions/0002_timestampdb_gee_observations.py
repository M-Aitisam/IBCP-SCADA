"""timestampdb: GEE satellite observations, ingestion runs and checkpoints

Creates the time-series store for the Earth Engine acquisition pipeline.

`gee_satellite_observations` becomes a TimescaleDB hypertable partitioned on
observation_timestamp where the extension is available. Managed Postgres
offerings differ here — Neon, Supabase and Vercel Postgres do not ship
timescaledb by default — so the migration probes for it and falls back to a
plain indexed table. Application behaviour is identical either way; only query
performance at scale differs. The fallback is logged, not silent.

Revision ID: 0002_timestampdb
Revises: 0001_initial
Create Date: 2026-08-21
"""
import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_timestampdb"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

OBSERVATIONS = "gee_satellite_observations"

# Timescale chunks roughly one month of observations at a time; small enough to
# prune efficiently, large enough to avoid chunk sprawl over a 10-year backfill.
CHUNK_INTERVAL = "30 days"


def _try_enable_timescale(connection) -> bool:
    """Enable timescaledb if the server offers it. Never fails the migration."""
    available = connection.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    if not available:
        logger.warning(
            "timescaledb extension is not available on this server; "
            "%s will be created as a plain indexed table", OBSERVATIONS
        )
        return False
    try:
        connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        return True
    except Exception as exc:  # noqa: BLE001
        # Typically insufficient privilege on managed Postgres.
        logger.warning(
            "could not enable timescaledb (%s); falling back to a plain table", exc
        )
        return False


def upgrade() -> None:
    connection = op.get_bind()

    op.create_table(
        OBSERVATIONS,
        # Partition key first; it is part of the primary key because Timescale
        # requires every unique index on a hypertable to include it.
        sa.Column("observation_timestamp", sa.DateTime(timezone=True), nullable=False),
        # sha256 of dataset|source_image_id|region_id|metric — the idempotency key.
        sa.Column("observation_key", sa.String(length=64), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("dataset_asset_id", sa.String(length=160), nullable=False),
        sa.Column("dataset_version", sa.String(length=32), nullable=True),
        sa.Column("region_type", sa.String(length=32), nullable=False),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("province", sa.String(length=128), nullable=True),
        sa.Column("district", sa.String(length=128), nullable=True),
        sa.Column("tehsil", sa.String(length=128), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("band", sa.String(length=64), nullable=True),
        sa.Column("metric", sa.String(length=64), nullable=False),
        # Non-null only for computed products (NDVI); keeps raw measurements
        # and derived features distinguishable.
        sa.Column("derived_metric", sa.String(length=64), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("scale_factor", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("min_value", sa.Float(), nullable=True),
        sa.Column("max_value", sa.Float(), nullable=True),
        sa.Column("mean_value", sa.Float(), nullable=True),
        sa.Column("median_value", sa.Float(), nullable=True),
        sa.Column("pixel_count", sa.Integer(), nullable=True),
        sa.Column("quality_flag", sa.String(length=64), nullable=True),
        sa.Column("cloud_percentage", sa.Float(), nullable=True),
        sa.Column("source_image_id", sa.String(length=255), nullable=False),
        sa.Column("source_product_id", sa.String(length=255), nullable=True),
        sa.Column("spatial_resolution", sa.Float(), nullable=True),
        sa.Column("acquisition_metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "processing_status",
            sa.String(length=32),
            nullable=False,
            server_default="stored",
        ),
        sa.PrimaryKeyConstraint(
            "observation_timestamp", "observation_key", name="pk_gee_observations"
        ),
    )

    op.create_index("ix_gee_obs_dataset_time", OBSERVATIONS, ["dataset", "observation_timestamp"])
    op.create_index("ix_gee_obs_region_time", OBSERVATIONS, ["region_id", "observation_timestamp"])
    op.create_index(
        "ix_gee_obs_dataset_metric_time",
        OBSERVATIONS,
        ["dataset", "metric", "observation_timestamp"],
    )
    op.create_index("ix_gee_obs_source_image", OBSERVATIONS, ["dataset", "source_image_id"])
    # Lookups by hash alone during the pre-upsert existence check.
    op.create_index("ix_gee_obs_key", OBSERVATIONS, ["observation_key"])

    if _try_enable_timescale(connection):
        connection.execute(
            sa.text(
                "SELECT create_hypertable(:table, 'observation_timestamp', "
                "chunk_time_interval => INTERVAL :interval, "
                "migrate_data => TRUE, if_not_exists => TRUE)"
            ).bindparams(table=OBSERVATIONS, interval=CHUNK_INTERVAL)
        )
        logger.info("%s created as a TimescaleDB hypertable", OBSERVATIONS)

    op.create_table(
        "gee_ingestion_runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("datasets_attempted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("datasets_succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("datasets_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_inserted", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("records_updated", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("records_skipped", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("records_rejected", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("dataset_stats", postgresql.JSONB(), nullable=True),
        sa.Column("latest_observation_per_dataset", postgresql.JSONB(), nullable=True),
        sa.Column("errors", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_gee_runs_started", "gee_ingestion_runs", ["started_at"])

    op.create_table(
        "gee_ingestion_checkpoints",
        sa.Column("dataset", sa.String(length=64), primary_key=True),
        sa.Column("last_successful_observation_date", sa.Date(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        # What we asked GEE for vs what GEE actually holds. Kept apart so a
        # publication lag is never mistaken for ingested data.
        sa.Column("requested_until", sa.Date(), nullable=True),
        sa.Column("latest_available_at_source", sa.Date(), nullable=True),
        sa.Column("availability_status", sa.String(length=48), nullable=True),
        sa.Column("backfill_cursor", sa.Date(), nullable=True),
        sa.Column("backfill_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("gee_ingestion_checkpoints")
    op.drop_index("ix_gee_runs_started", table_name="gee_ingestion_runs")
    op.drop_table("gee_ingestion_runs")
    # Dropping a hypertable drops its chunks; no special handling needed.
    for index in (
        "ix_gee_obs_key",
        "ix_gee_obs_source_image",
        "ix_gee_obs_dataset_metric_time",
        "ix_gee_obs_region_time",
        "ix_gee_obs_dataset_time",
    ):
        op.drop_index(index, table_name=OBSERVATIONS)
    op.drop_table(OBSERVATIONS)
    # The timescaledb extension is intentionally left enabled: other objects
    # may depend on it, and dropping an extension is not this migration's call.
