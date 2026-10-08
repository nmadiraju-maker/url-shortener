"""QA Agent: runs the test suite with coverage in the workspace and traces tests to acceptance criteria."""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as ET  # hardened parser: no entity expansion / XML bombs

from ..errors import TransientError
from .base import Agent, AgentContext, StageResult

AC_RE = re.compile(r"AC-[A-Z0-9]+-\d+")


def ac_map(files: dict[str, str]) -> dict[str, list[str]]:
    """test function name -> AC ids declared in its docstring."""
    out: dict[str, list[str]] = {}
    for path, src in files.items():
        if not path.startswith("tests/"):
            continue
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                ids = AC_RE.findall(ast.get_docstring(node) or "")
                if ids:
                    out[node.name] = ids
    return out


def parse_junit(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    if root is None:
        raise TransientError(f"test report {path.name} is empty")
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        raise TransientError(f"test report {path.name} contains no test suite")
    cases: dict[str, bool] = {}
    failures = []
    for case in suite.iter("testcase"):
        name = case.get("name", "").split("[")[0]
        ok = case.find("failure") is None and case.find("error") is None
        cases[name] = cases.get(name, True) and ok
        if not ok:
            failures.append(f"{case.get('classname')}::{case.get('name')}")
    return {"total": int(suite.get("tests", 0)), "failures": int(suite.get("failures", 0)),
            "errors": int(suite.get("errors", 0)), "skipped": int(suite.get("skipped", 0)),
            "cases": cases, "failed": failures}


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 100.0


def _totals(files: dict[str, Any]) -> dict[str, Any]:
    """Recompute coverage totals over a subset of files (coverage.py's own percent formula)."""
    keys = ("num_statements", "covered_lines", "num_branches", "covered_branches")
    t = {k: sum(f["summary"].get(k, 0) for f in files.values()) for k in keys}
    total = t["num_statements"] + t["num_branches"]
    t["percent_covered"] = 100.0 * (t["covered_lines"] + t["covered_branches"]) / total if total else 100.0
    return t


def coverage_summary(cov: dict[str, Any], omit: list[str], threshold: float) -> dict[str, Any]:
    """Coverage for the report and gates. Files matching `omit` (measured by another job, e.g. infrastructure
    adapters tested against real servers) are listed as excluded, never silently dropped."""
    excluded = sorted(f for f in cov["files"] if any(fnmatch(f, pattern) for pattern in omit))
    files = {f: d for f, d in cov["files"].items() if f not in excluded}
    totals = _totals(files) if omit else cov["totals"]
    per_file = {f: {"line_pct": round(d["summary"]["percent_covered"], 2), "missing_lines": d["missing_lines"],
                    "missing_branches": d.get("missing_branches", [])} for f, d in files.items()}
    return {"percent": round(totals["percent_covered"], 2), "covered_lines": totals["covered_lines"],
            "num_statements": totals["num_statements"], "num_branches": totals.get("num_branches"),
            "covered_branches": totals.get("covered_branches"), "per_file": per_file, "threshold": threshold,
            "excluded": excluded, "omit_patterns": omit,
            "below_threshold": [f for f, d in per_file.items() if d["line_pct"] < threshold]}


class QAAgent(Agent):
    name = "qa"

    def run(self, ctx: AgentContext) -> StageResult:
        req = ctx.context.require_dict("requirements")
        out_dir = ctx.run_dir / "qa"
        out_dir.mkdir(exist_ok=True)
        threshold = float(ctx.params.get("coverage_threshold", 100))
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore::DeprecationWarning",
               f"--cov={ctx.params.get('package', 'urlshort')}", "--cov-branch",
               f"--cov-report=json:{out_dir / 'coverage.json'}", f"--cov-report=html:{out_dir / 'htmlcov'}",
               f"--junitxml={out_dir / 'junit.xml'}", ctx.params.get("tests", "tests/service")]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COV_CORE", "COVERAGE_"))}
        env.update(PYTHONPATH=str(ctx.workspace.root), COVERAGE_FILE=str(out_dir / ".coverage"))
        try:
            proc = subprocess.run(cmd, cwd=ctx.workspace.root, capture_output=True, text=True, env=env,
                                  timeout=float(ctx.params.get("timeout", 300)))
        except subprocess.TimeoutExpired as exc:
            raise TransientError(f"test run timed out after {exc.timeout}s") from exc
        if not (out_dir / "junit.xml").exists():
            raise TransientError(f"test runner produced no results: {proc.stderr[-400:]}")
        junit = parse_junit(out_dir / "junit.xml")
        cov = json.loads((out_dir / "coverage.json").read_text())
        coverage = coverage_summary(cov, list(ctx.params.get("coverage_omit", [])), threshold)
        per_file = coverage["per_file"]
        mapping = ac_map(ctx.workspace.python_files())
        required = [ac["id"] for s in req["stories"] for ac in s["acceptance_criteria"]]
        traced = {ac: sorted(t for t, ids in mapping.items() if ac in ids) for ac in required}
        passing = {ac: [t for t in ts if junit["cases"].get(t)] for ac, ts in traced.items()}
        uncovered = [ac for ac, ts in passing.items() if not ts]
        line_pct = coverage["percent"]
        report: dict[str, Any] = {"tests": {k: junit[k] for k in ("total", "failures", "errors", "skipped", "failed")},
                  "coverage": coverage,
                  "functional": {"required_acs": len(required), "covered_acs": len(required) - len(uncovered),
                                 "percent": _pct(len(required) - len(uncovered), len(required)),
                                 "trace": passing, "uncovered": uncovered},
                  "command": " ".join(cmd[1:])}
        report["markdown"] = render(report)
        ok_tests = proc.returncode == 0 and junit["failures"] == 0 and junit["errors"] == 0
        defects = not ok_tests or bool(uncovered) or bool(report["coverage"]["below_threshold"])
        return StageResult(
            summary=f"{junit['total']} tests, {junit['failures'] + junit['errors']} failing, coverage {line_pct}%, "
                    f"AC coverage {report['functional']['percent']}%",
            artifacts={"qa_report": report},
            checks={"tests_pass": ok_tests, "coverage_threshold_met": line_pct >= threshold,
                    "acceptance_criteria_traced": not uncovered},
            # deterministic defects go back to development (bounded rework); only infra faults are retried
            rework=ctx.params.get("rework_target", "development") if defects else None,
            feedback=[f"failing test {t}" for t in junit["failed"]] +
                     [f"AC without passing test: {a}" for a in uncovered] +
                     [f"coverage below {threshold}%: {f} missing {d['missing_lines']}"
                      for f, d in per_file.items() if d["line_pct"] < threshold])


def render(r: dict[str, Any]) -> str:
    t, c, f = r["tests"], r["coverage"], r["functional"]
    out = ["# QA report", "", f"`{r['command']}`", "", "## Unit + functional test results",
           f"- Total: **{t['total']}**, failures: {t['failures']}, errors: {t['errors']}, skipped: {t['skipped']}", "",
           "## Code coverage (line + branch)",
           f"- Overall: **{c['percent']}%** ({c['covered_lines']}/{c['num_statements']} lines, "
           f"{c['covered_branches']}/{c['num_branches']} branches); threshold {c['threshold']}%", "",
           "| File | Coverage | Missing lines |", "|---|---|---|"]
    out += [f"| `{p}` | {d['line_pct']}% | {d['missing_lines'] or '-'} |" for p, d in sorted(c["per_file"].items())]
    out += ["", f"Below threshold: {', '.join(c['below_threshold']) or 'none'}",
            f"Measured elsewhere (excluded): {', '.join(c.get('excluded', [])) or 'none'}", "",
            "## Functional coverage (acceptance criteria → passing tests)",
            f"- **{f['covered_acs']}/{f['required_acs']} ACs ({f['percent']}%)**", "",
            "| AC | Verified by |", "|---|---|"]
    out += [f"| {ac} | {', '.join(ts) or '**NOT COVERED**'} |" for ac, ts in f["trace"].items()]
    return "\n".join(out) + "\n"
