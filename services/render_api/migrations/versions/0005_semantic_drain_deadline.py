"""Bound campaign semantic drain time without discarding pending deliveries."""

import sqlalchemy as sa
from alembic import op

revision = "0005_semantic_drain_deadline"
down_revision = "0004_provider_rate_state"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pipeline_state", sa.Column("campaign_semantic_deadline", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("pipeline_state", "campaign_semantic_deadline")
