"""Design Agent: produces architecture, API contract, data model, ADRs, threat model and diagrams.

Re-planning hook: if the design introduces a schema change it proposes a new high-impact
`migration_review` stage in front of development (the engine validates and wires it in).
"""
from __future__ import annotations

from typing import Any

from .base import Agent, AgentContext, StageResult
from .catalog import FEATURES

COMPONENT_DIAGRAM = """flowchart LR
    client([API client / browser]) -->|HTTPS| lb[Load balancer<br/>trusted proxy]
    lb --> mw[web.middleware<br/>request IDs, JSON logs,<br/>security headers, CORS]
    mw --> routes[web routers<br/>ops / links / redirect]
    routes --> rl[GCRA rate limiters<br/>create + redirect]
    routes --> svc[ShortenerService]
    svc --> val[validation<br/>SSRF + shortener guard]
    svc --> an[analytics<br/>bots, daily keyed visitor IDs]
    svc --> aud[AuditTrail<br/>hash chain]
    svc --> repo[(Repository port)]
    repo --> sqlite[(SQLite adapter<br/>WAL, versioned schema)]"""

SEQUENCE_REDIRECT = """sequenceDiagram
    participant U as User agent
    participant A as API (GET /{code})
    participant S as ShortenerService
    participant R as Repository
    U->>A: GET /abc1234
    A->>S: resolve(code, referer, UA, ip)
    S->>R: get_link(code)
    alt unknown / inactive
        S-->>A: NotFound -> 404
    else expired
        S-->>A: LinkExpired -> 410
    else active
        S->>R: record_click (txn: insert click + counter)
        Note over S,R: failure is logged, never blocks the redirect
        S-->>A: target_url
        A-->>U: 307 Location, Cache-Control: no-store
    end"""

STRIDE = [
    {"threat": "Spoofing", "vector": "Unauthenticated takedown requests", "mitigation": "Admin API key, constant-time compare (hmac.compare_digest)"},
    {"threat": "Tampering", "vector": "Altering audit history", "mitigation": "SHA-256 hash chain; verify() detects edits/reordering"},
    {"threat": "Repudiation", "vector": "Admin denies deactivation", "mitigation": "Audit record with actor + timestamp"},
    {"threat": "Information disclosure", "vector": "PII in analytics/logs", "mitigation": "Salted IP hash only; policy CMP-001 blocks PII in log calls"},
    {"threat": "Denial of service", "vector": "Mass link creation / code-space exhaustion", "mitigation": "Per-client token bucket (429 + Retry-After); bounded collision retries"},
    {"threat": "Elevation / SSRF", "vector": "Short links to internal hosts, credential phishing", "mitigation": "Scheme allow-list, private/loopback/link-local IP and *.internal rejection, blocklist"},
]

BASE_DECISIONS = [
    {"summary": "ADR-001 Hexagonal layout: web routers -> framework-free service -> Repository port",
     "rationale": "Business rules testable without HTTP; storage swappable (SQLite now, Postgres planned)",
     "alternatives": ["Fat route handlers", "ORM-coupled models"]},
    {"summary": "ADR-002 Random 7-char base62 codes from a CSPRNG with bounded collision retry",
     "rationale": "Non-enumerable (privacy) and reveals no volume; 62^7 = 3.5e12 codes",
     "alternatives": ["Auto-increment + base62", "Hash of the URL"]},
    {"summary": "ADR-003 307 redirect with Cache-Control: no-store",
     "rationale": "Every click reaches the service, so analytics are accurate; 301 would be cached by browsers",
     "alternatives": ["301 (cheaper, loses analytics)", "302"]},
    {"summary": "ADR-004 Uncapped click recording fails open; capped links fail closed",
     "rationale": "Availability of redirects outranks analytics completeness, but a limit must never be overspent",
     "alternatives": ["Always fail closed", "Async queue (planned for scale-out)"]},
    {"summary": "ADR-005 GCRA rate limiting behind a RateLimiter port",
     "rationale": "Token-bucket behaviour with one number per client; same algorithm as the planned Redis backend",
     "alternatives": ["Fixed window", "Sliding-window log"]},
    {"summary": "ADR-006 Visitor IDs are HMAC-SHA256 under a daily key; raw IPs never stored",
     "rationale": "Counts unique visitors per day without personal data at rest; visitors not linkable across days",
     "alternatives": ["Salted hash with a fixed salt", "Store raw IPs"]},
]


def render(design: dict[str, Any]) -> str:
    md = [f"# Design — {design['title']}", "", "## Overview", design["overview"], "",
          "## Component architecture", "```mermaid", design["diagrams"]["components"], "```", "",
          "## Redirect sequence", "```mermaid", design["diagrams"]["redirect_sequence"], "```", "",
          "## API contract", "| Method & path | Purpose | Stories |", "|---|---|---|"]
    md += [f"| `{e['endpoint']}` | {e['purpose']} | {', '.join(e['stories'])} |" for e in design["api"]]
    if not design["api"]:
        md.append("| _no endpoint changes (cross-cutting feature)_ | | |")
    md += ["", "## Data model"] + [f"- **{t}**" for t in design["data_model"]["tables"]]
    if design["data_model"]["changes"]:
        md += ["", "### Schema changes (require migration review)"] + [f"- {c}" for c in design["data_model"]["changes"]]
    md += ["", "## Architecture decisions"] + [f"- **{d['summary']}** — {d['rationale']} "
                                               f"(rejected: {', '.join(d['alternatives'])})" for d in design["decisions"]]
    md += ["", "## Threat model (STRIDE)", "| Threat | Vector | Mitigation |", "|---|---|---|"]
    md += [f"| {t['threat']} | {t['vector']} | {t['mitigation']} |" for t in design["threat_model"]]
    md += ["", "## Data retention", design["data_retention"], "", "## Risks & trade-offs"]
    md += [f"- {r}" for r in design["risks"]]
    return "\n".join(md) + "\n"


class DesignAgent(Agent):
    name = "design"

    def run(self, ctx: AgentContext) -> StageResult:
        req = ctx.context.require_dict("requirements")
        impact = ctx.context.get("impact")
        endpoints: dict[str, dict[str, Any]] = {}
        tables: set[str] = set()
        changes: list[str] = []
        decisions: list[dict[str, Any]] = list(BASE_DECISIONS if ctx.params.get("greenfield") else [])
        impacted = impact.content.get("impacted_modules", []) if impact and isinstance(impact.content, dict) else None
        for story in req["stories"]:
            f = FEATURES[story["feature"]]
            for ep in f.get("api", []):
                endpoints.setdefault(ep, {"endpoint": ep, "purpose": f["story"][1], "stories": []})["stories"].append(story["id"])
            tables.update(f.get("tables", []))
            if f.get("schema_change"):
                changes.append(f"{f['schema_change']} NULL (nullable => backward compatible; {story['id']})")
        if "max_clicks" in req["features"]:
            decisions.append({"summary": "ADR-007 Enforce max_clicks with one conditional UPDATE at redirect time",
                              "rationale": "Check-and-count must be atomic or concurrent clicks overshoot the cap; "
                                           "bots must not consume it; capped links fail closed",
                              "alternatives": ["Read count then increment (races)", "Separate redemption table"]})
        if "lookalike" in req["features"]:
            decisions.append({"summary": "ADR-008 Reject hostnames that mix Unicode scripts (incl. punycode forms)",
                              "rationale": "Catches homoglyph phishing (Latin + Cyrillic) without network calls; "
                                           "single-script internationalised names stay valid",
                              "alternatives": ["Confusables skeleton vs. a brand list", "Reputation API lookup"]})
        if "hourly" in req["features"]:
            decisions.append({"summary": "ADR-009 Compute clicks_by_hour at read time from the clicks table",
                              "rationale": "No schema change; acceptable at current volumes; rollups planned",
                              "alternatives": ["Hourly rollup table updated on write"]})
        # Cross-cutting stories (headers, logging, limits) touch no endpoint: the contract is "unchanged",
        # which is a valid design outcome, not a missing one.
        cross_cutting = all(not FEATURES[s["feature"]].get("api") for s in req["stories"])
        design: dict[str, Any] = {
            "title": req["title"],
            "overview": ("Stateless HTTP service in a hexagonal layout. " +
                         (f"Delta design touching {', '.join(impacted)}." if impacted is not None else
                          "Greenfield build of all components below.")),
            "api": sorted(endpoints.values(), key=lambda e: e["endpoint"]),
            "data_model": {"tables": sorted(tables), "changes": changes},
            "decisions": decisions,
            "threat_model": STRIDE,
            "data_retention": ("Clicks retained 400 days then purged (job out of scope for prototype); audit log "
                               "retained 7 years; only salted IP hashes stored."),
            "diagrams": {"components": COMPONENT_DIAGRAM, "redirect_sequence": SEQUENCE_REDIRECT},
            "risks": ["SQLite single-writer limits throughput -> Repository port allows Postgres swap",
                      "In-process rate limiter is per-node -> Redis-backed limiter for multi-node",
                      "Synchronous click write on redirect path -> move to async queue at scale (NFR-1)"],
        }
        design["markdown"] = render(design)
        new_stages = []
        if changes and ctx.params.get("propose_migration_stage", True):
            new_stages.append({"spec": {"id": "migration_review", "agent": "migration", "depends_on": ["design"],
                                        "requires_approval": True, "impact": "high",
                                        "exit_gates": ["has_rollback", "backward_compatible"]},
                               "before": ["development"]})
        return StageResult(summary=f"{len(endpoints)} endpoints, {len(decisions)} ADRs, {len(changes)} schema changes",
                           artifacts={"design": design},
                           checks={"api_contract_defined": bool(endpoints) or cross_cutting, "threat_model_present": True},
                           decisions=decisions, new_stages=new_stages)


class MigrationAgent(Agent):
    """Plans forward + rollback DDL for schema changes proposed by the design."""
    name = "migration"

    def run(self, ctx: AgentContext) -> StageResult:
        design = ctx.context.require_dict("design")
        steps: list[dict[str, Any]] = []
        for change in design["data_model"]["changes"]:
            col = change.split()[0]
            table, column = col.split(".")
            steps.append({"forward": f"ALTER TABLE {table} ADD COLUMN {column} INTEGER",
                          "rollback": f"ALTER TABLE {table} DROP COLUMN {column}",
                          "backfill": "none (NULL = unlimited)", "online": True})
        plan = {"steps": steps, "strategy": "expand-only; additive nullable column; app tolerates both schemas",
                "verification": "PRAGMA table_info(links) contains column after startup migration"}
        plan["markdown"] = "# Migration plan\n\n" + "\n".join(
            f"- forward: `{s['forward']}`\n  - rollback: `{s['rollback']}`\n  - backfill: {s['backfill']}" for s in steps
        ) + f"\n\nStrategy: {plan['strategy']}\n"
        return StageResult(summary=f"{len(steps)} migration step(s), additive only", artifacts={"migration_plan": plan},
                           checks={"has_rollback": all(s["rollback"] for s in steps),
                                   "backward_compatible": all("NOT NULL" not in s["forward"] for s in steps)})
