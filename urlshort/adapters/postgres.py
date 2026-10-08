"""PostgreSQL implementation of the Repository port (psycopg 3 with a connection pool).

Same guarantees as the SQLite adapter, using Postgres' own mechanisms:
  * click caps: ONE conditional UPDATE. Concurrent updates of a row are serialised by its row lock and
    the WHERE clause is re-checked after waiting, so the cap can never be overshot.
  * audit chain: read-last-hash-and-append under a transaction-scoped advisory lock, so concurrent
    writers (any instance) cannot fork the chain.
  * only a duplicate short code (the links primary key) becomes AliasConflict.
Schema changes are Alembic revisions (urlshort/adapters/migrations), applied on start by default.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..errors import AliasConflict
from ..events import encode_click
from ..storage import ApiKey, AuditRecord, ChainFn, Click, Link

AUDIT_LOCK_KEY = 0x75726C73   # advisory lock id for audit appends ("urls")
MIGRATIONS = Path(__file__).with_name("migrations")


def alembic_config(url: str) -> Any:
    """Alembic configuration for this package's migrations (also usable from the alembic CLI API)."""
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["url"] = url
    return cfg


def migrate(url: str) -> str:
    """Upgrade the database to the latest schema; returns the head revision."""
    from alembic import command
    from alembic.script import ScriptDirectory

    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    return str(ScriptDirectory.from_config(cfg).get_current_head())


class PostgresRepository:
    def __init__(self, url: str, *, min_size: int = 1, max_size: int = 10, auto_migrate: bool = True) -> None:
        if auto_migrate:
            migrate(url)
        self._pool: ConnectionPool[psycopg.Connection[dict[str, Any]]] = ConnectionPool(
            url, min_size=min_size, max_size=max_size, open=True,
            kwargs={"row_factory": dict_row}, connection_class=psycopg.Connection)

    def close(self) -> None:
        self._pool.close()

    @staticmethod
    def _to_link(row: dict[str, Any]) -> Link:
        return Link(code=row["code"], target_url=row["target_url"], owner=row["owner"], created_at=row["created_at"],
                    expires_at=row["expires_at"], is_active=row["is_active"], click_count=row["click_count"],
                    stats_token_hash=row["stats_token_hash"], max_clicks=row["max_clicks"])

    # ---------------------------------------------------------------- links
    def insert_link(self, link: Link) -> None:
        try:
            with self._pool.connection() as conn:
                conn.execute(
                    "INSERT INTO links(code, target_url, owner, created_at, expires_at, is_active, click_count,"
                    " stats_token_hash, max_clicks) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (link.code, link.target_url, link.owner, link.created_at, link.expires_at, link.is_active,
                     link.click_count, link.stats_token_hash, link.max_clicks))
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "links_pkey":
                raise AliasConflict(f"code '{link.code}' is already taken") from exc
            raise

    def get_link(self, code: str) -> Link | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM links WHERE code = %s", (code,)).fetchone()
        return self._to_link(row) if row else None

    def find_reusable_link(self, owner: str, target_url: str) -> Link | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM links WHERE owner = %s AND target_url = %s AND is_active AND expires_at IS NULL"
                " AND max_clicks IS NULL ORDER BY created_at DESC LIMIT 1", (owner, target_url)).fetchone()
        return self._to_link(row) if row else None

    def deactivate(self, code: str) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute("UPDATE links SET is_active = FALSE WHERE code = %s AND is_active", (code,))
        return cur.rowcount == 1

    def all_codes(self) -> list[str]:
        with self._pool.connection() as conn:
            return [r["code"] for r in conn.execute("SELECT code FROM links").fetchall()]

    # ---------------------------------------------------------------- API keys
    @staticmethod
    def _to_key(r: dict[str, Any]) -> ApiKey:
        return ApiKey(key_id=r["key_id"], name=r["name"], owner=r["owner"], role=r["role"],
                      secret_hash=r["secret_hash"], created_at=r["created_at"], revoked_at=r["revoked_at"])

    def insert_api_key(self, key: ApiKey) -> None:
        with self._pool.connection() as conn:
            conn.execute("INSERT INTO api_keys(key_id, name, owner, role, secret_hash, created_at)"
                         " VALUES (%s, %s, %s, %s, %s, %s)",
                         (key.key_id, key.name, key.owner, key.role, key.secret_hash, key.created_at))

    def get_api_key(self, key_id: str) -> ApiKey | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE key_id = %s", (key_id,)).fetchone()
        return self._to_key(row) if row else None

    def list_api_keys(self) -> list[ApiKey]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at, key_id").fetchall()
        return [self._to_key(r) for r in rows]

    def revoke_api_key(self, key_id: str, at: datetime) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute("UPDATE api_keys SET revoked_at = %s WHERE key_id = %s AND revoked_at IS NULL",
                               (at, key_id))
        return cur.rowcount == 1

    # ---------------------------------------------------------------- clicks
    @staticmethod
    def _store_click(conn: psycopg.Connection[dict[str, Any]], click: Click, as_event: bool) -> None:
        """In events mode the click goes to the outbox in the SAME transaction as the counter update."""
        if as_event:
            conn.execute("INSERT INTO click_outbox(event_id, payload, created_at) VALUES (%s, %s, %s)",
                         (click.event_id, encode_click(click), click.ts))
            return
        conn.execute(
            "INSERT INTO clicks(code, ts, referrer_host, agent_family, is_bot, ip_id, ip_key_id, event_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (click.code, click.ts, click.referrer_host, click.agent_family, click.is_bot, click.ip_id,
             click.ip_key_id, click.event_id))

    def record_click(self, click: Click, *, as_event: bool = False) -> None:
        with self._pool.connection() as conn, conn.transaction():
            self._store_click(conn, click, as_event)
            if not click.is_bot:
                conn.execute("UPDATE links SET click_count = click_count + 1 WHERE code = %s", (click.code,))

    def record_click_with_limit(self, click: Click, *, as_event: bool = False) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            if click.is_bot:
                allowed = conn.execute(
                    "SELECT 1 FROM links WHERE code = %s AND (max_clicks IS NULL OR click_count < max_clicks)",
                    (click.code,)).fetchone() is not None
            else:
                allowed = conn.execute(
                    "UPDATE links SET click_count = click_count + 1"
                    " WHERE code = %s AND (max_clicks IS NULL OR click_count < max_clicks)",
                    (click.code,)).rowcount == 1
            if allowed:
                self._store_click(conn, click, as_event)
            return allowed

    # ---------------------------------------------------------------- outbox (events mode)
    def outbox_pending(self, limit: int) -> list[tuple[int, str]]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT id, payload FROM click_outbox WHERE published_at IS NULL ORDER BY id"
                                " LIMIT %s", (limit,)).fetchall()
        return [(r["id"], r["payload"]) for r in rows]

    def mark_published(self, ids: list[int], at: datetime) -> None:
        with self._pool.connection() as conn:
            conn.execute("UPDATE click_outbox SET published_at = %s WHERE id = ANY(%s)", (at, ids))

    def outbox_backlog(self) -> int:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM click_outbox WHERE published_at IS NULL").fetchone()
        return int(row["n"]) if row else 0

    def apply_click_event(self, click: Click) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "INSERT INTO clicks(code, ts, referrer_host, agent_family, is_bot, ip_id, ip_key_id, event_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
                (click.code, click.ts, click.referrer_host, click.agent_family, click.is_bot, click.ip_id,
                 click.ip_key_id, click.event_id))
        return cur.rowcount == 1

    def clicks_for(self, code: str) -> list[Click]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM clicks WHERE code = %s ORDER BY ts, id", (code,)).fetchall()
        return [Click(code=r["code"], ts=r["ts"], referrer_host=r["referrer_host"], agent_family=r["agent_family"],
                      is_bot=r["is_bot"], ip_id=r["ip_id"], ip_key_id=r["ip_key_id"], event_id=r["event_id"])
                for r in rows]

    # ---------------------------------------------------------------- audit
    def append_audit(self, ts: str, actor: str, action: str, target: str, details: str, chain: ChainFn) -> str:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (AUDIT_LOCK_KEY,))   # released at commit/rollback
            row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            prev_hash, new_hash = chain(row["hash"] if row else None)
            conn.execute("INSERT INTO audit_log(ts, actor, action, target, details, prev_hash, hash)"
                         " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                         (ts, actor, action, target, details, prev_hash, new_hash))
            return new_hash

    def audit_records(self) -> list[AuditRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        return [AuditRecord(**r) for r in rows]

    # ---------------------------------------------------------------- ops
    def ping(self) -> bool:
        try:
            with self._pool.connection(timeout=2) as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:  # readiness probe: any failure means "not ready", never an exception
            return False
