"""Add the durable automated GEE backfill queue."""
import sqlalchemy as sa
from alembic import op

revision = "0007_gee_backfill"
down_revision = "0006_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gee_backfill_progress",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("chunk_start", sa.Date(), nullable=False),
        sa.Column("chunk_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("records_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint("dataset", "chunk_start", name="uq_gee_backfill_dataset_chunk"),
    )
    op.create_index(
        "ix_gee_backfill_status_start",
        "gee_backfill_progress",
        ["status", "chunk_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_gee_backfill_status_start", table_name="gee_backfill_progress")
    op.drop_table("gee_backfill_progress")