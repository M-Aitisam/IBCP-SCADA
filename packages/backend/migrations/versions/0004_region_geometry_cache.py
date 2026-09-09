"""Persisted region geometry for the GIS map

Additive: creates one table, touches nothing existing.

Exporting simplified district boundaries from Earth Engine measured ~15 seconds
for 119 features. An in-process cache handles that on a long-lived server, but
not on serverless, where every cold instance would pay it again and Vercel's
function timeout is shorter than the export — the map would fail to load rather
than merely be slow.

Boundaries change on the order of years, so the result belongs in the database.
This is a cache of a derived artifact keyed by the ROI configuration that
produced it, not a second region system.

Revision ID: 0004_region_geometry
Revises: 0003_ingestion_locks
Create Date: 2026-08-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_region_geometry"
down_revision = "0003_ingestion_locks"
branch_labels = None
depends_on = None

TABLE = "gee_region_geometry"


def upgrade() -> None:
    op.create_table(
        TABLE,
        # Derived from the ROI asset, province filter and simplify tolerance,
        # so changing any of them yields a different row rather than silently
        # serving boundaries that no longer match the configuration.
        sa.Column("cache_key", sa.String(length=128), primary_key=True),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("region_type", sa.String(length=32), nullable=False),
        sa.Column("simplify_metres", sa.Float(), nullable=False),
        sa.Column("feature_count", sa.Integer(), nullable=False),
        sa.Column("attribution", sa.String(length=255), nullable=True),
        sa.Column("geojson", postgresql.JSONB(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
