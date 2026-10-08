"""Scenario loading: a scenario declares the stage graph, inputs, workspace seed and engine config."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agents.base import Agent
from .agents.development import export_baseline
from .agents.registry import default_agents
from .approvals import ApprovalGateway
from .context import ContextStore
from .engine import EngineConfig, Orchestrator
from .graph import StageGraph, StageSpec
from .workspace import Workspace


def load(path: str | Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(Path(path).read_text())
    for key in ("name", "stages", "inputs"):
        if key not in data:
            raise ValueError(f"scenario missing '{key}'")
    return data


def build(scenario: dict[str, Any], run_dir: Path, approvals: ApprovalGateway, *,
          agents: dict[str, Agent] | None = None, **engine_kw: Any) -> Orchestrator:
    run_dir = Path(run_dir)
    ws = Workspace(run_dir / "workspace")
    seed = scenario.get("workspace", {})
    if seed.get("seed") == "baseline":
        origin = export_baseline(seed["ref"], seed["paths"], ws.root, **({"source": Path(seed["source"])}
                                                                         if "source" in seed else {}))
        ws.commit(f"chore: seed workspace from {seed['ref']} ({origin}) - brownfield baseline")
    ctx = ContextStore()
    ctx.inputs = json.loads(json.dumps(scenario["inputs"]))
    graph = StageGraph([StageSpec.from_dict(s) for s in scenario["stages"]])
    cfg = EngineConfig(**scenario.get("engine", {}))
    (run_dir / "scenario.json").write_text(json.dumps(scenario, indent=2))
    return Orchestrator(graph, agents or default_agents(), run_dir=run_dir, workspace=ws, approvals=approvals,
                        config=cfg, context=ctx, **engine_kw)
