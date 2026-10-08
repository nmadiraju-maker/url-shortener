"""Generate the data-driven SDLC documents from the code, the tests and the agents themselves.

    python scripts/generate_sdlc_docs.py

Writes docs/requirements.md (stories, acceptance criteria, traceability to tests), docs/qa-report.md
(unit and functional coverage, from a fresh test run) and the automated part of
docs/code-review-report.md (the review agent over every source file). Re-run after changes; CI does not
need it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sdlc.agents.catalog import FEATURES, NFRS  # noqa: E402
from sdlc.agents.qa import ac_map, parse_junit  # noqa: E402
from sdlc.agents.review import review_source  # noqa: E402

DELIVERED = {"max_clicks": "v0.10.0 (brownfield scenario)", "lookalike": "v0.11.0 (ambiguous scenario)",
             "hourly": "v0.11.0 (ambiguous scenario)"}
STAMP = datetime.now(UTC).strftime("%Y-%m-%d")


def test_sources() -> dict[str, str]:
    return {str(p.relative_to(ROOT)): p.read_text() for p in sorted((ROOT / "tests").rglob("test_*.py"))}


def run_tests(out: Path) -> tuple[dict, dict]:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
           "--cov=urlshort", "--cov=sdlc", "--cov-branch", f"--cov-report=json:{out / 'cov.json'}",
           f"--junitxml={out / 'junit.xml'}", "tests"]
    subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True)
    return json.loads((out / "cov.json").read_text()), parse_junit(out / "junit.xml")


def requirements_doc(trace: dict[str, list[str]], passing: dict[str, bool]) -> str:
    lines = [f"# Requirements and traceability\n\n_Generated {STAMP} by `scripts/generate_sdlc_docs.py` from "
             "`sdlc/agents/catalog.py` and the test suite._\n",
             "Every acceptance criterion (AC) is traced to the tests that verify it: tests name their ACs in "
             "their docstrings, and the QA agent uses the same mapping in every orchestrator run.\n",
             "| Story | Priority | Delivered | ACs | Verified by passing tests |", "|---|---|---|---|---|"]
    for i, (key, f) in enumerate(FEATURES.items(), 1):
        ids = [f"AC-{f['ac_prefix']}-{n}" for n in range(1, len(f["ac"]) + 1)]
        ok = sum(1 for a in ids if any(passing.get(t) for t in trace.get(a, [])))
        lines.append(f"| US-{i:02d} {key} | {f['priority']} | {DELIVERED.get(key, 'v0.9.0')} | {len(ids)} | "
                     f"{ok}/{len(ids)} |")
    lines += ["", "## User stories and acceptance criteria"]
    for i, (key, f) in enumerate(FEATURES.items(), 1):
        as_a, want, so_that = f["story"]
        lines += ["", f"### US-{i:02d} {key}", f"**As a** {as_a}, **I want to** {want}, **so that** {so_that}.", "",
                  "| AC | Given | When | Then | Tests |", "|---|---|---|---|---|"]
        for n, (g, w, t) in enumerate(f["ac"], 1):
            ac = f"AC-{f['ac_prefix']}-{n}"
            tests = ", ".join(f"`{x}`" for x in trace.get(ac, [])) or "**none**"
            lines.append(f"| {ac} | {g} | {w} | {t} | {tests} |")
    lines += ["", "## Non-functional requirements", *[f"- **{n['id']}** {n['text']}" for n in NFRS], ""]
    return "\n".join(lines)


def qa_doc(cov: dict, junit: dict, trace: dict[str, list[str]], passing: dict[str, bool]) -> str:
    acs = [f"AC-{f['ac_prefix']}-{n}" for f in FEATURES.values() for n in range(1, len(f["ac"]) + 1)]
    covered = [a for a in acs if any(passing.get(t) for t in trace.get(a, []))]
    lines = [f"# QA report\n\n_Generated {STAMP} from a fresh run: "
             "`pytest --cov=urlshort --cov=sdlc --cov-branch tests`._\n",
             "## Results", f"- Tests: **{junit['total']}**, failures {junit['failures']}, errors {junit['errors']}, "
             f"skipped {junit['skipped']}",
             f"- Functional coverage: **{len(covered)}/{len(acs)} acceptance criteria** verified by passing tests", ""]
    for pkg in ("urlshort", "sdlc"):
        files = {k: v for k, v in cov["files"].items() if k.startswith(pkg + "/")}
        stmts = sum(v["summary"]["num_statements"] for v in files.values())
        miss = sum(v["summary"]["missing_lines"] for v in files.values())
        br = sum(v["summary"]["num_branches"] for v in files.values())
        brm = sum(v["summary"]["num_branches"] - v["summary"]["covered_branches"] for v in files.values())
        lines += [f"## Unit coverage: `{pkg}`", f"{stmts - miss}/{stmts} lines and {br - brm}/{br} branches.", "",
                  "| File | Lines | Branches | Missing |", "|---|---|---|---|"]
        for name, v in sorted(files.items()):
            s = v["summary"]
            lines.append(f"| `{name}` | {s['covered_lines']}/{s['num_statements']} | "
                         f"{s['covered_branches']}/{s['num_branches']} | {v['missing_lines'] or '-'} |")
        lines.append("")
    lines += ["## Where 100% was not literally achieved",
              "- **Protocol class bodies are excluded from coverage** (`pyproject.toml`): they are interface "
              "declarations whose `...` bodies never execute.",
              "- **Bandit low-severity B404/B603 in `sdlc/`** are accepted by design: the orchestrator must run git "
              "(argument lists, no shell, absolute executable path). The service has no findings at any severity.",
              "- **Infrastructure adapters** (`urlshort/adapters/`) need real Postgres and Redis: CI measures them in "
              "its own job (`make ci-infra` locally). They are included here only if the report was generated with "
              "`URLSHORT_TEST_DATABASE_URL` and `URLSHORT_TEST_REDIS_URL` set.",
              "- **Docker-dependent checks** (container smoke test, production refusal) run only in CI, not in this "
              "report's local run.", "",
              "## How CI enforces this",
              "Every pull request runs lint, `mypy --strict`, bandit + pip-audit, the suite on Python 3.11 and 3.12 "
              "with separate 100% line-and-branch gates for `urlshort` and `sdlc`, the Docker integration job and "
              "the four SDLC scenarios.", ""]
    return "\n".join(lines)


def review_doc() -> str:
    files = sorted(p for pkg in ("urlshort", "sdlc") for p in (ROOT / pkg).rglob("*.py"))
    findings = [f for p in files for f in review_source(str(p.relative_to(ROOT)), p.read_text())]
    lines = [f"## Automated review of every source file\n\n_Generated {STAMP}: the review agent's rules "
             f"(`sdlc/agents/review.py`) over all {len(files)} files of `urlshort/` and `sdlc/`._\n",
             f"Findings: **{len(findings)}** "
             f"(high {sum(f['severity'] == 'high' for f in findings)}, "
             f"medium {sum(f['severity'] == 'medium' for f in findings)}, "
             f"low {sum(f['severity'] == 'low' for f in findings)}).", ""]
    if findings:
        lines += ["| Rule | Severity | Location | Message |", "|---|---|---|---|"]
        lines += [f"| {f['rule']} | {f['severity']} | `{f['file']}:{f['line']}` | {f['message']} |" for f in findings]
    lines += ["", "<details><summary>Files reviewed</summary>", "", *[f"- `{p.relative_to(ROOT)}`" for p in files],
              "", "</details>", ""]
    return "\n".join(lines)


def main() -> None:
    sources = test_sources()
    mapping = ac_map(sources)
    trace: dict[str, list[str]] = {}
    for test, ids in mapping.items():
        for ac in ids:
            trace.setdefault(ac, []).append(test)
    with tempfile.TemporaryDirectory() as tmp:
        cov, junit = run_tests(Path(tmp))
    passing = junit["cases"]
    (ROOT / "docs" / "requirements.md").write_text(requirements_doc(trace, passing))
    (ROOT / "docs" / "qa-report.md").write_text(qa_doc(cov, junit, trace, passing))
    review = ROOT / "docs" / "code-review-report.md"
    marker = "<!-- automated review below is generated; do not edit by hand -->"
    head = review.read_text().split(marker)[0] if review.exists() else "# Code review report\n\n"
    review.write_text(head.rstrip("\n") + "\n\n" + marker + "\n\n" + review_doc())
    print("wrote docs/requirements.md, docs/qa-report.md, docs/code-review-report.md")


if __name__ == "__main__":
    main()
