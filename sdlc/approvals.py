"""Human-in-the-loop approval gateway.

Approval sources:
  * DecisionFileApprovals - decisions recorded by a named human reviewer in a JSON file
    (used for reproducible demos/CI and for asynchronous approvals).
  * InteractiveApprovals  - prompt on the terminal.
A missing decision is PENDING: the engine safe-stops and persists state so the run can be resumed.
Default is deny-by-absence: nothing high-impact proceeds without an explicit human decision.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APPROVED, REJECTED, PENDING, AMEND = "approved", "rejected", "pending", "amend"


@dataclass
class ApprovalRequest:
    checkpoint: str          # e.g. "design#1"
    stage: str
    reason: str
    summary: dict[str, Any]


@dataclass
class ApprovalDecision:
    status: str
    approver: str = ""
    comment: str = ""
    amendments: dict[str, Any] = field(default_factory=dict)   # {"stage": ..., "inputs": {...}} upstream change


class ApprovalGateway:
    def decide(self, request: ApprovalRequest) -> ApprovalDecision:  # pragma: no cover - interface
        raise NotImplementedError


class DecisionFileApprovals(ApprovalGateway):
    def __init__(self, path: str | Path | None) -> None:
        self._decisions: dict[str, Any] = {}
        if path and Path(path).exists():
            self._decisions = json.loads(Path(path).read_text())

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        entry = self._decisions.get(request.checkpoint) or self._decisions.get(request.stage + "#*")
        if not entry:
            return ApprovalDecision(PENDING)
        status = entry.get("status", PENDING)
        if status not in {APPROVED, REJECTED, AMEND, PENDING}:
            return ApprovalDecision(PENDING, comment=f"unrecognised status '{status}'")
        if status != PENDING and not entry.get("approver"):
            return ApprovalDecision(PENDING, comment="decision without a named approver is ignored")
        return ApprovalDecision(status, entry.get("approver", ""), entry.get("comment", ""),
                                entry.get("amendments", {}))


class InteractiveApprovals(ApprovalGateway):
    def __init__(self, ask: Callable[[str], str] = input, approver: str = "terminal-user") -> None:
        self._ask = ask
        self._approver = approver

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        print(f"\n=== APPROVAL REQUIRED: {request.checkpoint} ===\nReason: {request.reason}")
        print(json.dumps(request.summary, indent=2, default=str)[:3000])
        answer = self._ask("Approve? [y]es / [n]o / [p]ause: ").strip().lower()
        if answer.startswith("y"):
            return ApprovalDecision(APPROVED, self._approver, "approved interactively")
        if answer.startswith("n"):
            return ApprovalDecision(REJECTED, self._approver, self._ask("Reason: "))
        return ApprovalDecision(PENDING)
