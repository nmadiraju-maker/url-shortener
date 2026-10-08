"""Append-only, hash-chained JSONL audit log for the orchestrator (tamper-evident)."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._lock = threading.Lock()
        self._prev = GENESIS
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            lines = self.path.read_text().splitlines()
            if lines:
                self._prev = json.loads(lines[-1])["hash"]

    def emit(self, event: str, *, stage: str | None = None, actor: str = "orchestrator",
             **data: object) -> dict[str, Any]:
        with self._lock:
            rec: dict[str, Any] = {"ts": datetime.now(UTC).isoformat(), "run_id": self.run_id, "event": event,
                   "stage": stage, "actor": actor, "data": data, "prev_hash": self._prev}
            rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()
            with self.path.open("a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
            self._prev = rec["hash"]
            return rec

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def verify(self) -> bool:
        prev = GENESIS
        for rec in self.records():
            claimed = rec.pop("hash")
            if rec["prev_hash"] != prev:
                return False
            if hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest() != claimed:
                return False
            prev = claimed
        return True
