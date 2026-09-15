"""Persist autonomous campaign lifecycle and seven-day clock."""

import sqlalchemy as sa
from alembic import op

revision = "0003_campaign_lifecycle"
down_revision = "0002_final_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pipeline_state", sa.Column("campaign_id", sa.String(64), nullable=False, server_default=""))
    op.add_column(
        "pipeline_state", sa.Column("campaign_status", sa.String(24), nullable=False, server_default="READY")
    )
    op.add_column("pipeline_state", sa.Column("campaign_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pipeline_state", sa.Column("campaign_deadline", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pipeline_state", sa.Column("campaign_raw_target", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("pipeline_state", sa.Column("campaign_raw_scanned", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("pipeline_state", sa.Column("campaign_last_progress_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pipeline_state", sa.Column("campaign_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_pipeline_state_campaign_status", "pipeline_state", ["campaign_status"])


def downgrade():
    op.drop_index("ix_pipeline_state_campaign_status", table_name="pipeline_state")
    for name in (
        "campaign_completed_at",
        "campaign_last_progress_at",
        "campaign_raw_scanned",
        "campaign_raw_target",
        "campaign_deadline",
        "campaign_started_at",
        "campaign_status",
        "campaign_id",
    ):
        op.drop_column("pipeline_state", name)
