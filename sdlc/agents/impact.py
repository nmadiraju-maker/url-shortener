"""Impact Analysis Agent (brownfield codebase reasoning).

Parses the workspace with `ast` to build a symbol index and import graph, seeds the impacted set
from the concepts behind each requested feature, then expands to transitive importers, affected
API routes, tables and tests. The resulting `allowed_scope` feeds change-control policy CHG-003.
"""
from __future__ import annotations

import ast
import re
from typing import Any

from .base import Agent, AgentContext, StageResult
from .catalog import FEATURES

ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}


def module_name(path: str) -> str:
    return path[:-3].replace("/", ".").removesuffix(".__init__")



def router_prefix(tree: ast.Module) -> str:
    """The `prefix=` of an `APIRouter(...)` created in the module, so routes get their full path."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "APIRouter"):
            for kw in node.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    return kw.value.value
    return ""

def analyse(files: dict[str, str]) -> dict[str, Any]:
    symbols: dict[str, set[str]] = {}
    imports: dict[str, set[str]] = {}
    routes: list[dict[str, Any]] = []
    prefixes: dict[str, str] = {}
    tables: dict[str, set[str]] = {}
    for path, src in files.items():
        mod = module_name(path)
        tree = ast.parse(src)
        prefixes[mod] = router_prefix(tree)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        symbols[mod] = names
        deps = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.level:
                    pkg = mod.rsplit(".", node.level if not path.endswith("__init__.py") else node.level - 1)[0]
                    deps.add(f"{pkg}.{node.module}")
                else:
                    deps.add(node.module)
            elif isinstance(node, ast.Import):
                deps.update(a.name for a in node.names)
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                            and dec.func.attr in ROUTE_METHODS and dec.args
                            and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str)):
                        called = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
                        path_ = prefixes.get(mod, "") + dec.args[0].value
                        routes.append({"route": f"{dec.func.attr.upper()} {path_}", "handler": node.name,
                                       "module": mod, "calls": sorted(called)})
        imports[mod] = deps
        for t in re.findall(r"(?:CREATE TABLE IF NOT EXISTS|FROM|INTO|UPDATE)\s+(\w+)", src):
            tables.setdefault(t, set()).add(mod)
    return {"symbols": symbols, "imports": imports, "routes": routes, "tables": tables}


class ImpactAnalysisAgent(Agent):
    name = "impact"

    def run(self, ctx: AgentContext) -> StageResult:
        req = ctx.context.require_dict("requirements")
        files = {p: s for p, s in ctx.workspace.python_files().items() if p.startswith(("urlshort/", "tests/"))}
        src = {p: s for p, s in files.items() if p.startswith("urlshort/")}
        idx = analyse(src)
        concepts = sorted({c for f in req["features"] for c in FEATURES[f].get("concepts", [])})
        seeds = sorted(m for m, names in idx["symbols"].items() if names & set(concepts))
        impacted, frontier = set(seeds), list(seeds)
        while frontier:  # reverse import closure
            cur = frontier.pop()
            for mod, deps in idx["imports"].items():
                if cur in deps and mod not in impacted:
                    impacted.add(mod)
                    frontier.append(mod)
        wanted = {ep for f in req["features"] for ep in FEATURES[f].get("api", [])}
        routes = [r for r in idx["routes"] if r["route"] in wanted]
        tests = sorted(p for p, s in files.items() if p.startswith("tests/") and
                       any(m in s for m in impacted))
        touched_tables = sorted({t for f in req["features"] for t in FEATURES[f].get("tables", [])})
        schema = any(FEATURES[f].get("schema_change") for f in req["features"])
        scope = sorted({m.replace(".", "/") + ".py" for m in seeds} | {"tests/**", "tests/*", "docs/**", "docs/*",
                                                                       "CHANGELOG.md", "VERSION", "RELEASE_NOTES.md"})
        if schema:
            scope.append("urlshort/storage.py")
        graph = ["flowchart RL"] + [f"    {m.replace('.', '_')}[{m}] --> {d.replace('.', '_')}[{d}]"
                                    for m in sorted(impacted) for d in sorted(idx["imports"][m]) if d in impacted]
        risk = "high" if schema else ("medium" if len(impacted) > 3 else "low")
        impact: dict[str, Any] = {"concepts": concepts, "seed_modules": seeds, "impacted_modules": sorted(impacted),
                  "api_routes": [r["route"] for r in routes], "tables": touched_tables, "schema_change": schema,
                  "tests_to_update": tests, "allowed_scope": sorted(set(scope)), "risk": risk}
        impact["markdown"] = "\n".join([
            "# Impact analysis", "", f"**Risk:** {risk}  |  **Schema change:** {schema}", "",
            f"**Concepts searched:** {', '.join(concepts)}", "",
            "| Category | Items |", "|---|---|",
            f"| Seed modules (direct) | {', '.join(seeds)} |",
            f"| Impacted (reverse import closure) | {', '.join(sorted(impacted))} |",
            f"| API routes | {', '.join(impact['api_routes']) or '-'} |",
            f"| Tables | {', '.join(touched_tables) or '-'} |",
            f"| Tests to update | {', '.join(tests)} |",
            f"| Approved change scope | {', '.join(impact['allowed_scope'])} |", "",
            "## Dependency subgraph (impacted modules)", "```mermaid", "\n".join(graph), "```", ""])
        scope_patterns = impact["allowed_scope"]
        return StageResult(summary=f"{len(seeds)} seed / {len(impacted)} impacted modules, risk={risk}",
                           artifacts={"impact": impact}, checks={"impact_scope_defined": bool(seeds)},
                           decisions=[{"summary": f"Change scope limited to {len(scope_patterns)} path patterns",
                                       "rationale": "derived from AST symbol/import analysis; enforced by CHG-003"}])
