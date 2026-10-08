"""Operator commands:  python -m urlshort.admin_cli ...

  keys create --name NAME --role owner|admin [--owner OWNER]   prints the key ONCE
  keys list                                                     id, name, role, owner, created, revoked
  keys revoke KEY_ID
  audit checkpoint                                              signed checkpoint as JSON (ship it to WORM storage)
  audit verify --checkpoint FILE                                exit 0 if intact, 1 otherwise

Uses the same storage settings as the API (URLSHORT_DATABASE_URL or URLSHORT_DB_PATH); checkpoints need
URLSHORT_AUDIT_ANCHOR_KEY (or _FILE).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .audit import AuditTrail
from .auth import create_key
from .config import Settings
from .storage import Repository, SqliteRepository


def repository(settings: Settings) -> Repository:
    if settings.database_url:
        from .adapters.wiring import build
        return build(Settings(database_url=settings.database_url), None)[0]
    return SqliteRepository(settings.db_path)


def main(argv: list[str] | None = None, *, settings: Settings | None = None,
         out: Callable[[str], None] = print, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    parser = argparse.ArgumentParser(prog="urlshort.admin_cli")
    area = parser.add_subparsers(dest="area", required=True)
    keys = area.add_parser("keys").add_subparsers(dest="action", required=True)
    create = keys.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--role", choices=["owner", "admin"], required=True)
    create.add_argument("--owner")
    keys.add_parser("list")
    revoke = keys.add_parser("revoke")
    revoke.add_argument("key_id")
    audit = area.add_parser("audit").add_subparsers(dest="action", required=True)
    audit.add_parser("checkpoint")
    verify = audit.add_parser("verify")
    verify.add_argument("--checkpoint", required=True, type=Path)
    args = parser.parse_args(argv)
    settings = settings or Settings.from_env()
    repo = repository(settings)
    if args.area == "keys" and args.action == "create":
        out(create_key(repo, name=args.name, role=args.role, owner=args.owner, now=now()))
        out("Store this key now: it is not kept and cannot be shown again.")
    elif args.area == "keys" and args.action == "list":
        for k in repo.list_api_keys():
            out(f"{k.key_id}  {k.role:<6} {k.name:<24} owner={k.owner or '-'}  created={k.created_at.date()}"
                f"  {'REVOKED ' + str(k.revoked_at.date()) if k.revoked_at else 'active'}")
    elif args.area == "keys":
        if not repo.revoke_api_key(args.key_id, now()):
            out(f"no active key {args.key_id}")
            return 1
        out(f"revoked {args.key_id}")
    elif args.action == "checkpoint":
        out(json.dumps(AuditTrail(repo).checkpoint(settings.audit_anchor_key, now())))
    else:
        ok, reason = AuditTrail(repo).verify_checkpoint(json.loads(args.checkpoint.read_text()),
                                                        settings.audit_anchor_key)
        out(reason)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
