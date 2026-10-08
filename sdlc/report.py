"""Audit-grade run report: graph state, timeline, approvals, policy, re-plans, lineage and metrics."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .engine import Orchestrator

INTERESTING = ("stage.", "approval.", "replan.", "policy.", "run.")


def render(orch: Orchestrator, title: str) -> str:
    m = orch.metrics.summary()
    recs = orch.audit.records()
    out = [f"# SDLC run report — {title}", "",
           f"- **Run id:** `{orch.run_id}`  \n- **Final status:** **{orch.run_status}**  \n"
           f"- **Audit chain verified:** {orch.audit.verify()} ({len(recs)} hash-chained events)", "",
           "## Stage graph (final state)", "```mermaid", orch.graph.to_mermaid(orch.status), "```", "",
           "Parallel waves: " + " → ".join("[" + ", ".join(w) + "]" for w in orch.graph.layers()), "",
           "## Stages", "| Stage | Agent | Status | Attempts | Retries | Rollbacks | Fallbacks | Reworks | Note |",
           "|---|---|---|---|---|---|---|---|---|"]
    for sid in orch.graph.topological_order():
        s = m["per_stage"].get(sid, {})
        spec = orch.graph.stages[sid]
        fallback = f" → {spec.fallback_agent}" if spec.fallback_agent else ""
        note = orch.reasons.get(sid, "") if orch.status[sid] != "SUCCEEDED" else ""
        out.append(f"| {sid} | {spec.agent}{fallback} | {orch.status[sid]} | {s.get('attempts', 0)} | "
                   f"{s.get('retries', 0)} | {s.get('rollbacks', 0)} | {s.get('fallbacks', 0)} | "
                   f"{s.get('reworks', 0)} | {note} |")
    out += ["", "## Human approval checkpoints", "| Checkpoint | Decision | Approver | Reason | Comment |",
            "|---|---|---|---|---|"]
    out += [f"| {a['checkpoint']} | **{a['status']}** | {a['approver']} | {a['reason']} | {a['comment']} |"
            for a in orch.approvals_log] or ["| - | - | - | - | - |"]
    for sid, p in orch.pending.items():
        out.append(f"| {sid} (pending) | **pending** | — | {p['reason']} | run paused; resume after decision |")
    out += ["", "## Policy guardrail evaluations", "| Stage | Outcome | Violations |", "|---|---|---|"]
    for p in orch.policy_log:
        v = "<br/>".join(f"{x['rule']} ({x['outcome']}) {x['message']} `{x['location']}`"
                         for x in p["violations"]) or "—"
        out.append(f"| {p['stage']} | {p['outcome']} | {v} |")
    out += ["", "## Re-planning & recovery events"]
    replans = [r for r in recs if r["event"].startswith(("replan.", "stage.rollback", "stage.fallback",
                                                          "stage.attempt_failed", "run.safe_stop", "run.paused"))]
    out += [f"- `{r['ts'][11:19]}` **{r['event']}** {r['stage'] or ''} — {_brief(r['data'])}"
            for r in replans] or ["- none"]
    out += ["", "## Decisions (lineage)", "| Id | Stage | Decision | Rationale | By |", "|---|---|---|---|---|"]
    out += [f"| {d.id} | {d.stage} | {d.summary} | {d.rationale} | {d.decided_by} |" for d in orch.ctx.decisions]
    for name in ("release", "code_change"):
        if orch.ctx.has(name):
            out += ["", f"## Provenance of `{name}`", "```", *orch.ctx.lineage(name), "```"]
            break
    out += ["", "## Reliability metrics", "| Metric | Value |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in m.items() if k not in ("per_stage", "counters")]
    out += ["", "## Timeline", "| Time (UTC) | Event | Stage | Actor |", "|---|---|---|---|"]
    out += [f"| {r['ts'][11:23]} | {r['event']} | {r['stage'] or ''} | {r['actor']} |" for r in recs
            if r["event"].startswith(INTERESTING)]
    return "\n".join(out) + "\n"


def _brief(data: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in data.items() if k not in ("metrics", "stage_status")}, default=str)[:300]


def write(orch: Orchestrator, title: str) -> Path:
    path = Path(orch.run_dir) / "run-report.md"
    path.write_text(render(orch, title))
    (Path(orch.run_dir) / "metrics.json").write_text(json.dumps(orch.metrics.summary(), indent=2))
    return path
