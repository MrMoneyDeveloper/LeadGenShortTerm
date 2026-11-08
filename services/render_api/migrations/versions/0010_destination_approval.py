"""Bind destination approval to the exact (spreadsheet, folder, layout, shard size) tuple."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_destination_approval"
down_revision = "0009_target_fencing_recovery"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pipeline_state", sa.Column(
        "export_destination_approval", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade():
    op.drop_column("pipeline_state", "export_destination_approval")
