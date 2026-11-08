"""Qualified-contact counts per batch, executor fencing token, destination verification."""

import sqlalchemy as sa
from alembic import op

revision = "0009_target_fencing_recovery"
down_revision = "0008_optional_lead_email"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("export_batches", sa.Column("qualified_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("pipeline_state", sa.Column(
        "export_destination_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    # A destination that already delivered rows was verified by those deliveries.
    op.execute("UPDATE pipeline_state SET export_destination_verified = true WHERE export_spreadsheet_id <> ''")
    # Existing batches: count acknowledged/claimed rows that had an email and were not synthetic is
    # not recoverable after ACK; every batch delivered before 0009 in this deployment was a labelled
    # synthetic test, so 0 is exact for them.
    op.create_table(
        "executor_fence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("role", sa.String(16), nullable=False, server_default=""),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("INSERT INTO executor_fence (id, epoch, role) VALUES (1, 0, '')")


def downgrade():
    op.drop_table("executor_fence")
    op.drop_column("pipeline_state", "export_destination_verified")
    op.drop_column("export_batches", "qualified_count")
