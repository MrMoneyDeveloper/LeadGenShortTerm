"""Alphabetical delivery layout, durable bucket cursors and final-lead target (additive)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_alphabetical_delivery"
down_revision = "0006_sample_store"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pipeline_state", sa.Column("export_layout", sa.String(16), nullable=True))
    op.add_column("pipeline_state", sa.Column(
        "export_bucket_positions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"))
    op.add_column("pipeline_state", sa.Column("campaign_final_target", sa.Integer(), nullable=True))
    op.add_column("export_batches", sa.Column("layout", sa.String(16), nullable=False, server_default="sequential"))


def downgrade():
    conn = op.get_bind()
    # Never silently discard alphabetical allocation state that already placed rows.
    used = conn.execute(sa.text(
        "SELECT count(*) FROM export_batches WHERE layout = 'alphabetical'")).scalar()
    if used:
        raise RuntimeError("alphabetical deliveries exist; refusing to drop their allocation state")
    op.drop_column("export_batches", "layout")
    op.drop_column("pipeline_state", "campaign_final_target")
    op.drop_column("pipeline_state", "export_bucket_positions")
    op.drop_column("pipeline_state", "export_layout")
