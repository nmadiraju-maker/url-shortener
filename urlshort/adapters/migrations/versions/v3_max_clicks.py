"""Schema version 3: per-link click caps (additive, nullable: expand-only)."""
from alembic import op

revision = "v3_max_clicks"
down_revision = "v2_baseline"


def upgrade() -> None:
    op.execute("ALTER TABLE links ADD COLUMN max_clicks INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE links DROP COLUMN max_clicks")
