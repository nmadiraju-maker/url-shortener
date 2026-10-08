"""Docs Agent (OpenAPI-driven) and StaticDocsAgent fallback (AST-driven, no imports executed)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from ..errors import AgentError
from ..workspace import Workspace
from .base import Agent, AgentContext, StageResult
from .impact import analyse

OPENAPI_CMD = ("import json; from urlshort.api import create_app; from urlshort.config import Settings; "
               "print(json.dumps(create_app(Settings()).openapi()))")


def changelog(ws: Workspace) -> str:
    log = ws.git("log", "--pretty=format:%h%x09%s").splitlines()
    groups: dict[str, list[str]] = {}
    for line in log:
        sha, subject = line.split("\t", 1)
        kind = subject.split("(")[0].split(":")[0]
        groups.setdefault(kind, []).append(f"- {subject} ({sha})")
    titles = {"feat": "Features", "fix": "Bug fixes", "test": "Tests", "docs": "Documentation", "chore": "Chores"}
    out = ["# Changelog", ""]
    for kind in ("feat", "fix", "test", "docs", "chore"):
        if kind in groups:
            out += [f"## {titles[kind]}"] + groups[kind] + [""]
    return "\n".join(out)


def _api_md(spec: dict[str, Any]) -> str:
    out = [f"# {spec['info']['title']} API v{spec['info']['version']}", "",
           "_Generated from the live OpenAPI schema by the Docs Agent._", "",
           "| Method | Path | Summary | Responses |", "|---|---|---|---|"]
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            codes = ", ".join(sorted(op["responses"]))
            out.append(f"| {method.upper()} | `{path}` | {op.get('summary', '')} | {codes} |")
    out += ["", "## Schemas"]
    for name, schema in sorted(spec.get("components", {}).get("schemas", {}).items()):
        props = ", ".join(f"`{k}`" for k in schema.get("properties", {}))
        out.append(f"- **{name}**: {props}")
    return "\n".join(out) + "\n"


class DocsAgent(Agent):
    name = "docs"

    def run(self, ctx: AgentContext) -> StageResult:
        ws = ctx.workspace
        env = {**os.environ, "PYTHONPATH": str(ws.root)}
        proc = subprocess.run([sys.executable, "-c", OPENAPI_CMD], cwd=ws.root, capture_output=True, text=True,
                              env=env, timeout=60)
        if proc.returncode != 0:
            raise AgentError(f"OpenAPI generation failed: {proc.stderr[-300:]}")
        spec = json.loads(proc.stdout)
        ws.write("docs/generated/openapi.json", json.dumps(spec, indent=2))
        ws.write("docs/generated/api.md", _api_md(spec))
        ws.write("CHANGELOG.md", changelog(ws))
        return StageResult(summary=f"API reference for {len(spec['paths'])} paths + changelog",
                           artifacts={"docs": {"files": ["docs/generated/openapi.json", "docs/generated/api.md",
                                                         "CHANGELOG.md"], "paths": sorted(spec["paths"]),
                                               "source": "openapi"}},
                           checks={"api_documented": bool(spec["paths"])},
                           commit_message="docs: regenerate API reference and changelog")


class StaticDocsAgent(Agent):
    name = "docs_static"

    def run(self, ctx: AgentContext) -> StageResult:
        ws = ctx.workspace
        idx = analyse({p: s for p, s in ws.python_files().items() if p.startswith("urlshort/")})
        lines = ["# API routes (static analysis fallback)", "",
                 "_OpenAPI generation was unavailable; routes extracted from source via AST._", "",
                 "| Route | Handler |", "|---|---|"] + [f"| `{r['route']}` | `{r['handler']}` |" for r in idx["routes"]]
        ws.write("docs/generated/api.md", "\n".join(lines) + "\n")
        ws.write("CHANGELOG.md", changelog(ws))
        return StageResult(summary=f"fallback docs: {len(idx['routes'])} routes",
                           artifacts={"docs": {"files": ["docs/generated/api.md", "CHANGELOG.md"],
                                               "paths": [r["route"] for r in idx["routes"]],
                                               "source": "static-fallback"}},
                           checks={"api_documented": bool(idx["routes"])},
                           commit_message="docs: regenerate API reference (static fallback) and changelog")
