"""Agent contract. Agents do work; the engine (not the agent) enforces gates, policy and approvals."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..context import ContextStore
    from ..graph import StageSpec
    from ..workspace import Workspace


@dataclass
class AgentContext:
    spec: StageSpec
    context: ContextStore
    workspace: Workspace
    attempt: int
    feedback: list[str]
    run_dir: Path
    params: dict[str, Any]


@dataclass
class StageResult:
    summary: str
    artifacts: dict[str, object] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)         # exit-gate evidence
    commit_message: str | None = None
    rework: str | None = None                                       # upstream stage to send back to
    feedback: list[str] = field(default_factory=list)
    escalate: str | None = None                                     # agent asks for human approval
    new_stages: list[dict[str, Any]] = field(default_factory=list)            # [{"spec": {...}, "before": [...]}]
    decisions: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)                    # applied only after approval


class Agent:
    name = "base"

    def run(self, ctx: AgentContext) -> StageResult:  # pragma: no cover - interface
        raise NotImplementedError
