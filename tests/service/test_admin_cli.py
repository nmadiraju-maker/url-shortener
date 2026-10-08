"""Operator CLI: key management and signed audit checkpoints."""
import json
import runpy
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from urlshort import admin_cli
from urlshort.audit import AuditTrail
from urlshort.config import Settings
from urlshort.storage import SqliteRepository

T0 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
KEY = "anchor-secret"


def run(settings: Settings, *argv: str) -> tuple[int, list[str]]:
    lines: list[str] = []
    return admin_cli.main(list(argv), settings=settings, out=lines.append, now=lambda: T0), lines


def test_key_lifecycle(tmp_path: Path) -> None:
    s = Settings(db_path=str(tmp_path / "k.db"))
    code, lines = run(s, "keys", "create", "--name", "team-a ci", "--role", "owner", "--owner", "team-a")
    assert code == 0 and lines[0].startswith("us_") and "cannot be shown again" in lines[1]
    key_id = lines[0].split("_")[1]
    code, lines = run(s, "keys", "list")
    assert code == 0 and key_id in lines[0] and "owner=team-a" in lines[0] and lines[0].endswith("active")
    assert run(s, "keys", "revoke", key_id) == (0, [f"revoked {key_id}"])
    assert run(s, "keys", "revoke", key_id) == (1, [f"no active key {key_id}"])
    assert "REVOKED 2026-01-15" in run(s, "keys", "list")[1][0]


def write_records(repo: SqliteRepository, n: int, actor: str = "admin:alice") -> None:
    trail = AuditTrail(repo)
    for i in range(n):
        trail.record(when=T0, actor=actor, action="link.deactivate", target=f"c{i}", details={})


def test_checkpoint_detects_truncation_and_rewrites(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    s = Settings(db_path=str(db), audit_anchor_key=KEY)
    write_records(SqliteRepository(str(db)), 3)
    code, lines = run(s, "audit", "checkpoint")
    cp = tmp_path / "cp.json"
    cp.write_text(lines[0])
    assert json.loads(lines[0])["count"] == 3
    write_records(SqliteRepository(str(db)), 2)                                   # normal growth is fine
    assert run(s, "audit", "verify", "--checkpoint", str(cp)) == (0, ["ok: 5 records, checkpoint at 3 intact"])
    wrong_key = Settings(db_path=str(db), audit_anchor_key="other")
    assert run(wrong_key, "audit", "verify", "--checkpoint", str(cp))[1] == ["checkpoint signature is invalid"]
    repo = SqliteRepository(str(db))
    repo._conn.execute("DELETE FROM audit_log WHERE id > 1")                     # truncation
    assert "truncated" in run(s, "audit", "verify", "--checkpoint", str(cp))[1][0]
    repo._conn.execute("DELETE FROM audit_log")                                   # full rewrite: a valid new chain
    write_records(repo, 3, actor="admin:someone-else")                            # ...that hides who acted
    assert AuditTrail(repo).verify() is True                                      # the chain alone cannot tell
    code, lines = run(s, "audit", "verify", "--checkpoint", str(cp))
    assert code == 1 and "rewritten" in lines[0]                                  # the checkpoint can


def test_checkpoint_edge_cases(tmp_path: Path, repo: SqliteRepository) -> None:
    trail = AuditTrail(repo)
    empty = trail.checkpoint(KEY, T0)
    assert empty["count"] == 0 and trail.verify_checkpoint(empty, KEY)[0] is True
    with pytest.raises(ValueError, match="anchor key"):
        trail.checkpoint("", T0)
    assert trail.verify_checkpoint({**empty, "count": "3"}, KEY) == (False, "checkpoint signature is invalid")
    from urlshort import audit
    forged = {"count": None, "head_hash": "x", "ts": "t"}
    forged["signature"] = audit._sign(KEY, forged)
    assert trail.verify_checkpoint(forged, KEY) == (False, "checkpoint is malformed")
    write_records(repo, 2)
    cp = trail.checkpoint(KEY, T0)
    repo._conn.execute("UPDATE audit_log SET actor = 'someone-else' WHERE id = 2")   # edit the head record
    assert trail.verify_checkpoint(cp, KEY) == (False, "audit chain is broken")


def test_postgres_storage_is_used_when_configured(monkeypatch: pytest.MonkeyPatch, repo: SqliteRepository) -> None:
    seen: list[str] = []
    monkeypatch.setattr("urlshort.adapters.wiring.build",
                        lambda settings, given: (seen.append(settings.database_url) or repo, None))
    assert admin_cli.repository(Settings(database_url="postgresql://u:p@db/x")) is repo
    assert seen == ["postgresql://u:p@db/x"]


def test_module_entry_point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("URLSHORT_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setattr(sys, "argv", ["admin_cli", "keys", "list"])
    monkeypatch.delitem(sys.modules, "urlshort.admin_cli", raising=False)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("urlshort.admin_cli", run_name="__main__")
    assert exc.value.code == 0
