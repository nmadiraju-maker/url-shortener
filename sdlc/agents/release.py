"""Release Agent: verifies release readiness across all stages; tagging happens only after human approval."""
from __future__ import annotations

from typing import Any

from ..audit import AuditLog
from ..context import ContextStore
from .base import Agent, AgentContext, StageResult

REQUIRED = ["requirements", "design", "code_change", "review_report", "qa_report", "security_report", "docs"]


def _content(c: ContextStore, name: str) -> dict[str, Any] | None:
    art = c.get(name)
    return art.content if art is not None and isinstance(art.content, dict) else None


class ReleaseAgent(Agent):
    name = "release"

    def run(self, ctx: AgentContext) -> StageResult:
        c = ctx.context
        missing = [a for a in REQUIRED if not c.has(a)]
        review = _content(c, "review_report")
        qa = _content(c, "qa_report")
        checklist = {
            "all_artifacts_present": not missing,
            "review_approved": review is not None and review["verdict"] == "approved",
            "tests_green": qa is not None and qa["tests"]["failures"] == 0 and qa["tests"]["errors"] == 0,
            "coverage_target_met": qa is not None and not qa["coverage"]["below_threshold"],
            "acceptance_criteria_verified": qa is not None and not qa["functional"]["uncovered"],
            "security_clean": c.has("security_report") and not any(
                v["outcome"] == "block" for v in c.require_dict("security_report")["violations"]),
            "audit_chain_intact": AuditLog(ctx.run_dir / "audit.jsonl", "verify").verify(),
        }
        version = ctx.params.get("version", "0.1.0")
        req = c.require_dict("requirements")
        notes = [f"# Release v{version}", "", "## Stories delivered"]
        for s in req["stories"]:
            acs = ", ".join(a["id"] for a in s["acceptance_criteria"])
            notes.append(f"- {s['id']} {s['i_want']} ({acs})")
        if qa:
            q = qa
            notes += ["", "## Quality", f"- {q['tests']['total']} tests passing; coverage {q['coverage']['percent']}%; "
                                         f"AC coverage {q['functional']['percent']}%"]
        notes += ["", "## Readiness checklist"] + [f"- [{'x' if v else ' '}] {k}" for k, v in checklist.items()]
        notes += ["", "## Rollback", "Redeploy previous tag; schema changes are additive (expand-only) so the previous "
                                      "version runs against the new schema."]
        ctx.workspace.write("RELEASE_NOTES.md", "\n".join(notes) + "\n")
        ctx.workspace.write("VERSION", version + "\n")
        return StageResult(summary=f"release candidate v{version}: "
                                   f"{sum(checklist.values())}/{len(checklist)} readiness checks green",
                           artifacts={"release": {"version": version, "checklist": checklist, "missing": missing,
                                                  "markdown": "\n".join(notes) + "\n"}},
                           checks={"all_gates_green": all(checklist.values())}, tags=[f"v{version}"],
                           commit_message=f"chore(release): v{version}")
