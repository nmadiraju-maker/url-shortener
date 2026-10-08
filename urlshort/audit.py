"""Tamper-evident audit trail.

Each record stores sha256(previous hash + its own contents). Editing, deleting or reordering any
record breaks every hash after it, which `verify()` detects. This is tamper-EVIDENT, not
tamper-proof: someone who can rewrite the whole table can recompute the chain, so production
deployments should also anchor the latest hash in write-once storage.
"""

from __future__ import annotations

import hashlib
import hmac
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

    # ---------------------------------------------------------------- signed checkpoints
    def checkpoint(self, key: str, now: datetime) -> dict[str, Any]:
        """A signed statement "at `ts` the chain had `count` records ending in `head_hash`".

        The hash chain alone detects editing a record, but not rewriting the whole chain or deleting the newest
        records. Checkpoints, signed with a key the database never sees and shipped to write-once storage (for
        example S3 Object Lock), close that gap: `verify_checkpoint` detects both.
        """
        if not key:
            raise ValueError("an anchor key is required (URLSHORT_AUDIT_ANCHOR_KEY or _FILE)")
        records = self._repo.audit_records()
        body = {"count": len(records), "head_hash": records[-1].hash if records else GENESIS,
                "ts": now.astimezone(UTC).isoformat()}
        return {**body, "signature": _sign(key, body)}

    def verify_checkpoint(self, cp: Mapping[str, Any], key: str) -> tuple[bool, str]:
        """(ok, reason). Checks the signature, that the chain still contains the checkpointed prefix, and the chain."""
        body = {k: cp.get(k) for k in ("count", "head_hash", "ts")}
        if not hmac.compare_digest(str(cp.get("signature", "")), _sign(key, body)):
            return False, "checkpoint signature is invalid"
        count = body["count"]
        if not isinstance(count, int) or count < 0:
            return False, "checkpoint is malformed"
        records = self._repo.audit_records()
        if len(records) < count:
            return False, f"audit log truncated: {len(records)} records, checkpoint has {count}"
        if (records[count - 1].hash if count else GENESIS) != body["head_hash"]:
            return False, "audit log rewritten: the checkpointed record no longer matches"
        if not self.verify():
            return False, "audit chain is broken"
        return True, f"ok: {len(records)} records, checkpoint at {count} intact"


def _sign(key: str, body: Mapping[str, Any]) -> str:
    return hmac.new(key.encode(), json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()
