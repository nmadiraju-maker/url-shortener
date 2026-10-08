"""Engine control-flow tests: gates, retries, fallback, rollback, approvals, re-planning, safe-stop, resume."""
from __future__ import annotations

import json
import time

import pytest

from sdlc.agents.base import StageResult
from sdlc.approvals import AMEND, APPROVED, REJECTED, ApprovalDecision
from sdlc.engine import EngineConfig, Orchestrator
from sdlc.errors import AgentError, TransientError
from sdlc.workspace import Workspace

from .conftest import DictApprovals, FnAgent, ok

A = ApprovalDecision


def test_parallel_success_lineage_and_persistence(build):
    agents = {"req": FnAgent("req", ok("reqs")), "a": FnAgent("a", ok("qa_out")),
              "b": FnAgent("b", ok("sec_out", decisions=[{"summary": "D", "rationale": "R"}]))}
    orch = build([{"id": "req", "agent": "req"},
                  {"id": "qa", "agent": "a", "depends_on": ["req"], "entry_gates": ["reqs"]},
                  {"id": "sec", "agent": "b", "depends_on": ["req"]}], agents)
    assert orch.run() == "COMPLETED"
    assert orch.ctx.get("qa_out").derived_from == [orch.ctx.get("reqs").ref]
    assert orch.ctx.decisions[0].summary == "D" and orch.audit.verify()
    state = json.loads((orch.run_dir / "state.json").read_text())
    assert state["run_status"] == "COMPLETED" and (orch.run_dir / "artifacts" / "qa_out.v1.json").exists()


def test_transient_retry_then_success_records_mttr(build):
    def flaky(ctx, n):
        if n == 1:
            raise TransientError("runner down")
        return StageResult(summary="ok")
    orch = build([{"id": "qa", "agent": "qa", "max_retries": 2}], {"qa": FnAgent("qa", flaky)})
    assert orch.run() == "COMPLETED"
    s = orch.metrics.summary()
    assert s["retries"] == 1 and s["incidents_recovered"] == 1 and s["mttr_seconds"] is not None


def test_transient_exhausted_then_fallback(build):
    def always(ctx, n):
        raise TransientError("down")
    orch = build([{"id": "docs", "agent": "p", "fallback_agent": "f", "max_retries": 1}],
                 {"p": FnAgent("p", always), "f": FnAgent("f", ok("docs"))})
    assert orch.run() == "COMPLETED" and orch.metrics.summary()["fallbacks"] == 1


def test_fault_injection_agent_error_uses_fallback(build):
    orch = build([{"id": "docs", "agent": "p", "fallback_agent": "f"}],
                 {"p": FnAgent("p", ok()), "f": FnAgent("f", ok())}, faults={"docs": {"agent_error": True}})
    assert orch.run() == "COMPLETED" and orch.agents["p"].calls == 0 and orch.agents["f"].calls == 1


def test_critical_failure_safe_stops_and_skips_downstream(build):
    def boom(ctx, n):
        raise AgentError("broken")
    orch = build([{"id": "dev", "agent": "d"}, {"id": "qa", "agent": "q", "depends_on": ["dev"]}],
                 {"d": FnAgent("d", boom), "q": FnAgent("q", ok())})
    assert orch.run() == "STOPPED"
    assert orch.status == {"dev": "FAILED", "qa": "SKIPPED"} and "broken" in orch.reasons["dev"]


def test_non_critical_failure_blocks_downstream(build):
    def boom(ctx, n):
        raise ValueError("bug")
    orch = build([{"id": "docs", "agent": "d", "critical": False}, {"id": "rel", "agent": "r", "depends_on": ["docs"]},
                  {"id": "other", "agent": "r"}], {"d": FnAgent("d", boom), "r": FnAgent("r", ok())})
    assert orch.run() == "FAILED"
    assert orch.status == {"docs": "FAILED", "rel": "BLOCKED", "other": "SUCCEEDED"}


def test_exit_gate_failure_retries_with_feedback(build):
    agent = FnAgent("q", lambda ctx, n: StageResult(summary="x", checks={"tests_pass": n > 1}, feedback=["fix test"]))
    orch = build([{"id": "qa", "agent": "q", "exit_gates": ["tests_pass"], "max_retries": 1}], {"q": agent})
    assert orch.run() == "COMPLETED" and agent.contexts[1].feedback == ["fix test"]


def test_missing_exit_gate_evidence_fails(build):
    orch = build([{"id": "qa", "agent": "q", "exit_gates": ["tests_pass"], "max_retries": 0}],
                 {"q": FnAgent("q", ok())})
    assert orch.run() == "STOPPED" and "not evaluated" in orch.reasons["qa"]


def test_entry_gate_missing_artifact(build):
    orch = build([{"id": "dev", "agent": "d", "entry_gates": ["design"]}], {"d": FnAgent("d", ok())})
    assert orch.run() == "STOPPED" and "entry gate" in orch.reasons["dev"]


def test_policy_block_rolls_back_workspace_then_retry_succeeds(build):
    def write(ctx, n):
        code = "x = eval('1')\n" if n == 1 else "x = 1\n"
        ctx.workspace.write("app/mod.py", code)
        return StageResult(summary="w", commit_message="feat: mod")
    orch = build([{"id": "dev", "agent": "d", "mutates_workspace": True, "max_retries": 1}],
                 {"d": FnAgent("d", write)})
    assert orch.run() == "COMPLETED"
    assert orch.ws.read("app/mod.py") == "x = 1\n" and orch.metrics.summary()["rollbacks"] == 1
    assert orch.policy_log[0]["outcome"] == "block" and "SEC-001" in orch.ctx.feedback.get("dev", ["SEC-001"])[0]


def test_escalation_approved_then_tag_applied(build):
    def release(ctx, n):
        ctx.workspace.write("VERSION", "1.0.0\n")
        return StageResult(summary="rc", tags=["v1.0.0"], escalate="tagging")
    orch = build([{"id": "rel", "agent": "r", "mutates_workspace": True, "impact": "high"}],
                 {"r": FnAgent("r", release)}, DictApprovals({"rel#1": A(APPROVED, "raj", "ship")}))
    assert orch.run() == "COMPLETED"
    assert orch.ws.git("tag") == "v1.0.0" and orch.approvals_log[0]["approver"] == "raj"
    assert "agent escalation" in orch.approvals_log[0]["reason"]


def test_rejection_rolls_back_and_fails(build):
    def write(ctx, n):
        ctx.workspace.write("f.txt", "x")
        return StageResult(summary="w")
    orch = build([{"id": "dev", "agent": "d", "mutates_workspace": True, "requires_approval": True}],
                 {"d": FnAgent("d", write)}, DictApprovals({"dev#1": A(REJECTED, "jane", "no")}))
    assert orch.run() == "STOPPED" and not orch.ws.exists("f.txt") and "rejected by jane" in orch.reasons["dev"]


def test_pending_pauses_then_resume_completes(build, tmp_path):
    specs = [{"id": "design", "agent": "d", "requires_approval": True},
             {"id": "dev", "agent": "v", "depends_on": ["design"]}]
    agents = {"d": FnAgent("d", ok("design_doc")), "v": FnAgent("v", ok("code"))}
    orch = build(specs, agents)
    assert orch.run() == "PAUSED" and orch.status["design"] == "AWAITING_APPROVAL"
    (orch.run_dir / "STOP").write_text("left over")
    still = Orchestrator.resume(orch.run_dir, agents, workspace=Workspace(tmp_path / "ws"), approvals=DictApprovals())
    assert still.run() == "PAUSED"
    resumed = Orchestrator.resume(orch.run_dir, agents, workspace=Workspace(tmp_path / "ws"),
                                  approvals=DictApprovals({"design#1": A(APPROVED, "jane")}))
    assert resumed.run() == "COMPLETED" and resumed.ctx.has("code") and resumed.audit.verify()


def test_amendment_replans_upstream_and_reruns(build):
    req = FnAgent("r", lambda ctx, n: StageResult(summary="r", artifacts={"reqs": {"c": ctx.params.get("c")}}))
    design = FnAgent("d", lambda ctx, n: StageResult(
        summary="d", artifacts={"design_doc": {"from": ctx.context.require("reqs")}}))
    approvals = DictApprovals({"design#1": A(AMEND, "sam", "rescope", {"stage": "req", "inputs": {"c": "new"}}),
                               "design#2": A(APPROVED, "sam")})
    orch = build([{"id": "req", "agent": "r"}, {"id": "design", "agent": "d", "depends_on": ["req"],
                                                "requires_approval": True}], {"r": req, "d": design}, approvals)
    assert orch.run() == "COMPLETED"
    assert req.calls == 2 and orch.ctx.get("design_doc").content == {"from": {"c": "new"}}
    assert any(r["event"] == "replan.amend" for r in orch.audit.records())


def test_amend_on_own_stage(build):
    agent = FnAgent("r", lambda ctx, n: StageResult(summary="r", artifacts={"req": {"v": ctx.params.get("v")}}))
    orch = build([{"id": "req", "agent": "r", "requires_approval": True}], {"r": agent},
                 DictApprovals({"req#1": A(AMEND, "p", "", {"inputs": {"v": 2}}), "req#2": A(APPROVED, "p")}))
    assert orch.run() == "COMPLETED" and orch.ctx.get("req").content == {"v": 2}


def _dev_review(build, reviews_needed: int, max_rework: int = 2):
    def dev(ctx, n):
        ctx.workspace.write("src.py", f"v = {n}\n")
        return StageResult(summary="dev", artifacts={"code_change": {"n": n}})
    rev = FnAgent("rv", lambda ctx, n: StageResult(summary="rv", artifacts={"review_report": {"round": n}},
                                                   rework="dev" if n <= reviews_needed else None,
                                                   feedback=["RV-001 fix"], checks={"no_high_findings": True}))
    return build([{"id": "dev", "agent": "dv", "mutates_workspace": True},
                  {"id": "review", "agent": "rv", "depends_on": ["dev"]},
                  {"id": "qa", "agent": "q", "depends_on": ["review"]}],
                 {"dv": FnAgent("dv", dev), "rv": rev, "q": FnAgent("q", ok())}, max_rework=max_rework)


def test_rework_loop_then_approval(build):
    orch = _dev_review(build, reviews_needed=1)
    assert orch.run() == "COMPLETED"
    assert orch.ws.read("src.py") == "v = 2\n" and orch.ctx.get("review_report").version == 2
    assert orch.agents["dv"].contexts[1].feedback == ["RV-001 fix"] and orch.metrics.summary()["reworks"] == 1


def test_rework_limit_fails(build):
    orch = _dev_review(build, reviews_needed=5, max_rework=1)
    assert orch.run() == "STOPPED" and "rework limit" in orch.reasons["review"]


def test_dynamic_stage_insertion(build):
    proposals = [{"spec": {"id": "mig", "agent": "m"}, "before": ["dev"]},
                 {"spec": {"id": "bad", "agent": "nope"}, "before": ["dev"]},
                 {"spec": {"id": "cyc", "agent": "m", "depends_on": ["dev"]}, "before": ["design"]}]
    orch = build([{"id": "design", "agent": "d"}, {"id": "dev", "agent": "v", "depends_on": ["design"]}],
                 {"d": FnAgent("d", ok("design_doc", new_stages=proposals)), "v": FnAgent("v", ok("code")),
                  "m": FnAgent("m", ok("migration"))})
    assert orch.run() == "COMPLETED" and orch.status["mig"] == "SUCCEEDED" and "bad" not in orch.status
    events = [r["event"] for r in orch.audit.records()]
    assert events.count("replan.insert_rejected") == 2 and "replan.insert" in events


def test_insertion_before_completed_stage_resets_it(build):
    proposal = [{"spec": {"id": "extra", "agent": "e", "depends_on": []}, "before": ["b"]}]
    c = FnAgent("c", lambda ctx, n: StageResult(summary="c", new_stages=proposal if n == 1 else []))
    b = FnAgent("b", ok("b_out"))
    orch = build([{"id": "b", "agent": "b"}, {"id": "c", "agent": "c", "depends_on": ["b"]}],
                 {"b": b, "c": c, "e": FnAgent("e", ok("extra"))})
    assert orch.run() == "COMPLETED"
    assert b.calls == 2 and c.calls == 2 and orch.graph.stages["b"].depends_on == ["extra"]


def test_changed_output_invalidates_completed_descendants(build):
    orch = build([{"id": "a", "agent": "a"}, {"id": "b", "agent": "b", "depends_on": ["a"]}],
                 {"a": FnAgent("a", ok("x")), "b": FnAgent("b", ok("y"))})
    orch.run()
    orch.status["a"] = "PENDING"
    orch._commit("a", StageResult(summary="new", artifacts={"x": {"changed": True}}), "a")
    assert orch.status["b"] == "PENDING" and any(r["event"] == "replan.invalidate" for r in orch.audit.records())


def test_kill_switch_safe_stop(build):
    def slow(ctx, n):
        (ctx.run_dir / "STOP").write_text("operator")
        return StageResult(summary="s")
    orch = build([{"id": "a", "agent": "a"}, {"id": "b", "agent": "b", "depends_on": ["a"]}],
                 {"a": FnAgent("a", slow), "b": FnAgent("b", ok())})
    assert orch.run() == "STOPPED" and orch.status["b"] == "SKIPPED" and "kill switch" in orch.reasons["b"]
    orch.request_stop("again")
    assert (orch.run_dir / "STOP").read_text() == "again"


@pytest.mark.parametrize("cfg,reason", [({"max_run_seconds": -1}, "wall-clock"),
                                        ({"failure_budget": 0}, "failure budget")])
def test_budgets_safe_stop(build, cfg, reason):
    orch = build([{"id": "a", "agent": "a"}], {"a": FnAgent("a", ok())}, **cfg)
    assert orch.run() == "STOPPED" and reason in orch.reasons["a"]


def test_pause_drains_running_mutating_stage(build):
    def slow_write(ctx, n):
        time.sleep(0.3)
        ctx.workspace.write("w.txt", "x")
        return StageResult(summary="w")
    orch = build([{"id": "fast", "agent": "f", "requires_approval": True},
                  {"id": "slow", "agent": "s", "mutates_workspace": True}],
                 {"f": FnAgent("f", ok()), "s": FnAgent("s", slow_write)})
    assert orch.run() == "PAUSED" and orch.status["slow"] == "PENDING" and not orch.ws.exists("w.txt")


def test_unknown_agent_rejected(build):
    with pytest.raises(ValueError, match="unknown agent"):
        build([{"id": "a", "agent": "ghost"}], {})


def test_default_engine_config():
    assert EngineConfig().max_rework == 2


def test_injected_transient_fault_then_recovery(build):
    orch = build([{"id": "qa", "agent": "q", "max_retries": 1}], {"q": FnAgent("q", ok())},
                 faults={"qa": {"transient_failures": 1}})
    assert orch.run() == "COMPLETED" and orch.agents["q"].calls == 1 and orch.metrics.summary()["retries"] == 1


def test_engine_enforces_document_compliance(build):
    bad_req = FnAgent("r", lambda ctx, n: StageResult(summary="r", artifacts={"requirements": {"stories": []}}))
    bad_design = FnAgent("d", lambda ctx, n: StageResult(summary="d", artifacts={"design": {"api": []}}))
    orch = build([{"id": "req", "agent": "r", "max_retries": 0, "critical": False},
                  {"id": "design", "agent": "d", "max_retries": 0, "critical": False}], {"r": bad_req, "d": bad_design})
    assert orch.run() == "FAILED"
    assert "CMP-002" in orch.reasons["req"] and "CMP-003" in orch.reasons["design"]


def test_policy_escalation_requires_approval_and_writes_markdown_artifact(build):
    def touch_protected(ctx, n):
        ctx.workspace.write("pyproject.toml", "[project]\n")
        return StageResult(summary="deps", artifacts={"note": {"markdown": "# Note\n", "k": 1}})
    orch = build([{"id": "dev", "agent": "d", "mutates_workspace": True}], {"d": FnAgent("d", touch_protected)},
                 DictApprovals({"dev#1": A(APPROVED, "jane", "deps ok")}))
    assert orch.run() == "COMPLETED" and orch.approvals_log[0]["reason"].startswith("policy escalation: [CHG-002]")
    art = orch.run_dir / "artifacts"
    assert (art / "note.v1.md").read_text() == "# Note\n"
    assert "markdown" not in json.loads((art / "note.v1.json").read_text())["content"]


def test_amendment_targeting_unrelated_stage_also_reruns_approver_stage(build):
    other = FnAgent("o", lambda ctx, n: StageResult(summary="o", artifacts={"o": {"v": ctx.params.get("v")}}))
    gate = FnAgent("g", ok("g"))
    orch = build([{"id": "other", "agent": "o"}, {"id": "gate", "agent": "g", "requires_approval": True}],
                 {"o": other, "g": gate},
                 DictApprovals({"gate#1": A(AMEND, "p", "", {"stage": "other", "inputs": {"v": 1}}),
                                "gate#2": A(APPROVED, "p")}))
    assert orch.run() == "COMPLETED" and gate.calls == 2 and orch.ctx.get("o").content == {"v": 1}


def test_pause_drains_running_read_only_stage(build):
    def slow(ctx, n):
        time.sleep(0.3)
        return StageResult(summary="s")
    orch = build([{"id": "fast", "agent": "f", "requires_approval": True}, {"id": "slow", "agent": "s"}],
                 {"f": FnAgent("f", ok()), "s": FnAgent("s", slow)})
    assert orch.run() == "PAUSED" and orch.status["slow"] == "PENDING"


def test_malformed_outcomes_raise_explicit_errors(build):  # type: ignore[no-untyped-def]
    """Guards that replaced asserts: they must fire even under `python -O`."""
    from sdlc.engine import _Outcome
    orch = build([{"id": "a", "agent": "a"}], {"a": FnAgent("a", ok())})
    with pytest.raises(RuntimeError, match="without a result"):
        orch._handle(_Outcome("a", True, result=None))
    with pytest.raises(RuntimeError, match="without a target"):
        orch._rework("a", StageResult(summary="x"))


def test_non_mapping_requirements_artifact_is_blocked_not_crashed(build):  # type: ignore[no-untyped-def]
    agent = FnAgent("r", lambda ctx, n: StageResult(summary="r", artifacts={"requirements": "just a string",
                                                                            "design": ["not", "a", "dict"]}))
    orch = build([{"id": "req", "agent": "r", "max_retries": 0, "critical": False}], {"r": agent})
    assert orch.run() == "FAILED"
    assert "CMP-002" in orch.reasons["req"] and "CMP-003" in orch.reasons["req"]
