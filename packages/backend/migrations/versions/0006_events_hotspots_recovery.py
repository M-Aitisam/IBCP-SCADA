"""Hazard events, hotspots, recovery, field reports, alert lifecycle

Additive. Creates seven tables and adds five nullable columns to gv_alerts for
the richer lifecycle; nothing is dropped or rewritten.

The alert columns are additive rather than a rewrite because the previous
two-state model (active/resolved) is a strict subset of the new six-state one:
existing rows keep working, and `status` simply gains values it did not have
before. A migration that rewrote them would have needed a data backfill for no
behavioural gain.

Revision ID: 0006_events
Revises: 0005_intelligence
Create Date: 2026-08-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_events"
down_revision = "0005_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- richer alert lifecycle -----------------------------------------
    op.add_column("gv_alerts", sa.Column("event_id", sa.String(length=48), nullable=True))
    op.add_column(
        "gv_alerts", sa.Column("investigating_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "gv_alerts", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "gv_alerts", sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("gv_alerts", sa.Column("dismissed_reason", sa.Text(), nullable=True))
    op.create_index("ix_alert_event", "gv_alerts", ["event_id"])

    # ---- hazard events ---------------------------------------------------
    op.create_table(
        "gv_hazard_events",
        sa.Column("event_id", sa.String(length=48), primary_key=True),
        sa.Column("hazard_type", sa.String(length=32), nullable=False),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("region_name", sa.String(length=128), nullable=True),
        sa.Column("province", sa.String(length=128), nullable=True),
        sa.Column("district", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="DETECTED"),
        sa.Column("severity", sa.String(length=24), nullable=False),
        sa.Column("peak_severity", sa.String(length=24), nullable=True),
        sa.Column("current_score", sa.Float(), nullable=True),
        sa.Column("peak_score", sa.Float(), nullable=True),
        sa.Column("onset_score", sa.Float(), nullable=True),
        sa.Column("change_rate", sa.Float(), nullable=True),
        sa.Column("first_detected_at", sa.Date(), nullable=False),
        sa.Column("last_updated_at", sa.Date(), nullable=False),
        sa.Column("peak_at", sa.Date(), nullable=True),
        sa.Column("resolved_at", sa.Date(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_periods", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("affected_area_km2", sa.Float(), nullable=True),
        sa.Column("peak_area_km2", sa.Float(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("source_datasets", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("data_quality", sa.Float(), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(length=24),
            nullable=False,
            server_default="UNVERIFIED",
        ),
        sa.Column("verified_by", sa.String(length=128), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calculation_version", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_event_open", "gv_hazard_events", ["hazard_type", "region_id", "status"])
    op.create_index("ix_event_recent", "gv_hazard_events", ["hazard_type", "last_updated_at"])
    op.create_index("ix_event_region", "gv_hazard_events", ["region_id", "first_detected_at"])
    op.create_index("ix_event_status", "gv_hazard_events", ["status", "severity"])

    # ---- replay timeline -------------------------------------------------
    op.create_table(
        "gv_event_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.String(length=48),
            sa.ForeignKey("gv_hazard_events.event_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("severity", sa.String(length=24), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("affected_area_km2", sa.Float(), nullable=True),
        sa.Column("change_from_previous", sa.Float(), nullable=True),
        sa.Column("data_quality", sa.Float(), nullable=True),
        sa.Column("indicators", postgresql.JSONB(), nullable=True),
        sa.Column("transition", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("event_id", "reference_date", name="uq_event_observation"),
    )
    op.create_index(
        "ix_event_obs_timeline", "gv_event_observations", ["event_id", "reference_date"]
    )

    # ---- hotspots --------------------------------------------------------
    op.create_table(
        "gv_hazard_hotspots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("cluster_id", sa.String(length=64), nullable=False),
        sa.Column("hazard_type", sa.String(length=32), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("region_ids", postgresql.JSONB(), nullable=False),
        sa.Column("cluster_size", sa.Integer(), nullable=False),
        sa.Column("average_severity", sa.Float(), nullable=True),
        sa.Column("maximum_severity", sa.Float(), nullable=True),
        sa.Column("dominant_level", sa.String(length=24), nullable=True),
        sa.Column("provinces", postgresql.JSONB(), nullable=True),
        sa.Column("first_detected", sa.Date(), nullable=False),
        sa.Column("growth_regions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("growth_rate", sa.Float(), nullable=True),
        sa.Column("calculation_version", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "cluster_id", "hazard_type", "reference_date", name="uq_hotspot_cycle"
        ),
    )
    op.create_index("ix_hotspot_lookup", "gv_hazard_hotspots", ["hazard_type", "reference_date"])

    # ---- recovery --------------------------------------------------------
    op.create_table(
        "gv_recovery_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.String(length=48),
            sa.ForeignKey("gv_hazard_events.event_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("baseline_value", sa.Float(), nullable=True),
        sa.Column("pre_event_value", sa.Float(), nullable=True),
        sa.Column("minimum_value", sa.Float(), nullable=True),
        sa.Column("current_value", sa.Float(), nullable=True),
        sa.Column("recovery_pct", sa.Float(), nullable=True),
        sa.Column("days_since_event", sa.Integer(), nullable=True),
        sa.Column("recovery_status", sa.String(length=24), nullable=False),
        sa.Column("trend", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("calculation_version", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "event_id", "region_id", "metric", "reference_date",
            name="uq_recovery_identity",
        ),
    )
    op.create_index("ix_recovery_event", "gv_recovery_metrics", ["event_id", "reference_date"])

    # ---- field reports ---------------------------------------------------
    op.create_table(
        "gv_field_reports",
        sa.Column("report_id", sa.String(length=48), primary_key=True),
        sa.Column("alert_id", sa.String(length=64), nullable=True),
        sa.Column("event_id", sa.String(length=48), nullable=True),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("reported_by", sa.String(length=128), nullable=False),
        sa.Column(
            "reported_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("hazard_type", sa.String(length=32), nullable=True),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=24), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("photo_reference", sa.String(length=512), nullable=True),
        sa.Column(
            "verification_status", sa.String(length=24), nullable=False,
            server_default="PENDING",
        ),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_field_report_alert", "gv_field_reports", ["alert_id"])
    op.create_index("ix_field_report_event", "gv_field_reports", ["event_id"])
    op.create_index("ix_field_report_region", "gv_field_reports", ["region_id", "reported_at"])
    op.create_index("ix_field_report_status", "gv_field_reports", ["verification_status"])

    # ---- alert biography -------------------------------------------------
    op.create_table(
        "gv_alert_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("alert_id", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("from_severity", sa.String(length=16), nullable=True),
        sa.Column("to_severity", sa.String(length=16), nullable=True),
        sa.Column("actor", sa.String(length=128), nullable=False, server_default="system"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_alert_history_alert", "gv_alert_history", ["alert_id", "occurred_at"])

    # ---- derived geometry facts -------------------------------------------
    op.create_table(
        "gv_region_geometry_stats",
        sa.Column("region_id", sa.String(length=64), primary_key=True),
        sa.Column("region_type", sa.String(length=32), nullable=True),
        sa.Column("province", sa.String(length=128), nullable=True),
        sa.Column("district", sa.String(length=128), nullable=True),
        sa.Column("area_km2", sa.Float(), nullable=True),
        sa.Column("centroid_lat", sa.Float(), nullable=True),
        sa.Column("centroid_lon", sa.Float(), nullable=True),
        sa.Column("bbox_min_lat", sa.Float(), nullable=True),
        sa.Column("bbox_min_lon", sa.Float(), nullable=True),
        sa.Column("bbox_max_lat", sa.Float(), nullable=True),
        sa.Column("bbox_max_lon", sa.Float(), nullable=True),
        sa.Column("neighbours", postgresql.JSONB(), nullable=True),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    for table in (
        "gv_region_geometry_stats",
        "gv_alert_history",
        "gv_field_reports",
        "gv_recovery_metrics",
        "gv_hazard_hotspots",
        "gv_event_observations",
        "gv_hazard_events",
    ):
        op.drop_table(table)

    op.drop_index("ix_alert_event", table_name="gv_alerts")
    for column in (
        "dismissed_reason",
        "dismissed_at",
        "confirmed_at",
        "investigating_at",
        "event_id",
    ):
        op.drop_column("gv_alerts", column)
