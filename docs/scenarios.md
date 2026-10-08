# SDLC scenarios

Three end-to-end runs of the orchestrator (`sdlc/`) against this service. Each is a JSON file in
`scenarios/` (stage graph, inputs, workspace seed) plus the recorded human decisions in
`scenarios/approvals/`. CI runs all three on every pull request and uploads their run reports.

```bash
make greenfield      # or brownfield / ambiguous / scenarios (all three)
python -m sdlc.cli verify runs/brownfield
```

They need the baseline tags `v0.9.0` (the service before any scenario) and `v0.10.0` (after the
brownfield delivery). Agents run offline: development applies the reviewed change plans in
`scenarios/changes/`, and two of them replay recorded flawed first drafts so the gates can be shown
catching real mistakes. Approvers' names are illustrative; their decisions are recorded in the approval
files and every one appears in the run's audit log.

## Greenfield: governance over a full build

The v0.9.0 service is materialised from its tag, one commit per area, through every gate.

| What it shows | How |
|---|---|
| Requirements from a feature list | 13 stories, every acceptance criterion traced to a passing test by QA |
| Architecture sign-off | Design (components, API contract, ADRs, STRIDE) needs human approval |
| Change control on a large change | The build touches protected paths (schema, config, dependencies) and is large: escalated, approved by name |
| Recovery from infrastructure failure | An injected QA runner fault is retried with backoff; MTTR is measured |
| Gated release | Release readiness checklist, then a tag applied only after approval |

Honest scope: offline mode replays the reviewed baseline. It demonstrates governance over a build, not
code generation; LLM-backed agents are planned behind the same gates.

## Brownfield: per-link click caps (delivers v0.10.0)

Request: an optional `max_clicks` per link that holds under concurrency.

| What it shows | How |
|---|---|
| Codebase reasoning | Impact analysis finds the affected modules, routes and tables and fixes the change scope |
| Dynamic re-planning | Design detects a schema change and inserts a `migration_review` stage; a DBA approves |
| Security guardrail | Draft 1 builds SQL with an f-string from request data: policy SEC-003 blocks it, the workspace rolls back, the agent retries |
| Human change control | Touching `storage.py` (schema) escalates for approval |
| Review loop | Draft 2 swallows a storage error and fails *open* on capped links: review RV-001 sends it back; round 2 approves |
| Fallback | The docs agent is made to fail; the static-analysis fallback produces the docs |
| Result | Schema v3 (additive), atomic check-and-count, capped links fail closed, 13 new tests including a concurrency test |

## Ambiguous: "make links safer, track more stuff, should be fast" (delivers v0.11.0)

| What it shows | How |
|---|---|
| Ambiguity detection | Four vague terms; two have options, two have none |
| Clarification | The product owner amends: "more stuff" = time of day; "fast" = already NFR-1, no work |
| Assumptions signed off | "Safer" is assumed to mean security headers; the product owner accepts for now |
| Mid-run upstream change | At design review the security lead re-scopes "safer" to look-alike domains: requirements and everything after them are invalidated and re-run |
| Result | Look-alike (mixed-script, incl. punycode) hostname rejection and `clicks_by_hour`, including the update of an existing test whose expectation changed |

## Promoting a run's result

```bash
git checkout -b deliver/<name>
python -m sdlc.cli promote runs/<name> --approver "<your name>"
```

`promote` refuses unless the run completed, its audit chain verifies and the repository has no
uncommitted changes. The run's commits keep the development agent as author; the approver is recorded in
the run's audit log.
