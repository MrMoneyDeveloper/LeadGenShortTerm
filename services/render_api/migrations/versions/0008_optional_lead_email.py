"""Allow final leads without an email (operator policy require_contact: false)."""

import sqlalchemy as sa
from alembic import op

revision = "0008_optional_lead_email"
down_revision = "0007_alphabetical_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("validated_leads", "email", existing_type=sa.String(320), nullable=True)
    op.alter_column("validated_leads", "email_hash", existing_type=sa.String(64), nullable=True)


def downgrade():
    pending = op.get_bind().execute(sa.text("SELECT count(*) FROM validated_leads WHERE email IS NULL")).scalar()
    if pending:
        raise RuntimeError("pending leads without email exist; deliver them before downgrading")
    op.alter_column("validated_leads", "email_hash", existing_type=sa.String(64), nullable=False)
    op.alter_column("validated_leads", "email", existing_type=sa.String(320), nullable=False)
