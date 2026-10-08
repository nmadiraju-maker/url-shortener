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

from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..errors import AliasConflict
from ..storage import AuditRecord, ChainFn, Click, Link

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

    # ---------------------------------------------------------------- clicks
    @staticmethod
    def _insert_click(conn: psycopg.Connection[dict[str, Any]], click: Click) -> None:
        conn.execute(
            "INSERT INTO clicks(code, ts, referrer_host, agent_family, is_bot, ip_id, ip_key_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (click.code, click.ts, click.referrer_host, click.agent_family, click.is_bot, click.ip_id, click.ip_key_id))

    def record_click(self, click: Click) -> None:
        with self._pool.connection() as conn, conn.transaction():
            self._insert_click(conn, click)
            if not click.is_bot:
                conn.execute("UPDATE links SET click_count = click_count + 1 WHERE code = %s", (click.code,))

    def record_click_with_limit(self, click: Click) -> bool:
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
                self._insert_click(conn, click)
            return allowed

    def clicks_for(self, code: str) -> list[Click]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM clicks WHERE code = %s ORDER BY ts, id", (code,)).fetchall()
        return [Click(code=r["code"], ts=r["ts"], referrer_host=r["referrer_host"], agent_family=r["agent_family"],
                      is_bot=r["is_bot"], ip_id=r["ip_id"], ip_key_id=r["ip_key_id"]) for r in rows]

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
