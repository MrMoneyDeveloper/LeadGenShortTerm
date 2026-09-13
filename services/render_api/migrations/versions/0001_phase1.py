"""Immutable Phase-1 PostgreSQL schema. No application-model imports."""

from pathlib import Path

from alembic import op

revision = "0001_phase1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    for table in [
        "failed_jobs",
        "api_usage",
        "pipeline_metrics",
        "validated_leads",
        "email_validation",
        "classification_results",
        "candidates",
        "identity_index",
        "dedupe_index",
        "source_records",
        "processing_jobs",
        "pipeline_state",
        "source_cursors",
    ]:
        op.drop_table(table)
