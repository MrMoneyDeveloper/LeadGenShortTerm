"""Bounded per-source sample store for testing/training (operator-authorized, provenance kept)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_sample_store"
down_revision = "0005_semantic_drain_deadline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sample_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_record_id", sa.Text(), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "source_record_id"),
    )
    op.create_index("ix_sample_records_source", "sample_records", ["source"])
    op.create_index("ix_sample_records_candidate_id", "sample_records", ["candidate_id"])


def downgrade():
    op.drop_index("ix_sample_records_candidate_id", table_name="sample_records")
    op.drop_index("ix_sample_records_source", table_name="sample_records")
    op.drop_table("sample_records")
