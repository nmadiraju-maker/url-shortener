"""Tamper-evident audit trail.

Each record stores sha256(previous hash + its own contents). Editing, deleting or reordering any
record breaks every hash after it, which `verify()` detects. This is tamper-EVIDENT, not
tamper-proof: someone who can rewrite the whole table can recompute the chain, so production
deployments should also anchor the latest hash in write-once storage.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .storage import Repository

GENESIS = "0" * 64


def digest(prev_hash: str, ts: str, actor: str, action: str, target: str, details: str) -> str:
    payload = json.dumps([prev_hash, ts, actor, action, target, details], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class AuditTrail:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def record(self, *, when: datetime, actor: str, action: str, target: str, details: Mapping[str, Any]) -> str:
        if when.tzinfo is None:
            raise ValueError("audit timestamps must be timezone-aware")
        ts = when.astimezone(UTC).isoformat()
        body = json.dumps(dict(details), sort_keys=True, default=str)

        def chain(prev: str | None) -> tuple[str, str]:
            prev_hash = prev or GENESIS
            return prev_hash, digest(prev_hash, ts, actor, action, target, body)

        return self._repo.append_audit(ts, actor, action, target, body, chain)

    def verify(self) -> bool:
        prev = GENESIS
        for rec in self._repo.audit_records():
            if rec.prev_hash != prev:
                return False
            if digest(prev, rec.ts, rec.actor, rec.action, rec.target, rec.details) != rec.hash:
                return False
            prev = rec.hash
        return True
