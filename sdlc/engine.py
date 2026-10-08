"""Orchestration engine: stateful, non-linear execution of the SDLC stage graph under governance.

Control flow
  * Worker threads run agents (parallel where the DAG allows; workspace-mutating stages are
    serialised by a lock).  The coordinator thread owns ALL state transitions, approvals and
    re-planning, so the state machine stays deterministic.
  * Per stage: entry gates -> snapshot -> attempt(s) [agent -> exit gates -> policy] with bounded
    retries + backoff -> fallback agent -> rollback on failure -> human approval when required.
  * Non-linear paths: review can send work back (rework), humans can amend upstream inputs
    (re-plan), agents can propose new stages (dynamic insertion); changed upstream outputs
    invalidate already-completed descendants.
  * Safe-stop: kill-switch file, wall-clock budget, failure budget, critical-stage failure and
    pending approvals all stop the run with state persisted for `resume`.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from .agents.base import Agent, AgentContext, StageResult
from .approvals import AMEND, APPROVED, PENDING, ApprovalGateway, ApprovalRequest
from .audit import AuditLog
from .context import Artifact, ContextStore, dump_context, load_context
from .errors import AgentError, TransientError
from .graph import StageGraph, StageSpec
from .metrics import Metrics
from .policy import BLOCK, ESCALATE, PolicyEngine, PolicyReport
from .workspace import Workspace

PENDING_S, RUNNING, AWAITING, SUCCEEDED, FAILED, BLOCKED, SKIPPED = (
    "PENDING", "RUNNING", "AWAITING_APPROVAL", "SUCCEEDED", "FAILED", "BLOCKED", "SKIPPED")
TERMINAL = {SUCCEEDED, FAILED, BLOCKED, SKIPPED}


@dataclass
class EngineConfig:
    max_workers: int = 4
    backoff_base: float = 0.05
    max_rework: int = 2
    max_run_seconds: float = 900.0
    failure_budget: int = 12
    # chaos injection: {stage: {"transient_failures": n, "agent_error": bool}}
    faults: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Outcome:
    stage: str
    ok: bool
    result: StageResult | None = None
    policy: PolicyReport | None = None
    reason: str = ""
    agent: str = ""


class Orchestrator:
    def __init__(self, graph: StageGraph, agents: dict[str, Agent], *, run_dir: Path, workspace: Workspace,
                 approvals: ApprovalGateway, policy: PolicyEngine | None = None,
                 config: EngineConfig | None = None, context: ContextStore | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 run_id: str | None = None) -> None:
        self.graph = graph
        self.agents = agents
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.ws = workspace
        self.approvals = approvals
        self.policy = policy or PolicyEngine()
        self.cfg = config or EngineConfig()
        self.ctx = context or ContextStore()
        self.clock, self.sleep = clock, sleep
        self.run_id = run_id or self.run_dir.name
        self.audit = AuditLog(self.run_dir / "audit.jsonl", self.run_id)
        self.metrics = Metrics(clock)
        self.status: dict[str, str] = {sid: PENDING_S for sid in graph.stages}
        self.reasons: dict[str, str] = {}
        self.approval_rounds: dict[str, int] = {}
        self.rework_counts: dict[str, int] = {}
        self.snapshots: dict[str, str] = {}
        self.pending: dict[str, dict[str, Any]] = {}
        self.approvals_log: list[dict[str, Any]] = []
        self.policy_log: list[dict[str, Any]] = []
        self.run_status = "NEW"
        self._ws_lock = Lock()
        self._failures = 0
        self._missing_agents()

    def _missing_agents(self) -> None:
        for s in self.graph.stages.values():
            for name in filter(None, [s.agent, s.fallback_agent]):
                if name not in self.agents:
                    raise ValueError(f"stage '{s.id}' references unknown agent '{name}'")

    # ================================================================ public API
    def run(self) -> str:
        if self.run_status == "PAUSED":  # resume() re-asked a parked approval and it is still pending
            self.persist()
            return self.run_status
        self.run_status = "RUNNING"
        self.audit.emit("run.start", stages=list(self.graph.stages), layers=self.graph.layers())
        started = self.clock()
        running: dict[Future[_Outcome], str] = {}
        with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as pool:
            while True:
                stop = self._stop_reason(started)
                if stop:
                    self._drain(running)
                    self._safe_stop(stop)
                    break
                self._propagate_blocks()
                for sid in self._ready(running):
                    self.status[sid] = RUNNING
                    self.audit.emit("stage.start", stage=sid, agent=self.graph.stages[sid].agent)
                    running[pool.submit(self._execute, sid)] = sid
                if not running:
                    break
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for fut in done:
                    running.pop(fut)
                    self._handle(fut.result())
                if self.run_status in {"PAUSED", "STOPPED"}:
                    self._drain(running)
                    break
        if self.run_status == "RUNNING":
            self.run_status = "COMPLETED" if all(s == SUCCEEDED for s in self.status.values()) else "FAILED"
        self.metrics.finish()
        self.audit.emit("run.end", status=self.run_status, stage_status=dict(self.status),
                        metrics=self.metrics.summary())
        self.persist()
        return self.run_status

    def request_stop(self, reason: str = "operator kill switch") -> None:
        (self.run_dir / "STOP").write_text(reason)

    # ================================================================ scheduling
    def _ready(self, running: dict[Future[_Outcome], str]) -> list[str]:
        busy = set(running.values())
        out = []
        for sid in self.graph.topological_order():
            spec = self.graph.stages[sid]
            if self.status[sid] != PENDING_S or sid in busy:
                continue
            if all(self.status[d] == SUCCEEDED for d in spec.depends_on):
                out.append(sid)
        return out

    def _propagate_blocks(self) -> None:
        changed = True
        while changed:
            changed = False
            for sid, spec in self.graph.stages.items():
                if self.status[sid] == PENDING_S and any(self.status[d] in {FAILED, BLOCKED, SKIPPED}
                                                         for d in spec.depends_on):
                    self.status[sid] = BLOCKED
                    self.reasons[sid] = "upstream did not succeed"
                    self.audit.emit("stage.blocked", stage=sid, reason=self.reasons[sid])
                    changed = True

    def _stop_reason(self, started: float) -> str | None:
        if (self.run_dir / "STOP").exists():
            return "kill switch: " + (self.run_dir / "STOP").read_text().strip()
        if self.clock() - started > self.cfg.max_run_seconds:
            return "wall-clock budget exceeded"
        if self._failures >= self.cfg.failure_budget:
            return "failure budget exhausted"
        return None

    def _drain(self, running: dict[Future[_Outcome], str]) -> None:
        for fut in list(running):
            outcome = fut.result()  # cooperative stop: let in-flight work finish, then discard
            sid = running.pop(fut)
            if outcome.ok and self.graph.stages[sid].mutates_workspace and sid in self.snapshots:
                self.ws.rollback(self.snapshots[sid])
            self.status[sid] = PENDING_S
            self.audit.emit("stage.discarded", stage=sid, reason="run stopping")

    def _safe_stop(self, reason: str) -> None:
        self.run_status = "STOPPED"
        for sid, st in self.status.items():
            if st == PENDING_S:
                self.status[sid] = SKIPPED
                self.reasons[sid] = f"safe-stop: {reason}"
        self.audit.emit("run.safe_stop", reason=reason)

    # ================================================================ execution (worker thread)
    def _execute(self, sid: str) -> _Outcome:
        spec = self.graph.stages[sid]
        missing = [a for a in spec.entry_gates if not self.ctx.has(a)]
        if missing:
            return _Outcome(sid, False, reason=f"entry gate failed; missing artifacts {missing}")
        if spec.mutates_workspace:
            with self._ws_lock:
                return self._attempts(spec)
        return self._attempts(spec)

    def _attempts(self, spec: StageSpec) -> _Outcome:
        sid = spec.id
        base: str | None = None
        if spec.mutates_workspace:
            self.snapshots.setdefault(sid, self.ws.snapshot())
            base = self.ws.snapshot()
        agents = [spec.agent] + ([spec.fallback_agent] if spec.fallback_agent else [])
        last_reason = ""
        for idx, agent_name in enumerate(agents):
            if idx > 0:
                self.metrics.mark(sid, "fallbacks")
                self.audit.emit("stage.fallback", stage=sid, agent=agent_name, after=last_reason)
            for attempt in range(1, spec.max_retries + 2):
                t0 = self.clock()
                feedback = list(self.ctx.feedback.get(sid, []))
                actx = AgentContext(spec, self.ctx, self.ws, attempt, feedback, self.run_dir,
                                    {**spec.params, **self.ctx.inputs.get(sid, {})})
                try:
                    self._inject_fault(sid, agent_name, attempt, primary=idx == 0)
                    result = self.agents[agent_name].run(actx)
                except TransientError as exc:
                    last_reason = f"transient: {exc}"
                    self._attempt_failed(spec, base, t0, last_reason,
                                         attempt, retryable=True)
                    continue
                except (AgentError, Exception) as exc:  # non-retryable for this agent -> fallback
                    last_reason = f"{type(exc).__name__}: {exc}"
                    self._attempt_failed(spec, base, t0, last_reason,
                                         attempt, retryable=False)
                    break
                report = self._evaluate_policy(spec, result, base)
                failed_checks = [k for k, v in result.checks.items() if not v and k in spec.exit_gates]
                missing_gates = [g for g in spec.exit_gates if g not in result.checks]
                if result.rework:  # reviewer verdict, not an execution failure
                    self.metrics.attempt(sid, self.clock() - t0, True)
                    return _Outcome(sid, True, result, report, agent=agent_name)
                if report.outcome == BLOCK or failed_checks or missing_gates:
                    last_reason = "; ".join(
                        [f"exit gate failed: {c}" for c in failed_checks] +
                        [f"exit gate not evaluated: {g}" for g in missing_gates] +
                        [f"policy: {f}" for f in report.feedback() if report.outcome == BLOCK])
                    for note in result.feedback + report.feedback():
                        self.ctx.add_feedback(sid, note)
                    self._attempt_failed(spec, base, t0, last_reason,
                                         attempt, retryable=True)
                    continue
                self.metrics.attempt(sid, self.clock() - t0, True)
                return _Outcome(sid, True, result, report, agent=agent_name)
        return _Outcome(sid, False, reason=last_reason or "all attempts exhausted")

    def _attempt_failed(self, spec: StageSpec, base: str | None, t0: float, reason: str, attempt: int,
                        retryable: bool) -> None:
        self.metrics.attempt(spec.id, self.clock() - t0, False)
        self._failures += 1
        self.audit.emit("stage.attempt_failed", stage=spec.id, attempt=attempt, reason=reason,
                        will_retry=retryable and attempt <= spec.max_retries)
        if base is not None:
            self.ws.rollback(base)
            self.metrics.mark(spec.id, "rollbacks")
            self.audit.emit("stage.rollback", stage=spec.id, to=base)
        if retryable and attempt <= spec.max_retries:
            self.metrics.mark(spec.id, "retries")
            self.sleep(self.cfg.backoff_base * (2 ** (attempt - 1)))

    def _inject_fault(self, sid: str, agent: str, attempt: int, primary: bool) -> None:
        fault = self.cfg.faults.get(sid, {})
        if primary and attempt <= fault.get("transient_failures", 0):
            raise TransientError(f"injected transient fault #{attempt} in {agent}")
        if primary and fault.get("agent_error"):
            raise AgentError(f"injected hard failure in {agent}")

    def _evaluate_policy(self, spec: StageSpec, result: StageResult, base: str | None) -> PolicyReport:
        groups = []
        # A non-mapping artifact is checked as empty, so policy BLOCKS it rather than crashing on it.
        if "requirements" in result.artifacts:
            req = result.artifacts["requirements"]
            groups.append(self.policy.check_requirements(req if isinstance(req, dict) else {}))
        if "design" in result.artifacts:
            design = result.artifacts["design"]
            groups.append(self.policy.check_design(design if isinstance(design, dict) else {}))
        if base is not None:
            changed, deleted, lines = self.ws.changes_since(base)
            impact = self.ctx.get("impact")
            scope = impact.content.get("allowed_scope") if impact and isinstance(impact.content, dict) else None
            groups.append(self.policy.check_changes(changed, deleted=deleted, allowed_scope=scope,
                                                    lines_changed=lines))
            groups.append(self.policy.scan_code(changed))
        report = self.policy.report(*groups)
        entry = {"stage": spec.id, **report.to_dict()}
        self.policy_log.append(entry)
        self.audit.emit("policy.evaluated", stage=spec.id, outcome=report.outcome,
                        violations=len(report.violations))
        return report

    # ================================================================ transitions (coordinator thread)
    def _handle(self, out: _Outcome) -> None:
        sid, spec = out.stage, self.graph.stages[out.stage]
        if not out.ok:
            self._fail(sid, out.reason)
            return
        result = out.result
        if result is None:  # explicit, unlike assert (stripped under python -O)
            raise RuntimeError(f"stage '{sid}' reported success without a result")
        if result.rework:
            self._rework(sid, result)
            return
        reasons = []
        if spec.requires_approval:
            reasons.append("stage requires human sign-off")
        if spec.impact == "high":
            reasons.append("high-impact stage")
        if out.policy and out.policy.outcome == ESCALATE:
            reasons.append("policy escalation: " + "; ".join(out.policy.feedback()))
        if result.escalate:
            reasons.append("agent escalation: " + result.escalate)
        if reasons:
            self._approve(sid, result, " | ".join(reasons), out.agent)
        else:
            self._commit(sid, result, out.agent)

    def _approve(self, sid: str, result: StageResult, reason: str, agent: str) -> None:
        rnd = self.approval_rounds.get(sid, 0) + 1
        checkpoint = f"{sid}#{rnd}"
        summary = {"summary": result.summary, "checks": result.checks,
                   "artifacts": sorted(result.artifacts), "decisions": result.decisions}
        decision = self.approvals.decide(ApprovalRequest(checkpoint, sid, reason, summary))
        record = {"checkpoint": checkpoint, "stage": sid, "reason": reason, "status": decision.status,
                  "approver": decision.approver, "comment": decision.comment, "amendments": decision.amendments}
        self.audit.emit("approval.decision", stage=sid, actor=decision.approver or "n/a",
                        **{k: v for k, v in record.items() if k != "stage"})
        if decision.status == PENDING:
            self.status[sid] = AWAITING
            self.pending[sid] = {"result": asdict(result), "agent": agent, "reason": reason}
            self.run_status = "PAUSED"
            self.reasons[sid] = f"awaiting human approval at {checkpoint}"
            self.audit.emit("run.paused", stage=sid, checkpoint=checkpoint)
            return
        self.approval_rounds[sid] = rnd
        self.approvals_log.append(record)
        if decision.status == APPROVED:
            self.ctx.decide(sid, f"Approved {checkpoint}", decision.comment or reason,
                            decided_by=decision.approver)
            self._commit(sid, result, agent)
        elif decision.status == AMEND:
            self._amend(sid, decision.amendments, decision.approver, decision.comment)
        else:
            self._rollback_stage(sid)
            self._fail(sid, f"rejected by {decision.approver}: {decision.comment}")

    def _commit(self, sid: str, result: StageResult, agent: str) -> None:
        spec = self.graph.stages[sid]
        derived = [a.ref for d in spec.depends_on for a in self._artifacts_of(d)]
        changed_outputs = []
        for name, content in result.artifacts.items():
            prev = self.ctx.get(name)
            art = self.ctx.put(name, content, produced_by=sid, derived_from=derived)
            self._write_artifact(art)
            if prev is not None and prev.hash != art.hash:
                changed_outputs.append(name)
        for d in result.decisions:
            self.ctx.decide(sid, d["summary"], d.get("rationale", ""), alternatives=d.get("alternatives"),
                            based_on=derived)
        commit = None
        if spec.mutates_workspace:
            commit = self.ws.commit(result.commit_message or f"chore({sid}): stage output")
            for tag in result.tags:  # irreversible actions happen only after gates + approval
                self.ws.git("tag", "-a", tag, "-m", f"{tag} released by orchestrator run {self.run_id}")
        self.status[sid] = SUCCEEDED
        self.ctx.feedback.pop(sid, None)
        self.audit.emit("stage.succeeded", stage=sid, agent=agent, summary=result.summary, commit=commit,
                        artifacts={n: a.ref for n in result.artifacts if (a := self.ctx.get(n)) is not None})
        for item in result.new_stages:
            self._insert_stage(sid, item)
        if changed_outputs:
            self._invalidate_descendants(sid, f"upstream outputs changed: {changed_outputs}")

    def _fail(self, sid: str, reason: str) -> None:
        self.status[sid] = FAILED
        self.reasons[sid] = reason
        self.audit.emit("stage.failed", stage=sid, reason=reason)
        if self.graph.stages[sid].critical:
            self._rollback_stage(sid)
            self._safe_stop(f"critical stage '{sid}' failed: {reason}")

    def _rework(self, sid: str, result: StageResult) -> None:
        target = result.rework
        if target is None:
            raise RuntimeError(f"stage '{sid}' requested rework without a target")
        count = self.rework_counts.get(target, 0) + 1
        for name, content in result.artifacts.items():  # keep the review report for lineage/audit
            self._write_artifact(self.ctx.put(name, content, produced_by=sid))
        if target not in self.graph.stages or count > self.cfg.max_rework:
            self._fail(sid, f"rework limit reached for '{target}' ({self.cfg.max_rework})")
            return
        self.rework_counts[target] = count
        self.metrics.mark(target, "reworks")
        for note in result.feedback:
            self.ctx.add_feedback(target, note)
        self.audit.emit("replan.rework", stage=sid, target=target, round=count, feedback=result.feedback)
        self._reset([target] + self.graph.descendants(target), f"rework requested by {sid}")

    def _amend(self, sid: str, amendments: dict[str, Any], approver: str, comment: str) -> None:
        target = amendments.get("stage", sid)
        inputs = amendments.get("inputs", {})
        self.ctx.inputs.setdefault(target, {}).update(inputs)
        self.ctx.decide(sid, f"Human amended inputs of '{target}'", comment, decided_by=approver,
                        alternatives=["proceed with agent assumptions"])
        self.audit.emit("replan.amend", stage=sid, actor=approver, target=target, inputs=inputs)
        self._rollback_stage(sid)
        affected = [target] + self.graph.descendants(target)
        if sid not in affected:
            affected.append(sid)
        self._reset(affected, f"upstream amendment by {approver}")

    def _invalidate_descendants(self, sid: str, reason: str) -> None:
        done = [d for d in self.graph.descendants(sid) if self.status[d] == SUCCEEDED]
        if done:
            self.audit.emit("replan.invalidate", stage=sid, invalidated=done, reason=reason)
            self._reset(done, reason)

    def _reset(self, stages: list[str], reason: str) -> None:
        mutating = [s for s in stages if self.graph.stages[s].mutates_workspace and s in self.snapshots]
        if mutating:  # roll the workspace back to before the earliest affected mutating stage
            order = self.graph.topological_order()
            first = min(mutating, key=order.index)
            self.ws.rollback(self.snapshots[first])
            for s in mutating:
                self.metrics.mark(s, "rollbacks")
                self.snapshots.pop(s, None)
            self.audit.emit("stage.rollback", stage=first, to="pre-stage snapshot", reason=reason)
        for s in stages:
            self.status[s] = PENDING_S
            self.reasons[s] = reason

    def _rollback_stage(self, sid: str) -> None:
        if self.graph.stages[sid].mutates_workspace and sid in self.snapshots:
            self.ws.rollback(self.snapshots.pop(sid))
            self.metrics.mark(sid, "rollbacks")
            self.audit.emit("stage.rollback", stage=sid, to="pre-stage snapshot")

    def _insert_stage(self, parent: str, item: dict[str, Any]) -> None:
        spec = StageSpec.from_dict({**item["spec"], "depends_on": item["spec"].get("depends_on", [parent])})
        if spec.agent not in self.agents:
            self.audit.emit("replan.insert_rejected", stage=parent, new=spec.id, reason="unknown agent")
            return
        try:
            self.graph.insert(spec, item.get("before", []))
        except ValueError as exc:
            self.audit.emit("replan.insert_rejected", stage=parent, new=spec.id, reason=str(exc))
            return
        self.status[spec.id] = PENDING_S
        for b in item.get("before", []):
            if self.status[b] == SUCCEEDED:
                self._reset([b] + self.graph.descendants(b), f"new upstream stage {spec.id}")
        self.audit.emit("replan.insert", stage=parent, new=spec.id, before=item.get("before", []))

    # ================================================================ persistence & resume
    def _artifacts_of(self, sid: str) -> list[Artifact]:
        return [h[-1] for h in self.ctx._artifacts.values() if h and h[-1].produced_by == sid]

    def _write_artifact(self, art: Artifact) -> None:
        folder = self.run_dir / "artifacts"
        folder.mkdir(exist_ok=True)
        stem = f"{art.name}.v{art.version}"
        content = art.content
        if isinstance(content, dict) and "markdown" in content:
            (folder / f"{stem}.md").write_text(content["markdown"])
            content = {k: v for k, v in content.items() if k != "markdown"}
        (folder / f"{stem}.json").write_text(json.dumps(
            {"ref": art.ref, "produced_by": art.produced_by, "derived_from": art.derived_from,
             "content": content}, indent=2, default=str))

    def persist(self) -> None:
        state = {"run_id": self.run_id, "run_status": self.run_status, "status": self.status,
                 "reasons": self.reasons, "approval_rounds": self.approval_rounds,
                 "rework_counts": self.rework_counts, "snapshots": self.snapshots, "pending": self.pending,
                 "approvals_log": self.approvals_log, "policy_log": self.policy_log,
                 "graph": [asdict(s) for s in self.graph.stages.values()], "metrics": self.metrics.summary()}
        (self.run_dir / "state.json").write_text(json.dumps(state, indent=2, default=str))
        (self.run_dir / "context.json").write_text(json.dumps(dump_context(self.ctx), indent=2, default=str))

    @classmethod
    def resume(cls, run_dir: Path, agents: dict[str, Agent], *, workspace: Workspace, approvals: ApprovalGateway,
               policy: PolicyEngine | None = None, config: EngineConfig | None = None, **kw: Any) -> Orchestrator:
        run_dir = Path(run_dir)
        state = json.loads((run_dir / "state.json").read_text())
        ctx = load_context(json.loads((run_dir / "context.json").read_text()))
        graph = StageGraph([StageSpec.from_dict(s) for s in state["graph"]])
        orch = cls(graph, agents, run_dir=run_dir, workspace=workspace, approvals=approvals, policy=policy,
                   config=config, context=ctx, run_id=state["run_id"], **kw)
        for key in ("reasons", "approval_rounds", "rework_counts", "snapshots", "approvals_log", "policy_log"):
            setattr(orch, key, state[key])
        stop_file = run_dir / "STOP"
        if stop_file.exists():
            stop_file.unlink()
        for sid, st in state["status"].items():
            orch.status[sid] = st if st in {SUCCEEDED, FAILED} else PENDING_S
        orch.audit.emit("run.resume", previous=state["run_status"])
        for sid, pend in state["pending"].items():  # re-ask the human for parked approvals
            result = StageResult(**pend["result"])
            orch._approve(sid, result, pend["reason"], pend["agent"])
            if orch.run_status == "PAUSED":
                break
        return orch
