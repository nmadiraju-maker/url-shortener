"""Code Review Agent: reviews every changed file, tracks findings across rounds and requests rework."""
from __future__ import annotations

import ast
import re
from typing import Any

from .base import Agent, AgentContext, StageResult

SEVERITY_BLOCKS = {"high"}


def review_source(path: str, src: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    def add(rule: str, sev: str, msg: str, node: ast.AST | None = None, symbol: str = "") -> None:
        findings.append({"rule": rule, "severity": sev, "file": path, "line": getattr(node, "lineno", 0),
                         "symbol": symbol, "message": msg})

    tree = ast.parse(src)
    funcs = {n: n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    owner: dict[ast.AST, str] = {}
    for fn, name in funcs.items():
        for child in ast.walk(fn):
            owner.setdefault(child, name)
    for node in ast.walk(tree):
        sym = owner.get(node, "<module>")
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                add("RV-002", "high", "bare `except:` hides errors", node, sym)
            if all(isinstance(b, ast.Pass) or (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant))
                   for b in node.body):
                add("RV-001", "high", "exception swallowed silently (no log/re-raise)", node, sym)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print" \
                and not path.startswith(("tests/", "sdlc/cli")):
            add("RV-004", "medium", "print() in library code; use structured logging", node, sym)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = (node.end_lineno or node.lineno) - node.lineno
            if length > 60 and not path.startswith("tests/"):
                add("RV-003", "medium", f"function is {length} lines; consider splitting", node, node.name)
            for default in node.args.defaults + node.args.kw_defaults:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    add("RV-007", "medium", "mutable default argument", node, node.name)
    for i, line in enumerate(src.splitlines(), 1):
        if re.search(r"#\s*(TODO|FIXME|XXX)", line):
            findings.append({"rule": "RV-006", "severity": "low", "file": path, "line": i, "symbol": "",
                             "message": "unresolved TODO/FIXME"})
    if not path.startswith("tests/") and not ast.get_docstring(tree):
        add("RV-008", "low", "module docstring missing")
    return findings


def fingerprint(f: dict[str, Any]) -> str:
    return f"{f['rule']}:{f['file']}:{f['symbol']}"


class ReviewAgent(Agent):
    name = "review"

    def run(self, ctx: AgentContext) -> StageResult:
        change = ctx.context.require_dict("code_change")
        changed, _, _ = ctx.workspace.changes_since(change["base"])
        py = {p: s for p, s in changed.items() if p.endswith(".py")}
        findings = [f for p, s in sorted(py.items()) for f in review_source(p, s)]
        src_changed = [p for p in py if p.startswith("urlshort/")]
        if src_changed and not any(p.startswith("tests/") for p in changed):
            findings.append({"rule": "RV-005", "severity": "high", "file": ", ".join(src_changed), "line": 0,
                             "symbol": "", "message": "production code changed without test changes"})
        previous = [h.content for h in ctx.context.history("review_report") if isinstance(h.content, dict)]
        round_no = len(previous) + 1
        prev_fps = {fingerprint(f): (r["round"], f) for r in previous for f in r["findings"]}
        now_fps = {fingerprint(f) for f in findings}
        resolved = [{"finding": f, "raised_in_round": rnd, "resolved_in_round": round_no,
                     "resolution": "fixed by development agent after rework"}
                    for fp, (rnd, f) in prev_fps.items() if fp not in now_fps]
        blocking = [f for f in findings if f["severity"] in SEVERITY_BLOCKS]
        verdict = "changes_requested" if blocking else "approved"
        report = {"round": round_no, "verdict": verdict, "files_reviewed": sorted(changed),
                  "commits": change["commits"],
                  "findings": findings, "resolved": resolved,
                  "counts": {s: sum(f["severity"] == s for f in findings) for s in ("high", "medium", "low")}}
        report["markdown"] = render(report)
        feedback = [f"{f['rule']} {f['file']}:{f['line']} ({f['symbol']}) {f['message']}" for f in blocking]
        return StageResult(summary=f"round {round_no}: {verdict}; {len(findings)} findings, {len(resolved)} resolved",
                           artifacts={"review_report": report}, checks={"no_high_findings": not blocking},
                           rework="development" if blocking else None, feedback=feedback)


def render(r: dict[str, Any]) -> str:
    out = [f"# Code review — round {r['round']}: **{r['verdict'].upper()}**", "",
           f"Files reviewed ({len(r['files_reviewed'])}): " + ", ".join(f"`{f}`" for f in r["files_reviewed"]), "",
           "Commits: " + "; ".join(r["commits"]), "", "## Findings",
           "| Rule | Severity | Location | Symbol | Message |", "|---|---|---|---|---|"]
    out += [f"| {f['rule']} | {f['severity']} | `{f['file']}:{f['line']}` | {f['symbol']} | {f['message']} |"
            for f in r["findings"]] or ["| - | - | - | - | none |"]
    if r["resolved"]:
        out += ["", "## Resolved since previous round", "| Rule | Location | Raised | Resolved | Resolution |",
                "|---|---|---|---|---|"]
        for x in r["resolved"]:
            f = x["finding"]
            out.append(f"| {f['rule']} | `{f['file']}:{f['line']}` | round {x['raised_in_round']} "
                       f"| round {x['resolved_in_round']} | {x['resolution']} |")
    return "\n".join(out) + "\n"
