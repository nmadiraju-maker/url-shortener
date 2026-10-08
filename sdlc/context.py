"""Cross-stage context: versioned artifacts and decision lineage.

Every artifact records the hashes of the artifacts it was derived from, so any output
(e.g. a code change) can be traced back to the requirement and decisions behind it.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def content_hash(content: object) -> str:
    raw = json.dumps(content, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class Artifact:
    name: str
    content: object
    produced_by: str
    version: int
    hash: str
    derived_from: list[str] = field(default_factory=list)  # artifact "name@hash"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.hash}"


@dataclass
class Decision:
    id: str
    stage: str
    summary: str
    rationale: str
    alternatives: list[str] = field(default_factory=list)
    based_on: list[str] = field(default_factory=list)
    decided_by: str = "agent"


class ContextStore:
    def __init__(self) -> None:
        self._artifacts: dict[str, list[Artifact]] = {}
        self.decisions: list[Decision] = []
        self.inputs: dict[str, dict[str, Any]] = {}   # stage id -> extra params (incl. human amendments)
        self.feedback: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def put(self, name: str, content: object, produced_by: str, derived_from: list[str] | None = None) -> Artifact:
        with self._lock:
            history = self._artifacts.setdefault(name, [])
            art = Artifact(name=name, content=content, produced_by=produced_by, version=len(history) + 1,
                           hash=content_hash(content), derived_from=list(derived_from or []))
            history.append(art)
            return art

    def get(self, name: str) -> Artifact | None:
        history = self._artifacts.get(name)
        return history[-1] if history else None

    def require(self, name: str) -> object:
        art = self.get(name)
        if art is None:
            raise KeyError(f"artifact '{name}' not available")
        return art.content

    def has(self, name: str) -> bool:
        return name in self._artifacts

    def history(self, name: str) -> list[Artifact]:
        return list(self._artifacts.get(name, []))

    def names(self) -> list[str]:
        return sorted(self._artifacts)

    def decide(self, stage: str, summary: str, rationale: str, *, alternatives: list[str] | None = None,
               based_on: list[str] | None = None, decided_by: str = "agent") -> Decision:
        with self._lock:
            d = Decision(id=f"D{len(self.decisions) + 1:03d}", stage=stage, summary=summary, rationale=rationale,
                         alternatives=list(alternatives or []), based_on=list(based_on or []),
                         decided_by=decided_by)
            self.decisions.append(d)
            return d

    def add_feedback(self, stage: str, note: str) -> None:
        with self._lock:
            self.feedback.setdefault(stage, []).append(note)

    def lineage(self, name: str) -> list[str]:
        """Depth-first provenance chain for the latest version of an artifact."""
        out: list[str] = []
        index = {a.ref: a for hist in self._artifacts.values() for a in hist}

        def walk(ref: str, depth: int) -> None:
            out.append("  " * depth + ref)
            art = index.get(ref)
            for parent in (art.derived_from if art else []):
                walk(parent, depth + 1)

        latest = self.get(name)
        if latest:
            walk(latest.ref, 0)
        return out

    def snapshot(self) -> dict[str, Any]:
        return {
            "inputs": self.inputs,
            "artifacts": {n: [{k: v for k, v in asdict(a).items() if k != "content"} | {"ref": a.ref}
                              for a in h] for n, h in self._artifacts.items()},
            "decisions": [asdict(d) for d in self.decisions],
            "feedback": self.feedback,
        }


def dump_context(store: ContextStore) -> dict[str, Any]:
    return {"inputs": store.inputs, "feedback": store.feedback,
            "decisions": [asdict(d) for d in store.decisions],
            "artifacts": {n: [asdict(a) for a in h] for n, h in store._artifacts.items()}}


def load_context(data: dict[str, Any]) -> ContextStore:
    store = ContextStore()
    store.inputs = data.get("inputs", {})
    store.feedback = data.get("feedback", {})
    store.decisions = [Decision(**d) for d in data.get("decisions", [])]
    store._artifacts = {n: [Artifact(**a) for a in h] for n, h in data.get("artifacts", {}).items()}
    return store
