"""Explicit stage dependency graph (DAG) with gates, retry/fallback and approval metadata."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .errors import GraphError


@dataclass
class StageSpec:
    id: str
    agent: str
    depends_on: list[str] = field(default_factory=list)
    entry_gates: list[str] = field(default_factory=list)   # artifact names that must exist
    exit_gates: list[str] = field(default_factory=list)    # named checks evaluated on the result
    requires_approval: bool = False
    impact: str = "low"                                     # low | medium | high
    max_retries: int = 1
    fallback_agent: str | None = None
    mutates_workspace: bool = False                         # snapshot + rollback if True
    critical: bool = True                                   # failure safe-stops the run
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageSpec:
        return cls(**data)


class StageGraph:
    def __init__(self, stages: list[StageSpec]) -> None:
        self.stages: dict[str, StageSpec] = {}
        for s in stages:
            if s.id in self.stages:
                raise GraphError(f"duplicate stage id '{s.id}'")
            self.stages[s.id] = s
        self.validate()

    def validate(self) -> None:
        for s in self.stages.values():
            for dep in s.depends_on:
                if dep not in self.stages:
                    raise GraphError(f"stage '{s.id}' depends on unknown stage '{dep}'")
        self.topological_order()  # raises on cycle

    def topological_order(self) -> list[str]:
        indeg = {sid: len(s.depends_on) for sid, s in self.stages.items()}
        queue = deque(sorted(sid for sid, d in indeg.items() if d == 0))
        order: list[str] = []
        while queue:
            sid = queue.popleft()
            order.append(sid)
            for child in sorted(self.children(sid)):
                indeg[child] -= 1
                if indeg[child] == 0:
                    queue.append(child)
        if len(order) != len(self.stages):
            raise GraphError("stage graph contains a cycle")
        return order

    def layers(self) -> list[list[str]]:
        """Group stages into waves that may run in parallel."""
        depth: dict[str, int] = {}
        for sid in self.topological_order():
            deps = self.stages[sid].depends_on
            depth[sid] = 1 + max((depth[d] for d in deps), default=-1)
        waves: dict[int, list[str]] = {}
        for sid, d in depth.items():
            waves.setdefault(d, []).append(sid)
        return [sorted(waves[i]) for i in sorted(waves)]

    def children(self, sid: str) -> list[str]:
        return [s.id for s in self.stages.values() if sid in s.depends_on]

    def descendants(self, sid: str) -> list[str]:
        seen: list[str] = []
        stack = list(self.children(sid))
        while stack:
            cur = stack.pop()
            if cur not in seen:
                seen.append(cur)
                stack.extend(self.children(cur))
        order = self.topological_order()
        return sorted(seen, key=order.index)

    def insert(self, spec: StageSpec, before: list[str]) -> None:
        """Dynamically add a stage (re-planning) and wire it in front of `before` stages."""
        if spec.id in self.stages:
            raise GraphError(f"duplicate stage id '{spec.id}'")
        self.stages[spec.id] = spec
        for b in before:
            if b not in self.stages:
                del self.stages[spec.id]
                raise GraphError(f"cannot insert before unknown stage '{b}'")
            self.stages[b].depends_on.append(spec.id)
        try:
            self.validate()
        except GraphError:
            for b in before:
                self.stages[b].depends_on.remove(spec.id)
            del self.stages[spec.id]
            raise

    def to_mermaid(self, statuses: dict[str, str] | None = None) -> str:
        statuses = statuses or {}
        lines = ["flowchart LR"]
        for sid in self.topological_order():
            s = self.stages[sid]
            tag = " 🔒" if s.requires_approval else ""
            st = f"<br/><i>{statuses[sid]}</i>" if sid in statuses else ""
            lines.append(f'    {sid}["{sid}{tag}<br/>({s.agent}){st}"]')
            for d in s.depends_on:
                lines.append(f"    {d} --> {sid}")
        palette = {"SUCCEEDED": "#d4edda", "FAILED": "#f8d7da", "BLOCKED": "#fff3cd",
                   "AWAITING_APPROVAL": "#cce5ff", "SKIPPED": "#e2e3e5"}
        for sid, st in statuses.items():
            if st in palette:
                lines.append(f"    style {sid} fill:{palette[st]}")
        return "\n".join(lines)
