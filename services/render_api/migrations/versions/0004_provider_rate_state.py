"""Persist provider windows and cooldown independently of daily usage rows."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_provider_rate_state"
down_revision = "0003_campaign_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("provider_rate_state",
                    sa.Column("provider", sa.String(32), primary_key=True),
                    sa.Column("state", JSONB(), nullable=False))


def downgrade():
    op.drop_table("provider_rate_state")
