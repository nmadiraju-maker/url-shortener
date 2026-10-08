"""End to end: every agent runs, under the real engine and policies, against a copy of this repository's
own service. The request is a regression-only change ("security headers" already exist), so no code is
written, but each agent does its real work: impact analysis reads the real routers, QA runs the real test
suite and traces acceptance criteria, docs generates the real OpenAPI spec, release tags after approval."""
import json
from pathlib import Path

import pytest

from sdlc import report, scenario
from sdlc.approvals import DecisionFileApprovals
from sdlc.engine import Orchestrator

REPO = Path(__file__).resolve().parents[2]
SERVICE_PATHS = ["pyproject.toml", "requirements.txt", "urlshort", "tests/__init__.py", "tests/service", "config"]

SCENARIO = {
    "name": "pipeline-test",
    "workspace": {"seed": "baseline", "ref": "HEAD", "paths": SERVICE_PATHS, "source": str(REPO)},
    "engine": {"backoff_base": 0},
    "inputs": {"requirements": {"title": "Security headers", "requirement": "Every response needs security headers.",
                                "existing_features": ["headers"]}},
    "stages": [
        {"id": "requirements", "agent": "requirements", "exit_gates": ["stories_have_acceptance_criteria"]},
        {"id": "impact", "agent": "impact", "depends_on": ["requirements"], "entry_gates": ["requirements"]},
        {"id": "design", "agent": "design", "depends_on": ["impact"], "requires_approval": True,
         "exit_gates": ["api_contract_defined", "threat_model_present"]},
        {"id": "development", "agent": "development", "depends_on": ["design"], "mutates_workspace": True,
         "params": {"mode": "patch"}},
        {"id": "review", "agent": "review", "depends_on": ["development"], "exit_gates": ["no_high_findings"]},
        {"id": "qa", "agent": "qa", "depends_on": ["review"],
         "exit_gates": ["tests_pass", "coverage_threshold_met", "acceptance_criteria_traced"],
         "params": {"coverage_omit": ["urlshort/adapters/*"]}},           # measured by the infrastructure job
        {"id": "security", "agent": "security", "depends_on": ["review"], "exit_gates": ["no_blocking_findings"]},
        {"id": "docs", "agent": "docs", "fallback_agent": "docs_static", "depends_on": ["review"],
         "mutates_workspace": True, "exit_gates": ["api_documented"]},
        {"id": "release", "agent": "release", "depends_on": ["qa", "security", "docs"], "mutates_workspace": True,
         "requires_approval": True, "impact": "high", "exit_gates": ["all_gates_green"],
         "params": {"version": "9.9.9"}},
    ],
}
APPROVALS = {"design#1": {"status": "approved", "approver": "jane.architect", "comment": "ok"},
             "release#1": {"status": "approved", "approver": "raj.release-manager", "comment": "ship"}}


@pytest.fixture(scope="module")
def finished(tmp_path_factory: pytest.TempPathFactory) -> Orchestrator:
    base = tmp_path_factory.mktemp("pipeline")
    (base / "approvals.json").write_text(json.dumps(APPROVALS))
    approvals = DecisionFileApprovals(base / "approvals.json")
    orch = scenario.build(json.loads(json.dumps(SCENARIO)), base / "run", approvals)
    orch.run()
    return orch


def test_pipeline_completes_with_every_gate_green(finished: Orchestrator) -> None:
    assert finished.run_status == "COMPLETED", finished.reasons
    assert set(finished.status.values()) == {"SUCCEEDED"}
    assert [a["checkpoint"] for a in finished.approvals_log] == ["design#1", "release#1"]
    assert finished.audit.verify()


def test_agents_did_real_work_on_this_codebase(finished: Orchestrator) -> None:
    ctx = finished.ctx
    req = ctx.require_dict("requirements")
    assert [s["kind"] for s in req["stories"]] == ["regression"]
    impact = ctx.require_dict("impact")
    assert "urlshort.web.middleware" in impact["seed_modules"]                    # found by concept search
    qa = ctx.require_dict("qa_report")
    assert qa["tests"]["failures"] == 0 and qa["tests"]["total"] > 250            # the real service suite
    assert qa["functional"]["trace"]["AC-HEADERS-1"]                              # traced to a passing test
    assert qa["coverage"]["percent"] == 100.0
    assert qa["coverage"]["excluded"] and all(f.startswith("urlshort/adapters/") for f in qa["coverage"]["excluded"])
    docs = ctx.require_dict("docs")
    assert docs["source"] == "openapi" and "/api/v1/links/{code}/stats" in docs["paths"]
    review = ctx.require_dict("review_report")
    assert review["verdict"] == "approved" and review["files_reviewed"] == []     # regression only: no code changed
    release = ctx.require_dict("release")
    assert all(release["checklist"].values())


def test_release_tag_is_applied_only_after_approval(finished: Orchestrator) -> None:
    assert finished.ws.git("tag") == "v9.9.9"
    events = [r["event"] for r in finished.audit.records()]
    assert events.index("approval.decision", events.index("stage.start") + 1) < len(events)
    assert finished.ws.exists("RELEASE_NOTES.md") and finished.ws.exists("docs/generated/openapi.json")


def test_impact_reads_router_prefixes(finished: Orchestrator) -> None:
    from sdlc.agents.impact import analyse
    files = {str(p.relative_to(finished.ws.root)): p.read_text()
             for p in (finished.ws.root / "urlshort").rglob("*.py")}
    routes = {r["route"] for r in analyse(files)["routes"]}
    assert {"POST /api/v1/links", "GET /api/v1/links/{code}/stats", "GET /{code}", "GET /livez"} <= routes


def test_run_report_covers_the_run(finished: Orchestrator) -> None:
    path = report.write(finished, "pipeline test")
    text = path.read_text()
    for heading in ("## Stage graph (final state)", "## Human approval checkpoints", "## Policy guardrail evaluations",
                    "## Decisions (lineage)", "## Reliability metrics", "## Timeline"):
        assert heading in text
    assert "jane.architect" in text and "```mermaid" in text
    metrics = json.loads((finished.run_dir / "metrics.json").read_text())
    assert metrics["attempt_success_rate"] == 1.0
