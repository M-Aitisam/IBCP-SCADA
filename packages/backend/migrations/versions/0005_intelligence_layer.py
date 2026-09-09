"""Intelligence layer: registry, baselines, features, hazards, alerts, briefs

Additive throughout. Creates nine tables and adds four nullable columns to the
existing observations table; nothing is dropped, renamed or rewritten, so this
applies cleanly to a database already holding observations.

The four new observation columns are the Phase 4 data-quality fields. They live
on the observation row rather than in a side table because the relationship is
strictly 1:1 and every query that reads a value also wants to know whether to
trust it — a join for that would be pure overhead on the hottest table in the
schema.

Note on `quality_flag` vs `quality_flags`: the singular column already exists
and carries the ingestion-time marker ("quality_band"). It is left untouched
and the new plural column carries the structured score breakdown. Renaming the
old one would have broken the ingestion writer for no benefit.

Revision ID: 0005_intelligence
Revises: 0004_region_geometry
Create Date: 2026-08-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_intelligence"
down_revision = "0004_region_geometry"
branch_labels = None
depends_on = None

OBSERVATIONS = "gee_satellite_observations"


def upgrade() -> None:
    # ---- Phase 4: per-observation quality --------------------------------
    op.add_column(OBSERVATIONS, sa.Column("quality_score", sa.Float(), nullable=True))
    op.add_column(
        OBSERVATIONS, sa.Column("quality_status", sa.String(length=24), nullable=True)
    )
    op.add_column(
        OBSERVATIONS, sa.Column("quality_flags", postgresql.JSONB(), nullable=True)
    )
    op.add_column(OBSERVATIONS, sa.Column("quality_reason", sa.Text(), nullable=True))
    # Lets the dashboard filter to trustworthy observations without a heap scan.
    op.create_index(
        "ix_gee_obs_quality", OBSERVATIONS, ["dataset", "quality_status"]
    )

    # ---- Phase 1: dataset registry ---------------------------------------
    op.create_table(
        "gv_dataset_registry",
        sa.Column("dataset_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("gee_collection", sa.String(length=160), nullable=False),
        sa.Column("satellite", sa.String(length=128), nullable=True),
        sa.Column("sensor", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("spatial_resolution_m", sa.Float(), nullable=True),
        sa.Column("temporal_resolution", sa.String(length=32), nullable=True),
        sa.Column("expected_update_days", sa.Integer(), nullable=True),
        sa.Column("available_from", sa.Date(), nullable=True),
        sa.Column("earliest_observation", sa.Date(), nullable=True),
        sa.Column("latest_available_date", sa.Date(), nullable=True),
        sa.Column("latest_observation", sa.Date(), nullable=True),
        sa.Column("last_attempted_ingestion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_ingestion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("freshness_state", sa.String(length=24), nullable=True),
        sa.Column("lag_days", sa.Integer(), nullable=True),
        sa.Column("processing_method", sa.Text(), nullable=True),
        sa.Column("cloud_filter", sa.String(length=128), nullable=True),
        sa.Column("bands", postgresql.JSONB(), nullable=True),
        sa.Column("derived_indices", postgresql.JSONB(), nullable=True),
        sa.Column("region_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("record_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("coverage_pct", sa.Float(), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("registry_version", sa.String(length=32), nullable=True),
        sa.Column("extra_metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ---- Phase 9: baselines ----------------------------------------------
    op.create_table(
        "gv_metric_baselines",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("period_type", sa.String(length=16), nullable=False),
        sa.Column("period_key", sa.Integer(), nullable=False),
        sa.Column("mean_value", sa.Float(), nullable=True),
        sa.Column("median_value", sa.Float(), nullable=True),
        sa.Column("stddev_value", sa.Float(), nullable=True),
        sa.Column("min_value", sa.Float(), nullable=True),
        sa.Column("max_value", sa.Float(), nullable=True),
        sa.Column("p10_value", sa.Float(), nullable=True),
        sa.Column("p25_value", sa.Float(), nullable=True),
        sa.Column("p75_value", sa.Float(), nullable=True),
        sa.Column("p90_value", sa.Float(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sample_years", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_year", sa.Integer(), nullable=True),
        sa.Column("last_year", sa.Integer(), nullable=True),
        sa.Column("is_sufficient", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("calculation_version", sa.String(length=16), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "region_id",
            "metric",
            "dataset",
            "period_type",
            "period_key",
            "calculation_version",
            name="uq_baseline_identity",
        ),
    )
    op.create_index(
        "ix_baseline_lookup", "gv_metric_baselines", ["metric", "period_type", "period_key"]
    )
    op.create_index("ix_baseline_region", "gv_metric_baselines", ["region_id", "metric"])

    # ---- Phase 8: derived features ---------------------------------------
    op.create_table(
        "gv_derived_features",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("current_value", sa.Float(), nullable=True),
        sa.Column("observation_date", sa.Date(), nullable=True),
        sa.Column("change_7d", sa.Float(), nullable=True),
        sa.Column("change_7d_pct", sa.Float(), nullable=True),
        sa.Column("change_30d", sa.Float(), nullable=True),
        sa.Column("change_30d_pct", sa.Float(), nullable=True),
        sa.Column("change_yoy", sa.Float(), nullable=True),
        sa.Column("change_yoy_pct", sa.Float(), nullable=True),
        sa.Column("baseline_value", sa.Float(), nullable=True),
        sa.Column("baseline_stddev", sa.Float(), nullable=True),
        sa.Column("baseline_years", sa.Integer(), nullable=True),
        sa.Column("seasonal_anomaly", sa.Float(), nullable=True),
        sa.Column("seasonal_anomaly_pct", sa.Float(), nullable=True),
        sa.Column("z_score", sa.Float(), nullable=True),
        sa.Column("percentile", sa.Float(), nullable=True),
        sa.Column("trend_direction", sa.String(length=16), nullable=True),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="ok"),
        sa.Column("calculation_version", sa.String(length=16), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "region_id",
            "metric",
            "dataset",
            "reference_date",
            "calculation_version",
            name="uq_derived_feature_identity",
        ),
    )
    op.create_index("ix_feature_lookup", "gv_derived_features", ["metric", "reference_date"])
    op.create_index(
        "ix_feature_region_date", "gv_derived_features", ["region_id", "reference_date"]
    )

    # ---- Phases 10/13: hazard scores -------------------------------------
    op.create_table(
        "gv_hazard_scores",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("hazard", sa.String(length=32), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("level", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("contributors", postgresql.JSONB(), nullable=True),
        sa.Column("primary_driver", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("previous_level", sa.String(length=24), nullable=True),
        sa.Column("level_since", sa.Date(), nullable=True),
        sa.Column("consecutive_periods", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("data_quality", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="ok"),
        sa.Column("calculation_version", sa.String(length=16), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "region_id",
            "hazard",
            "reference_date",
            "calculation_version",
            name="uq_hazard_score_identity",
        ),
    )
    op.create_index("ix_hazard_lookup", "gv_hazard_scores", ["hazard", "reference_date"])
    op.create_index(
        "ix_hazard_region", "gv_hazard_scores", ["region_id", "hazard", "reference_date"]
    )
    op.create_index(
        "ix_hazard_level", "gv_hazard_scores", ["hazard", "level", "reference_date"]
    )

    # ---- Phase 18: alerts -------------------------------------------------
    op.create_table(
        "gv_alerts",
        sa.Column("alert_id", sa.String(length=64), primary_key=True),
        sa.Column("hazard", sa.String(length=32), nullable=False),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("region_name", sa.String(length=128), nullable=True),
        sa.Column("province", sa.String(length=128), nullable=True),
        sa.Column("district", sa.String(length=128), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("previous_severity", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("rule_id", sa.String(length=64), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=160), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("escalation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_detected", sa.Date(), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=128), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calculation_version", sa.String(length=16), nullable=False),
    )
    op.create_index("ix_alert_active", "gv_alerts", ["status", "severity", "reference_date"])
    op.create_index("ix_alert_region", "gv_alerts", ["region_id", "hazard", "status"])
    op.create_index("ix_alert_dedupe", "gv_alerts", ["dedupe_key", "status"])

    # ---- Phase 19: daily briefs ------------------------------------------
    op.create_table(
        "gv_daily_briefs",
        sa.Column("brief_date", sa.Date(), primary_key=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("headline", sa.Text(), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=True),
        sa.Column("active_alerts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_alerts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("critical_regions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deteriorating_regions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("improving_regions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("datasets_healthy", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("datasets_degraded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_risk_regions", postgresql.JSONB(), nullable=True),
        sa.Column("changes", postgresql.JSONB(), nullable=True),
        sa.Column("data_health", postgresql.JSONB(), nullable=True),
        sa.Column("calculation_version", sa.String(length=16), nullable=False),
    )

    # ---- Phases 15/16: models and predictions ----------------------------
    op.create_table(
        "gv_model_versions",
        sa.Column("model_version", sa.String(length=32), primary_key=True),
        sa.Column("model_type", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("training_start", sa.Date(), nullable=True),
        sa.Column("training_end", sa.Date(), nullable=True),
        sa.Column("training_regions", postgresql.JSONB(), nullable=True),
        sa.Column("features", postgresql.JSONB(), nullable=True),
        sa.Column("hyperparameters", postgresql.JSONB(), nullable=True),
        sa.Column("f1_score", sa.Float(), nullable=True),
        sa.Column("precision_score", sa.Float(), nullable=True),
        sa.Column("recall_score", sa.Float(), nullable=True),
        sa.Column("r2_score", sa.Float(), nullable=True),
        sa.Column("validation_metrics", postgresql.JSONB(), nullable=True),
        sa.Column("confusion_matrix", postgresql.JSONB(), nullable=True),
        sa.Column("feature_importance", postgresql.JSONB(), nullable=True),
        sa.Column("training_distribution", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="registered"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "gv_predictions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column(
            "model_version",
            sa.String(length=32),
            sa.ForeignKey("gv_model_versions.model_version"),
            nullable=False,
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("forecast_target_date", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("predicted_value", sa.Float(), nullable=True),
        sa.Column("predicted_class", sa.String(length=32), nullable=True),
        sa.Column("probability", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("feature_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("actual_value", sa.Float(), nullable=True),
        sa.Column("actual_class", sa.String(length=32), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "region_id",
            "target",
            "forecast_target_date",
            "model_version",
            name="uq_prediction_identity",
        ),
    )
    op.create_index(
        "ix_prediction_lookup", "gv_predictions", ["target", "forecast_target_date"]
    )
    op.create_index("ix_prediction_region", "gv_predictions", ["region_id", "reference_date"])

    # ---- Phases 6/23/28: pipeline stages and audit -----------------------
    op.create_table(
        "gv_pipeline_stage_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=48), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("records_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("run_id", "stage", name="uq_stage_per_run"),
    )
    op.create_index("ix_stage_run", "gv_pipeline_stage_runs", ["run_id"])
    op.create_index("ix_stage_recent", "gv_pipeline_stage_runs", ["stage", "started_at"])

    op.create_table(
        "gv_audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("actor_role", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False, server_default="success"),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_audit_recent", "gv_audit_logs", ["occurred_at"])
    op.create_index("ix_audit_entity", "gv_audit_logs", ["entity_type", "entity_id"])


def downgrade() -> None:
    for table in (
        "gv_audit_logs",
        "gv_pipeline_stage_runs",
        "gv_predictions",
        "gv_model_versions",
        "gv_daily_briefs",
        "gv_alerts",
        "gv_hazard_scores",
        "gv_derived_features",
        "gv_metric_baselines",
        "gv_dataset_registry",
    ):
        op.drop_table(table)

    op.drop_index("ix_gee_obs_quality", table_name=OBSERVATIONS)
    for column in ("quality_reason", "quality_flags", "quality_status", "quality_score"):
        op.drop_column(OBSERVATIONS, column)
