"""Unit tests: graph, context/lineage, audit log, metrics, approvals, workspace."""
from __future__ import annotations

import json

import pytest

from sdlc.approvals import (
    AMEND,
    APPROVED,
    PENDING,
    REJECTED,
    ApprovalRequest,
    DecisionFileApprovals,
    InteractiveApprovals,
)
from sdlc.audit import AuditLog
from sdlc.context import ContextStore, dump_context, load_context
from sdlc.errors import GraphError
from sdlc.graph import StageGraph, StageSpec
from sdlc.metrics import Metrics
from sdlc.workspace import Workspace


def S(id, deps=(), **kw):
    return StageSpec(id=id, agent="a", depends_on=list(deps), **kw)


# ---------------- graph
def test_layers_topology_descendants_and_mermaid():
    g = StageGraph([S("req"), S("design", ["req"]), S("qa", ["design"]), S("sec", ["design"]),
                    S("rel", ["qa", "sec"], requires_approval=True)])
    assert g.layers() == [["req"], ["design"], ["qa", "sec"], ["rel"]]
    assert g.descendants("design") == ["qa", "sec", "rel"]
    mm = g.to_mermaid({"req": "SUCCEEDED", "qa": "FAILED", "rel": "RUNNING"})
    assert "rel 🔒" in mm and "style req fill:#d4edda" in mm and "style rel" not in mm


@pytest.mark.parametrize("stages,msg", [
    ([S("a"), S("a")], "duplicate"), ([S("a", ["x"])], "unknown"), ([S("a", ["b"]), S("b", ["a"])], "cycle")])
def test_graph_validation(stages, msg):
    with pytest.raises(GraphError, match=msg):
        StageGraph(stages)


def test_insert_wires_and_rejects_bad_inserts():
    g = StageGraph([S("design"), S("dev", ["design"])])
    g.insert(S("mig", ["design"]), before=["dev"])
    assert "mig" in g.stages["dev"].depends_on
    with pytest.raises(GraphError, match="duplicate"):
        g.insert(S("mig"), before=[])
    with pytest.raises(GraphError, match="unknown"):
        g.insert(S("x"), before=["nope"])
    assert "x" not in g.stages
    with pytest.raises(GraphError, match="cycle"):
        g.insert(S("y", ["dev"]), before=["design"])
    assert "y" not in g.stages and "y" not in g.stages["design"].depends_on


# ---------------- context
def test_context_versions_lineage_and_roundtrip():
    c = ContextStore()
    req = c.put("req", {"a": 1}, "requirements")
    c.put("req", {"a": 2}, "requirements")
    design = c.put("design", {"d": 1}, "design", derived_from=[c.get("req").ref])
    c.put("code", "diff", "dev", derived_from=[design.ref, "external@x"])
    assert c.get("req").version == 2 and req.version == 1 and len(c.history("req")) == 2
    assert c.lineage("code")[0].startswith("code@") and c.lineage("code")[1].strip().startswith("design@")
    assert c.lineage("missing") == [] and c.require("design") == {"d": 1} and c.names() == ["code", "design", "req"]
    with pytest.raises(KeyError):
        c.require("nope")
    c.decide("design", "use X", "because", alternatives=["Y"])
    c.add_feedback("dev", "fix it")
    clone = load_context(json.loads(json.dumps(dump_context(c))))
    assert clone.get("req").content == {"a": 2} and clone.decisions[0].alternatives == ["Y"]
    assert clone.feedback == {"dev": ["fix it"]} and "artifacts" in c.snapshot()


# ---------------- audit
def test_audit_chain_verify_tamper_and_reopen(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl", "r1")
    assert log.records() == [] and log.verify()
    log.emit("x", stage="s", k=1)
    log.emit("y")
    assert AuditLog(tmp_path / "a.jsonl", "r1")._prev == log.records()[-1]["hash"]
    AuditLog(tmp_path / "a.jsonl", "r1").emit("z")
    assert AuditLog(tmp_path / "a.jsonl", "r1").verify()
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    rec = json.loads(lines[0])
    rec["data"]["k"] = 2
    (tmp_path / "a.jsonl").write_text("\n".join([json.dumps(rec)] + lines[1:]) + "\n")
    assert not AuditLog(tmp_path / "a.jsonl", "r1").verify()
    rec2 = json.loads(lines[1])
    rec2["prev_hash"] = "bad"
    (tmp_path / "a.jsonl").write_text("\n".join([lines[0], json.dumps(rec2)]) + "\n")
    assert not AuditLog(tmp_path / "a.jsonl", "r1").verify()


def test_audit_empty_existing_file(tmp_path):
    (tmp_path / "e.jsonl").write_text("")
    assert AuditLog(tmp_path / "e.jsonl", "r").emit("x")["prev_hash"] == "0" * 64


# ---------------- metrics
def test_metrics_mttr_and_frequencies():
    now = [0.0]
    m = Metrics(lambda: now[0])
    assert m.summary()["attempt_success_rate"] is None and m.summary()["mttr_seconds"] is None
    now[0] = 10
    m.attempt("qa", 2.0, False)
    m.mark("qa", "retries")
    m.mark("qa", "rollbacks")
    m.attempt("qa", 1.0, False)
    now[0] = 15
    m.attempt("qa", 1.0, True)
    m.incr("x")
    m.finish()
    s = m.summary()
    assert s["mttr_seconds"] == 7.0 and s["attempt_success_rate"] == 0.333 and s["retries"] == 1
    assert s["rollback_frequency"] == 0.333 and s["end_to_end_latency_seconds"] == 15 and s["counters"] == {"x": 1}


# ---------------- approvals
REQ = ApprovalRequest("design#1", "design", "why", {"summary": "s"})


def test_decision_file_approvals(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"design#1": {"status": "approved", "approver": "jane"},
                             "release#*": {"status": "rejected", "approver": "raj", "comment": "no"},
                             "qa#1": {"status": "approved"}, "x#1": {"status": "weird", "approver": "a"},
                             "y#1": {"status": "amend", "approver": "p", "amendments": {"stage": "req"}}}))
    gw = DecisionFileApprovals(p)
    assert gw.decide(REQ).status == APPROVED
    assert gw.decide(ApprovalRequest("release#3", "release", "", {})).status == REJECTED
    assert gw.decide(ApprovalRequest("qa#1", "qa", "", {})).status == PENDING  # no named approver
    assert gw.decide(ApprovalRequest("x#1", "x", "", {})).status == PENDING
    assert gw.decide(ApprovalRequest("y#1", "y", "", {})).amendments == {"stage": "req"}
    assert gw.decide(ApprovalRequest("z#1", "z", "", {})).status == PENDING
    assert DecisionFileApprovals(None).decide(REQ).status == PENDING


@pytest.mark.parametrize("answers,status", [(["y"], APPROVED), (["n", "too risky"], REJECTED), (["p"], PENDING)])
def test_interactive_approvals(answers, status, capsys):
    it = iter(answers)
    d = InteractiveApprovals(ask=lambda _: next(it), approver="me").decide(REQ)
    assert d.status == status and "APPROVAL REQUIRED" in capsys.readouterr().out
    assert AMEND == "amend"


# ---------------- workspace
def test_workspace_snapshot_changes_rollback(tmp_path):
    ws = Workspace(tmp_path / "w")
    Workspace(tmp_path / "w")  # re-open existing repo is a no-op
    ws.write("a/x.py", "print(1)\n")
    ws.write("tests/t.py", "x = 1\n")
    base = ws.snapshot()
    assert ws.snapshot() == base  # clean tree -> no new commit
    ws.write("a/x.py", "print(2)\nprint(3)\n")
    ws.write("docs/generated/big.json", "{}\n" * 50)
    (ws.root / "tests/t.py").unlink()
    changed, deleted, lines = ws.changes_since(base)
    assert set(changed) == {"a/x.py", "docs/generated/big.json"} and deleted == ["tests/t.py"] and lines == 4
    assert ws.commit("feat: change") and ws.commit("noop") is None
    ws.rollback(base)
    assert ws.read("a/x.py") == "print(1)\n" and ws.exists("tests/t.py") and not ws.exists("docs/generated/big.json")
    assert set(ws.python_files()) == {"a/x.py", "tests/t.py"}
    with pytest.raises(RuntimeError):
        ws.git("checkout", "does-not-exist")


def test_git_is_resolved_to_an_absolute_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from sdlc import workspace
    workspace.git_executable.cache_clear()
    assert workspace.git_executable().startswith("/")
    workspace.git_executable.cache_clear()
    monkeypatch.setattr(workspace.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="git is required"):
        workspace.git_executable()
    workspace.git_executable.cache_clear()


def test_workspace_ignores_build_byproducts_and_tolerates_binary_changes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    ws = Workspace(tmp_path / "w")
    base = ws.snapshot()
    (ws.root / "pkg" / "__pycache__").mkdir(parents=True)
    (ws.root / "pkg" / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"\xcb\x0d\x0d\x0a")
    ws.write(".coverage", "x")
    (ws.root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xcb\xff")
    changed, deleted, _ = ws.changes_since(base)
    assert changed == {"logo.png": ""} and deleted == []          # byproducts excluded; binary listed, no crash
