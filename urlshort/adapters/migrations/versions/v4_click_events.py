"""Schema version 4: transactional outbox for click events; clicks.event_id for idempotent apply."""
from alembic import op

revision = "v4_click_events"
down_revision = "v3_max_clicks"


def upgrade() -> None:
    op.execute("ALTER TABLE clicks ADD COLUMN event_id TEXT")
    op.execute("CREATE UNIQUE INDEX ux_clicks_event_id ON clicks(event_id)")
    op.execute("""
        CREATE TABLE click_outbox (
            id           BIGSERIAL PRIMARY KEY,
            event_id     TEXT NOT NULL UNIQUE,
            payload      TEXT NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL,
            published_at TIMESTAMPTZ
        )""")
    op.execute("CREATE INDEX ix_outbox_unpublished ON click_outbox(id) WHERE published_at IS NULL")


def downgrade() -> None:
    op.execute("DROP TABLE click_outbox")
    op.execute("DROP INDEX ux_clicks_event_id")
    op.execute("ALTER TABLE clicks DROP COLUMN event_id")
