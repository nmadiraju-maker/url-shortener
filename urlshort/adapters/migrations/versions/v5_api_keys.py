"""Schema version 5: owner and admin API keys (only a hash of each secret is stored)."""
from alembic import op

revision = "v5_api_keys"
down_revision = "v4_click_events"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE api_keys (
            key_id      TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            owner       TEXT,
            role        TEXT NOT NULL,
            secret_hash TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL,
            revoked_at  TIMESTAMPTZ
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE api_keys")
