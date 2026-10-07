"""Persistence. `Repository` is the port the service depends on; `SqliteRepository` is the adapter.

Guarantees:
  * All SQL is parameterised (no string formatting into queries).
  * Multi-step writes are single transactions: a click row and its counter, and each audit record
    with its link to the previous record, are written all-or-nothing.
  * The audit chain is appended under `BEGIN IMMEDIATE`, so separate connections or processes
    cannot both chain onto the same previous record (no forks).
  * Only a duplicate short code becomes AliasConflict; every other database error propagates.
  * Schema changes are versioned with PRAGMA user_version and are expand-only (new nullable
    columns), so older databases are upgraded in place and older code still runs against them.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .errors import AliasConflict

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    code        TEXT PRIMARY KEY,
    target_url  TEXT NOT NULL,
    owner       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    click_count INTEGER NOT NULL DEFAULT 0,
    stats_token_hash TEXT
);
CREATE INDEX IF NOT EXISTS ix_links_owner_target ON links(owner, target_url);
CREATE TABLE IF NOT EXISTS clicks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL REFERENCES links(code),
    ts            TEXT NOT NULL,
    referrer_host TEXT,
    agent_family  TEXT NOT NULL,
    is_bot        INTEGER NOT NULL,
    ip_id         INTEGER,
    ip_key_id     TEXT
);
CREATE INDEX IF NOT EXISTS ix_clicks_code_ts ON clicks(code, ts);
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    target    TEXT NOT NULL,
    details   TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash      TEXT NOT NULL
);
"""
SCHEMA_VERSION = 2
# version -> steps added in that version: (table, column, complete DDL). Expand-only and nullable.
# Every statement is a literal; nothing is built from strings at runtime.
MIGRATIONS: dict[int, tuple[tuple[str, str, str], ...]] = {
    2: (("links", "stats_token_hash", "ALTER TABLE links ADD COLUMN stats_token_hash TEXT"),
        ("clicks", "ip_id", "ALTER TABLE clicks ADD COLUMN ip_id INTEGER"),
        ("clicks", "ip_key_id", "ALTER TABLE clicks ADD COLUMN ip_key_id TEXT")),
}
SET_SCHEMA_VERSION = "PRAGMA user_version = 2"


@dataclass(frozen=True)
class Link:
    code: str
    target_url: str
    owner: str
    created_at: datetime
    expires_at: datetime | None
    is_active: bool
    click_count: int
    stats_token_hash: str | None = None   # sha256 of the per-link stats token; the token itself is never stored


@dataclass(frozen=True)
class Click:
    code: str
    ts: datetime
    referrer_host: str | None
    agent_family: str
    is_bot: bool
    ip_id: int | None          # keyed visitor ID (see analytics.visitor_id); never the raw IP
    ip_key_id: str | None      # which daily key produced ip_id (UTC date)


@dataclass(frozen=True)
class AuditRecord:
    id: int
    ts: str
    actor: str
    action: str
    target: str
    details: str
    prev_hash: str
    hash: str


# Given the previous record's hash (None for the first record), return (prev_hash, new_hash).
ChainFn = Callable[[str | None], tuple[str, str]]


class Repository(Protocol):
    def insert_link(self, link: Link) -> None: ...
    def get_link(self, code: str) -> Link | None: ...
    def find_reusable_link(self, owner: str, target_url: str) -> Link | None: ...
    def deactivate(self, code: str) -> bool: ...
    def record_click(self, click: Click) -> None: ...
    def clicks_for(self, code: str) -> list[Click]: ...
    def append_audit(self, ts: str, actor: str, action: str, target: str, details: str, chain: ChainFn) -> str: ...
    def audit_records(self) -> list[AuditRecord]: ...
    def ping(self) -> bool: ...


def _iso(value: datetime) -> str:
    """Store timestamps as UTC ISO-8601 text; naive datetimes are a bug, so refuse them."""
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SqliteRepository:
    def __init__(self, path: str = ":memory:") -> None:
        # One connection shared by the app's threads; every use goes through self._lock.
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 5000")  # wait up to 5 s for a lock instead of failing
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")  # readers don't block behind writers
            self._conn.executescript(SCHEMA)
            self._migrate()

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        """Bring older databases up to SCHEMA_VERSION (caller holds the lock).

        Fresh databases already have every column from SCHEMA; the column check makes each step
        idempotent, so it is safe for both fresh and older databases.
        """
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for target in sorted(v for v in MIGRATIONS if v > version):
            for table, column, ddl in MIGRATIONS[target]:
                columns = {r["name"] for r in self._conn.execute("SELECT name FROM pragma_table_info(?)", (table,))}
                if column not in columns:
                    self._conn.execute(ddl)
        if version < SCHEMA_VERSION:
            self._conn.execute(SET_SCHEMA_VERSION)

    @staticmethod
    def _to_link(row: sqlite3.Row) -> Link:
        return Link(code=row["code"], target_url=row["target_url"], owner=row["owner"],
                    created_at=_dt(row["created_at"]),
                    expires_at=_dt(row["expires_at"]) if row["expires_at"] else None,
                    is_active=bool(row["is_active"]), click_count=row["click_count"],
                    stats_token_hash=row["stats_token_hash"])

    # ---------------------------------------------------------------- links
    def insert_link(self, link: Link) -> None:
        params = (link.code, link.target_url, link.owner, _iso(link.created_at),
                  _iso(link.expires_at) if link.expires_at else None, int(link.is_active), link.click_count,
                  link.stats_token_hash)
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO links(code, target_url, owner, created_at, expires_at, is_active, click_count,"
                    " stats_token_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", params)
        except sqlite3.IntegrityError as exc:
            # The database is the final judge of uniqueness (no check-then-insert race). Only a
            # duplicate primary key means "code taken"; other violations are bugs and must surface.
            if exc.sqlite_errorname == "SQLITE_CONSTRAINT_PRIMARYKEY":
                raise AliasConflict(f"code '{link.code}' already exists") from exc
            raise

    def get_link(self, code: str) -> Link | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        return self._to_link(row) if row else None

    def find_reusable_link(self, owner: str, target_url: str) -> Link | None:
        """Latest active, permanent link for (owner, url), used for idempotent creates.

        Filtering happens in SQL before LIMIT 1. Filtering afterwards could pick an expiring link
        and wrongly conclude that no permanent one exists.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM links WHERE owner = ? AND target_url = ? AND is_active = 1 AND expires_at IS NULL"
                " ORDER BY created_at DESC LIMIT 1", (owner, target_url)).fetchone()
        return self._to_link(row) if row else None

    def deactivate(self, code: str) -> bool:
        """Soft delete: history and audit stay intact, and the code is never reused."""
        with self._lock:
            cur = self._conn.execute("UPDATE links SET is_active = 0 WHERE code = ? AND is_active = 1", (code,))
        return cur.rowcount == 1

    # ---------------------------------------------------------------- clicks
    def record_click(self, click: Click) -> None:
        """Insert the click and bump the human click counter in one transaction."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "INSERT INTO clicks(code, ts, referrer_host, agent_family, is_bot, ip_id, ip_key_id)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (click.code, _iso(click.ts), click.referrer_host, click.agent_family, int(click.is_bot),
                     click.ip_id, click.ip_key_id))
                if not click.is_bot:
                    self._conn.execute("UPDATE links SET click_count = click_count + 1 WHERE code = ?", (click.code,))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def clicks_for(self, code: str) -> list[Click]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM clicks WHERE code = ? ORDER BY ts, id", (code,)).fetchall()
        return [Click(code=r["code"], ts=_dt(r["ts"]), referrer_host=r["referrer_host"],
                      agent_family=r["agent_family"], is_bot=bool(r["is_bot"]), ip_id=r["ip_id"],
                      ip_key_id=r["ip_key_id"])
                for r in rows]

    # ---------------------------------------------------------------- audit
    def append_audit(self, ts: str, actor: str, action: str, target: str, details: str, chain: ChainFn) -> str:
        """Read the last hash and append the next record atomically; returns the new hash."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")  # takes the write lock now: no other writer can interleave
            try:
                row = self._conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
                prev_hash, new_hash = chain(row["hash"] if row else None)
                self._conn.execute(
                    "INSERT INTO audit_log(ts, actor, action, target, details, prev_hash, hash)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)", (ts, actor, action, target, details, prev_hash, new_hash))
                self._conn.execute("COMMIT")
                return new_hash
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def audit_records(self) -> list[AuditRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        return [AuditRecord(**dict(r)) for r in rows]

    # ---------------------------------------------------------------- ops
    def ping(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False
