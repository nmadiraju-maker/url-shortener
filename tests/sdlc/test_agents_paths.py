"""Agent paths a regression-only pipeline does not take: change plans, flawed drafts, materialised
baselines, migrations, review rounds, and defensive checks on malformed inputs."""
import subprocess
from pathlib import Path

import pytest

from sdlc.agents import development, qa
from sdlc.agents.base import AgentContext, StageResult
from sdlc.agents.design import DesignAgent, MigrationAgent
from sdlc.agents.impact import ImpactAnalysisAgent
from sdlc.agents.review import ReviewAgent
from sdlc.context import ContextStore
from sdlc.errors import AgentError, TransientError
from sdlc.graph import StageSpec
from sdlc.workspace import Workspace


def actx(tmp_path: Path, params: dict[str, object] | None = None, ctx: ContextStore | None = None,
         ws: Workspace | None = None, feedback: list[str] | None = None) -> AgentContext:
    return AgentContext(StageSpec("s", "a"), ctx or ContextStore(), ws or Workspace(tmp_path / "ws"), 1,
                        feedback or [], tmp_path, dict(params or {}))


def requirements(*features: str, kinds: dict[str, str] | None = None) -> ContextStore:
    c = ContextStore()
    stories = [{"id": f"US-{i:02d}", "feature": f, "kind": (kinds or {}).get(f, "feature"), "i_want": f"have {f}",
                "acceptance_criteria": [{"id": f"AC-{f.upper()}-1"}]} for i, f in enumerate(features, 1)]
    c.put("requirements", {"title": "t", "features": list(features), "stories": stories}, "requirements")
    return c


# ---------------------------------------------------------------- development: change plans
PLAN = '''KIND, SCOPE = "feat", "links"
EDITS = [
    {"file": "app.py", "find": "LIMIT = 1\\n", "replace": "LIMIT = 2\\n"},
    {"file": "tests/test_app.py", "create": "def test_limit():\\n    assert True\\n"},
]
DEFECTS = {"swallow": [{"file": "app.py", "find": "LIMIT = 2\\n",
                        "replace": "try:\\n    LIMIT = 2\\nexcept Exception:\\n    pass\\n"}]}
'''


def plan_workspace(tmp_path: Path) -> tuple[Workspace, Path]:
    ws = Workspace(tmp_path / "ws")
    ws.write("app.py", "LIMIT = 1\n")
    ws.commit("chore: baseline")
    changes = tmp_path / "changes"
    changes.mkdir()
    (changes / "max_clicks.py").write_text(PLAN)
    return ws, changes


def test_patch_mode_applies_plan_and_writes_story_commit(tmp_path: Path) -> None:
    ws, changes = plan_workspace(tmp_path)
    agent = development.DevelopmentAgent()
    r = agent.run(actx(tmp_path, {"mode": "patch", "changes_dir": str(changes)}, requirements("max_clicks"), ws,
                       feedback=["RV-001 app.py swallowed exception"]))
    change = r.artifacts["code_change"]
    assert ws.read("app.py") == "LIMIT = 2\n" and ws.exists("tests/test_app.py") and r.checks["compiles"]
    assert change["commits"] == ["feat(links): have max_clicks (US-01)"]
    assert change["files"] == ["app.py", "tests/test_app.py"]
    body = ws.git("log", "-1", "--format=%B")
    assert "Acceptance criteria: AC-MAX_CLICKS-1" in body and "Addresses review feedback:" in body
    assert "requirements@" in body


def test_recorded_flawed_draft_is_replayed_on_the_configured_invocation(tmp_path: Path) -> None:
    ws, changes = plan_workspace(tmp_path)
    r = development.DevelopmentAgent().run(actx(tmp_path, {"mode": "patch", "changes_dir": str(changes),
                                                           "draft_defects": {"1": ["max_clicks:swallow",
                                                                                   "hourly:other-feature"]}},
                                                requirements("max_clicks"), ws))
    assert "except Exception:" in ws.read("app.py")
    assert r.artifacts["code_change"]["notes"] == ["replayed recorded first-draft defect 'max_clicks:swallow'"]


def test_regression_stories_need_no_plan(tmp_path: Path) -> None:
    ws, changes = plan_workspace(tmp_path)
    r = development.DevelopmentAgent().run(actx(tmp_path, {"mode": "patch", "changes_dir": str(changes)},
                                                requirements("headers", kinds={"headers": "regression"}), ws))
    assert r.artifacts["code_change"]["commits"] == [] and ws.read("app.py") == "LIMIT = 1\n"


def test_materialize_mode_exports_baseline_in_planned_commits(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
    (src / "a.py").write_text("A = 1\n")
    (src / "b.py").write_text("B = 2\n")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "init"], cwd=src, check=True)
    ws = Workspace(tmp_path / "ws")
    plan = [{"paths": ["a.py"], "message": "feat: a"}, {"paths": ["b.py"], "message": "feat: b"}]
    params = {"mode": "materialize", "ref": "HEAD", "commit_plan": plan, "source": str(src)}
    r = development.DevelopmentAgent().run(actx(tmp_path, params, requirements("shorten"), ws))
    change = r.artifacts["code_change"]
    assert change["commits"] == ["feat: a", "feat: b"] and ws.read("b.py") == "B = 2\n"
    assert "materialised blueprint from git:HEAD" in change["notes"]
    missing = {**params, "commit_plan": [{"paths": ["missing.py"], "message": "feat: ghost"}]}
    with pytest.raises(AgentError, match="cannot export HEAD"):                    # exact or nothing
        development.DevelopmentAgent().run(actx(tmp_path, missing, requirements("shorten"), Workspace(tmp_path / "w2")))
    twice = {**params, "commit_plan": [{"paths": ["a.py"], "message": "feat: a"},
                                       {"paths": ["a.py"], "message": "feat: again"}]}
    with pytest.raises(AgentError, match="'feat: again' matched no files"):
        development.DevelopmentAgent().run(actx(tmp_path, twice, requirements("shorten"), Workspace(tmp_path / "w3")))


def test_unknown_requirements_hash_still_produces_a_commit(tmp_path: Path) -> None:
    ws, changes = plan_workspace(tmp_path)
    c = requirements("max_clicks")
    c._artifacts["requirements"][-1].hash = ""     # an artifact can exist without a usable hash
    development.DevelopmentAgent().run(actx(tmp_path, {"mode": "patch", "changes_dir": str(changes)}, c, ws))
    assert "requirements@" in ws.git("log", "-1", "--format=%B")


# ---------------------------------------------------------------- design, migration, impact
def test_schema_change_proposes_a_migration_review_stage_and_plan(tmp_path: Path) -> None:
    c = requirements("max_clicks")
    r = DesignAgent().run(actx(tmp_path, ctx=c))
    (proposal,) = r.new_stages
    assert proposal["spec"]["id"] == "migration_review" and proposal["before"] == ["development"]
    assert proposal["spec"]["requires_approval"] and proposal["spec"]["impact"] == "high"
    c.put("design", r.artifacts["design"], "design")
    m = MigrationAgent().run(actx(tmp_path, ctx=c))
    (step,) = m.artifacts["migration_plan"]["steps"]
    assert step["forward"] == "ALTER TABLE links ADD COLUMN max_clicks INTEGER"
    assert step["rollback"] == "ALTER TABLE links DROP COLUMN max_clicks"
    assert m.checks == {"has_rollback": True, "backward_compatible": True}


def test_impact_scope_includes_storage_for_schema_changes(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write("urlshort/storage.py", "class Link:\n    click_count = 0\n")
    ws.write("urlshort/service.py", "from .storage import Link\n\ndef resolve():\n    return Link\n")
    ws.write("tests/service/test_service.py", "from urlshort.service import resolve\n")
    r = ImpactAnalysisAgent().run(actx(tmp_path, ctx=requirements("max_clicks"), ws=ws))
    impact = r.artifacts["impact"]
    assert impact["schema_change"] is True and impact["risk"] == "high"
    assert "urlshort/storage.py" in impact["allowed_scope"] and impact["tests_to_update"]
    assert "urlshort.service" in impact["impacted_modules"]


# ---------------------------------------------------------------- review rounds
def test_review_tracks_findings_across_rounds(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    base = ws.head()
    ws.write("urlshort/m.py", '"""doc."""\ntry:\n    x = 1\nexcept ValueError:\n    """ignored"""\n')
    ws.write("tests/test_m.py", "x = 1\n")
    c = ContextStore()
    c.put("code_change", {"base": base, "commits": ["feat: m"]}, "development")
    first = ReviewAgent().run(actx(tmp_path, ctx=c, ws=ws))
    assert first.rework == "development" and "RV-001" in first.feedback[0]
    c.put("review_report", first.artifacts["review_report"], "review")
    ws.write("urlshort/m.py", '"""doc."""\ntry:\n    x = 1\nexcept ValueError:\n    raise\n')
    second = ReviewAgent().run(actx(tmp_path, ctx=c, ws=ws))
    report = second.artifacts["review_report"]
    assert second.rework is None and report["round"] == 2
    assert [x["finding"]["rule"] for x in report["resolved"]] == ["RV-001"]
    assert "## Resolved since previous round" in report["markdown"]


# ---------------------------------------------------------------- defensive checks
def test_qa_rejects_reports_without_a_suite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "j.xml"
    p.write_text("<testsuites></testsuites>")
    with pytest.raises(TransientError, match="no test suite"):
        qa.parse_junit(p)

    class Empty:
        def getroot(self) -> None:
            return None
    monkeypatch.setattr(qa.ET, "parse", lambda path: Empty())
    with pytest.raises(TransientError, match="is empty"):
        qa.parse_junit(p)


def test_non_mapping_artifacts_fail_clearly() -> None:
    c = ContextStore()
    c.put("requirements", ["not", "a", "mapping"], "requirements")
    with pytest.raises(TypeError, match="must be a mapping, got list"):
        c.require_dict("requirements")


def test_plan_already_applied_makes_no_empty_commit(tmp_path: Path) -> None:
    ws, changes = plan_workspace(tmp_path)
    (changes / "max_clicks.py").write_text('KIND, SCOPE = "feat", "x"\nEDITS = [{"file": "app.py", '
                                           '"find": "LIMIT = 1\\n", "replace": "LIMIT = 1\\n"}]\n')
    r = development.DevelopmentAgent().run(actx(tmp_path, {"mode": "patch", "changes_dir": str(changes)},
                                                requirements("max_clicks"), ws))
    assert r.artifacts["code_change"]["commits"] == []


def test_syntax_check_skips_non_python_files() -> None:
    assert development._compiles({"notes.txt": "def (:", "a.py": "x = 1\n"}) is True


@pytest.mark.parametrize("handler,rules", [
    ("except:\n    log()", {"RV-002"}),                 # bare except with a real body
    ("except ValueError:\n    raise", set()),           # typed, re-raised: fine
    ("except ValueError:\n    pass", {"RV-001"}),       # typed but swallowed
])
def test_review_exception_handler_rules(handler: str, rules: set[str]) -> None:
    from sdlc.agents.review import review_source
    src = f'"""doc."""\ntry:\n    x = 1\n{handler}\n'
    assert {f["rule"] for f in review_source("urlshort/m.py", src)} == rules


def test_stage_result_defaults() -> None:
    r = StageResult(summary="s")
    assert r.artifacts == {} and r.tags == [] and r.rework is None


def test_review_allows_long_test_functions_and_immutable_defaults() -> None:
    from sdlc.agents.review import review_source
    long_body = "\n".join(f"    x{i} = {i}" for i in range(70))
    src = f"def helper(a=None, b=(1, 2), *, c=0):\n    return a\n\n\ndef test_long():\n{long_body}\n"
    assert review_source("tests/test_x.py", src) == []
