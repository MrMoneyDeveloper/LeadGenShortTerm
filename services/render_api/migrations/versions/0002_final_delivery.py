"""Transient processing, source profiles and acknowledged final Google delivery."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_final_delivery"
down_revision = "0001_phase1"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("draining", "storage_pressure"):
        op.add_column("pipeline_state", sa.Column(name, sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("pipeline_state", sa.Column("export_next_position", sa.BigInteger(), nullable=False, server_default="0"))
    for name in ("export_spreadsheet_id", "export_folder_id"):
        op.add_column("pipeline_state", sa.Column(name, sa.String(160), nullable=False, server_default=""))
    op.add_column("pipeline_state", sa.Column("export_shard_rows", sa.Integer(), nullable=False, server_default="5000"))
    for table in ("source_records", "candidates", "validated_leads"):
        op.add_column(table, sa.Column("profile", postgresql.JSONB(), nullable=False, server_default="{}"))
    for table in ("candidates", "validated_leads"):
        op.add_column(table, sa.Column("rank_score", sa.Float(), nullable=False, server_default="0"))
    op.add_column("validated_leads", sa.Column("candidate_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_leads_candidate", "validated_leads", "candidates", ["candidate_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_validated_leads_candidate_id", "validated_leads", ["candidate_id"])
    op.create_index("ix_candidates_priority", "candidates", ["status", sa.text("rank_score DESC"), "available_at"])
    op.create_table(
        "export_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("spreadsheet_id", sa.String(160), nullable=False),
        sa.Column("drive_folder_id", sa.String(160), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("first_position", sa.BigInteger(), nullable=False),
        sa.Column("shard_rows", sa.Integer(), nullable=False),
        sa.Column("drive_file_id", sa.String(160), nullable=True),
        sa.Column("ack_digest", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_export_batches_status", "export_batches", ["status"])
    op.create_index("ix_export_batches_created_at", "export_batches", ["created_at"])
    # Existing final leads remain intact. Link their temporary candidates where possible.
    op.execute("UPDATE validated_leads AS l SET candidate_id=c.id FROM candidates AS c "
               "WHERE c.identity_hash=l.identity_hash AND c.status='VALIDATED'")
    # Old uncertain candidates must pass the newly earlier contact gate before inference.
    op.execute("UPDATE candidates SET status='VALIDATE' WHERE status='CLASSIFY'")


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM export_batches")).scalar():
        raise RuntimeError("Cannot downgrade while delivery receipts exist; preserve final-output state")
    op.drop_table("export_batches")
    op.drop_index("ix_candidates_priority", table_name="candidates")
    op.drop_index("ix_validated_leads_candidate_id", table_name="validated_leads")
    op.drop_constraint("fk_leads_candidate", "validated_leads", type_="foreignkey")
    op.drop_column("validated_leads", "candidate_id")
    for table in ("candidates", "validated_leads"):
        op.drop_column(table, "rank_score")
    for table in ("source_records", "candidates", "validated_leads"):
        op.drop_column(table, "profile")
    for name in ("export_shard_rows", "export_folder_id", "export_spreadsheet_id", "export_next_position", "storage_pressure", "draining"):
        op.drop_column("pipeline_state", name)
