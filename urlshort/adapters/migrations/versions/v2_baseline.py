"""Baseline: the schema as of SQLite schema version 2 (no Postgres deployments existed before it).

Revision IDs are named after the schema version they produce, so a test can check that Postgres and
SQLite are always at the same version.
"""
from alembic import op

revision = "v2_baseline"
down_revision = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE links (
            code             TEXT PRIMARY KEY,
            target_url       TEXT NOT NULL,
            owner            TEXT NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL,
            expires_at       TIMESTAMPTZ,
            is_active        BOOLEAN NOT NULL DEFAULT TRUE,
            click_count      BIGINT NOT NULL DEFAULT 0,
            stats_token_hash TEXT
        )""")
    op.execute("CREATE INDEX ix_links_owner_target ON links(owner, target_url)")
    op.execute("""
        CREATE TABLE clicks (
            id            BIGSERIAL PRIMARY KEY,
            code          TEXT NOT NULL REFERENCES links(code),
            ts            TIMESTAMPTZ NOT NULL,
            referrer_host TEXT,
            agent_family  TEXT NOT NULL,
            is_bot        BOOLEAN NOT NULL,
            ip_id         BIGINT,
            ip_key_id     TEXT
        )""")
    op.execute("CREATE INDEX ix_clicks_code_ts ON clicks(code, ts)")
    op.execute("""
        CREATE TABLE audit_log (
            id        BIGSERIAL PRIMARY KEY,
            ts        TEXT NOT NULL,
            actor     TEXT NOT NULL,
            action    TEXT NOT NULL,
            target    TEXT NOT NULL,
            details   TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            hash      TEXT NOT NULL
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE audit_log")
    op.execute("DROP TABLE clicks")
    op.execute("DROP TABLE links")
