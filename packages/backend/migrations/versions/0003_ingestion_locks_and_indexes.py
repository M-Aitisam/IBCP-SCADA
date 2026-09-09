"""Ingestion locks, and indexes for the GeoVision dashboard queries

Two additive changes. Nothing is dropped, renamed or rewritten, so this applies
cleanly to a database that already holds observations.

1. `gee_ingestion_locks` — a lease table giving the pipeline per-dataset mutual
   exclusion (see IngestionLock for why a lease rather than a flag).

2. Two composite indexes serving query shapes the dashboard introduces and the
   existing indexes do not cover:

     - (metric, observation_date)  for the cross-region "latest value of this
       metric everywhere" and trend queries, which filter on metric first and
       never mention dataset.
     - (region_id, metric, observation_date) for the region detail panel and
       per-region time series.

   The existing ix_gee_obs_region_time is (region_id, observation_timestamp),
   which cannot serve a metric-filtered region query without a heap filter over
   every observation that region has ever had.

Deliberately NOT added: an index per metric, or anything on province/district.
Region cardinality is in the hundreds — Postgres will hash-aggregate that fine,
and every extra index is write cost on a 10-year backfill.

Revision ID: 0003_ingestion_locks
Revises: 0002_timestampdb
Create Date: 2026-08-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_ingestion_locks"
down_revision = "0002_timestampdb"
branch_labels = None
depends_on = None

OBSERVATIONS = "gee_satellite_observations"


def upgrade() -> None:
    op.create_table(
        "gee_ingestion_locks",
        # One row per dataset; the dataset name is the lock's identity.
        sa.Column("lock_key", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=True),
        # Host/pid of the holder, for diagnosing a stuck run. Never used to
        # decide ownership — expires_at alone arbitrates that.
        sa.Column("holder", sa.String(length=128), nullable=True),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Reclaim scans ask "which leases have expired", never "which dataset".
    op.create_index("ix_gee_locks_expires", "gee_ingestion_locks", ["expires_at"])

    op.create_index(
        "ix_gee_obs_metric_date",
        OBSERVATIONS,
        ["metric", "observation_date"],
    )
    op.create_index(
        "ix_gee_obs_region_metric_date",
        OBSERVATIONS,
        ["region_id", "metric", "observation_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_gee_obs_region_metric_date", table_name=OBSERVATIONS)
    op.drop_index("ix_gee_obs_metric_date", table_name=OBSERVATIONS)
    op.drop_index("ix_gee_locks_expires", table_name="gee_ingestion_locks")
    op.drop_table("gee_ingestion_locks")
