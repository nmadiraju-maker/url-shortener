"""Agent unit tests for edge paths not exercised by the end-to-end scenarios."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sdlc.agents import development, qa
from sdlc.agents.base import AgentContext
from sdlc.agents.design import DesignAgent
from sdlc.agents.docs import DocsAgent, StaticDocsAgent, changelog
from sdlc.agents.release import ReleaseAgent
from sdlc.agents.requirements import RequirementsAgent, detect_ambiguities, match_features
from sdlc.agents.review import ReviewAgent, review_source
from sdlc.agents.security import SecurityAgent
from sdlc.context import ContextStore
from sdlc.errors import AgentError, TransientError
from sdlc.graph import StageSpec
from sdlc.workspace import Workspace


def actx(tmp_path, params=None, ctx=None, ws=None, feedback=None):
    return AgentContext(StageSpec("s", "a"), ctx or ContextStore(), ws or Workspace(tmp_path / "ws"), 1,
                        feedback or [], tmp_path, params or {})


def test_requirements_unresolved_and_free_text(tmp_path):
    text = "Make links safer and fast and with more stuff"
    assert {a["term"] for a in detect_ambiguities(text)} == {"safer", "fast", "more stuff"}
    r = RequirementsAgent().run(actx(tmp_path, {"requirement": text, "clarifications": {"fast": "p99 < 50ms"}}))
    req = r.artifacts["requirements"]
    assert not r.checks["no_unresolved_ambiguity"] and "more stuff" in r.escalate and "safer" in r.escalate
    assert {a["term"]: a["resolved_by"] for a in req["ambiguities"]}["fast"] == "human clarification"
    assert "## Ambiguities" in req["markdown"] and match_features("nothing relevant") == []


def test_requirements_regression_and_bug_kinds(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from sdlc.agents import catalog
    monkeypatch.setitem(catalog.FEATURES, "crashfix", {
        "ac_prefix": "CRASHFIX", "priority": "Must", "kind": "bug", "keywords": ["crash-17"],
        "story": ("user", "not see a crash", "the service is reliable"), "ac": [("x", "y", "z")]})
    r = RequirementsAgent().run(actx(tmp_path, {"requirement": "fix CRASH-17 and track analytics",
                                                "existing_features": ["analytics"]}))
    kinds = {s["feature"]: s["kind"] for s in r.artifacts["requirements"]["stories"]}
    assert kinds == {"analytics": "regression", "crashfix": "bug"} and r.escalate is None
    md = r.artifacts["requirements"]["markdown"]
    assert "regression only" in md and ", bug)" in md


def test_design_feature_adrs_and_no_migration_proposal(tmp_path):  # type: ignore[no-untyped-def]
    c = ContextStore()
    feats = ["max_clicks", "lookalike", "hourly"]
    c.put("requirements", {"title": "t", "features": feats,
                           "stories": [{"id": f"US-{i}", "feature": f} for i, f in enumerate(feats)]}, "r")
    r = DesignAgent().run(actx(tmp_path, {"propose_migration_stage": False}, ctx=c))
    assert r.new_stages == [] and len(r.decisions) == 3
    md = r.artifacts["design"]["markdown"]
    assert "Schema changes" in md and "links.max_clicks INTEGER NULL" in md


def test_development_patch_conflict_and_missing_plan(tmp_path):
    ws = Workspace(tmp_path / "ws")
    ws.write("a.py", "x = 1\nx = 1\n")
    with pytest.raises(AgentError, match="patch conflict"):
        development.apply_edits(ws, [{"file": "a.py", "find": "x = 1", "replace": "y"}])
    assert development.load_plan("does_not_exist", tmp_path) is None
    c = ContextStore()
    c.put("requirements", {"features": ["hourly"], "stories": [{"feature": "hourly", "kind": "feature"}]}, "r")
    with pytest.raises(AgentError, match="no change plan"):
        development.DevelopmentAgent().run(actx(tmp_path, {"mode": "patch", "changes_dir": str(tmp_path)}, c, ws))


def test_export_baseline_falls_back_to_working_tree(tmp_path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "m.py").write_text("x = 1\n")
    (src / "f.txt").write_text("hi")
    dest = tmp_path / "dest"
    dest.mkdir()
    assert development.export_baseline("v9", ["pkg", "f.txt", "missing"], dest, source=src) == \
        "directory (not a git repository)"
    assert (dest / "pkg" / "m.py").exists() and (dest / "f.txt").read_text() == "hi"


def test_compiles_detects_syntax_error(tmp_path):
    assert development._compiles({"a.py": "def (:", "b.txt": "x"}) is False
    assert development._compiles({"a.py": "x = 1\n"}) is True


def test_review_rules():
    src = '''
def f(a=[]):
    try:
        print(a)  # TODO remove
    except:
        pass
''' + "\n".join(f"    x{i} = {i}" for i in range(65))
    rules = {f["rule"] for f in review_source("urlshort/m.py", src)}
    assert rules == {"RV-001", "RV-002", "RV-003", "RV-004", "RV-006", "RV-007", "RV-008"}
    assert review_source("tests/t.py", "print(1)\n") == []


def test_review_requires_tests_for_source_changes(tmp_path):
    ws = Workspace(tmp_path / "ws")
    base = ws.head()
    ws.write("urlshort/m.py", '"""doc."""\nx = 1\n')
    c = ContextStore()
    c.put("code_change", {"base": base, "commits": ["feat: x"]}, "dev")
    r = ReviewAgent().run(actx(tmp_path, ctx=c, ws=ws))
    assert r.rework == "development" and "RV-005" in r.feedback[0]


def test_qa_parse_junit_testsuites_root(tmp_path):
    p = tmp_path / "j.xml"
    p.write_text('<testsuites><testsuite tests="2" failures="1" errors="0" skipped="0">'
                 '<testcase classname="t" name="test_a[1]"/><testcase classname="t" name="test_a[2]">'
                 '<failure/></testcase></testsuite></testsuites>')
    res = qa.parse_junit(p)
    assert res["cases"] == {"test_a": False} and res["failed"] == ["t::test_a[2]"]


def test_qa_timeout_and_missing_results_are_transient(tmp_path, monkeypatch):
    c = ContextStore()
    c.put("requirements", {"stories": []}, "r")
    context = actx(tmp_path, ctx=c)  # create the git workspace before patching subprocess

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("pytest", 1)
    monkeypatch.setattr(qa.subprocess, "run", timeout)
    with pytest.raises(TransientError, match="timed out"):
        qa.QAAgent().run(context)
    monkeypatch.setattr(qa.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 2, "", "boom"))
    with pytest.raises(TransientError, match="no results"):
        qa.QAAgent().run(context)


def test_security_flags_unpinned_dependencies_and_violations(tmp_path):
    ws = Workspace(tmp_path / "ws")
    ws.write("requirements.txt", "# deps\nfastapi>=0.1\nrequests\n\n")
    ws.write("app.py", "eval('1')\n")
    r = SecurityAgent().run(actx(tmp_path, ws=ws))
    assert not r.checks["no_blocking_findings"] and not r.checks["dependencies_pinned"]
    assert "unpinned dependency 'requests'" in r.feedback


def test_security_without_requirements_file(tmp_path):
    r = SecurityAgent().run(actx(tmp_path))
    assert r.checks == {"no_blocking_findings": True, "dependencies_pinned": True}


def test_docs_agent_failure_and_static_fallback(tmp_path):
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(AgentError, match="OpenAPI generation failed"):
        DocsAgent().run(actx(tmp_path, ws=ws))
    ws.write("urlshort/api.py", "@app.get('/x')\ndef h():\n    return 1\n")
    ws.commit("feat: x\n\nbody")
    ws.commit("noop")
    r = StaticDocsAgent().run(actx(tmp_path, ws=ws))
    assert r.artifacts["docs"]["paths"] == ["GET /x"] and "## Features" in changelog(ws)


def test_release_not_ready_when_artifacts_missing(tmp_path):
    c = ContextStore()
    c.put("requirements", {"stories": []}, "r")
    r = ReleaseAgent().run(actx(tmp_path, {"version": "9.9.9"}, ctx=c))
    assert not r.checks["all_gates_green"] and r.tags == ["v9.9.9"]
    assert "review_report" in r.artifacts["release"]["missing"] and Path(tmp_path / "ws" / "VERSION").exists()
