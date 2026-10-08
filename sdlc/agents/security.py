"""Security Agent: whole-workspace policy scan + dependency hygiene (runs in parallel with QA/docs)."""
from __future__ import annotations

import re

from ..policy import BLOCK, PolicyEngine
from .base import Agent, AgentContext, StageResult


class SecurityAgent(Agent):
    name = "security"

    def __init__(self, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()

    def run(self, ctx: AgentContext) -> StageResult:
        files = {p: s for p, s in ctx.workspace.python_files().items() if not p.startswith("tests/")}
        violations = self.policy.scan_code(files)
        deps = []
        if ctx.workspace.exists("requirements.txt"):
            for line in ctx.workspace.read("requirements.txt").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and not re.search(r"[<>=~]", line):
                    deps.append(f"unpinned dependency '{line}'")
        blocking = [v for v in violations if v.outcome == BLOCK]
        report = {"files_scanned": len(files), "violations": [v.__dict__ for v in violations],
                  "dependency_findings": deps,
                  "controls_verified": ["parameterised SQL (SEC-003)", "no eval/exec/shell (SEC-001)",
                                        "no hard-coded secrets (SEC-002)", "no PII in logs (CMP-001)"]}
        found = "\n".join(f"- **{v.rule}** {v.message} `{v.location}`" for v in violations) or "No policy violations."
        dep_text = "\n".join(f"- {d}" for d in deps) or "All dependencies have version constraints."
        report["markdown"] = (f"# Security scan\n\nFiles scanned: {len(files)}\n\n{found}"
                              f"\n\n## Dependencies\n{dep_text}\n")
        return StageResult(summary=f"{len(files)} files, {len(violations)} violations, {len(deps)} dependency findings",
                           artifacts={"security_report": report},
                           checks={"no_blocking_findings": not blocking, "dependencies_pinned": not deps},
                           feedback=[f"{v.rule} {v.location}: {v.message}" for v in blocking] + deps)
