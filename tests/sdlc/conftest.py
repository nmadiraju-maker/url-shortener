from __future__ import annotations

from collections.abc import Callable

import pytest

from sdlc.agents.base import Agent, AgentContext, StageResult
from sdlc.approvals import PENDING, ApprovalDecision, ApprovalGateway, ApprovalRequest
from sdlc.engine import EngineConfig, Orchestrator
from sdlc.graph import StageGraph, StageSpec
from sdlc.workspace import Workspace


class FnAgent(Agent):
    def __init__(self, name: str, fn: Callable[[AgentContext, int], StageResult]) -> None:
        self.name, self.fn, self.calls, self.contexts = name, fn, 0, []

    def run(self, ctx: AgentContext) -> StageResult:
        self.calls += 1
        self.contexts.append(ctx)
        return self.fn(ctx, self.calls)


def ok(name: str = "out", **kw) -> Callable:
    return lambda ctx, n: StageResult(summary="ok", artifacts={name: {"n": n}}, **kw)


class DictApprovals(ApprovalGateway):
    def __init__(self, decisions: dict | None = None) -> None:
        self.decisions = decisions or {}
        self.requests: list[ApprovalRequest] = []

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return self.decisions.get(request.checkpoint) or ApprovalDecision(PENDING)


@pytest.fixture
def build(tmp_path):
    def _build(specs: list[dict], agents: dict[str, Agent], approvals: ApprovalGateway | None = None,
               **cfg) -> Orchestrator:
        graph = StageGraph([StageSpec.from_dict(s) for s in specs])
        return Orchestrator(graph, agents, run_dir=tmp_path / "run", workspace=Workspace(tmp_path / "ws"),
                            approvals=approvals or DictApprovals(),
                            config=EngineConfig(backoff_base=0, **cfg), sleep=lambda s: None)
    return _build
