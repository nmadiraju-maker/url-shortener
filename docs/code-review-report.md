# Code review report

All submitted code was reviewed in three layers, and every change reached `main` only through a pull request
whose CI checks were green:

1. **Automated gates on every pull request:** ruff, `mypy --strict` (service and orchestrator), bandit,
   pip-audit, the full test suite on Python 3.11 and 3.12 with 100% line and branch coverage gates, the
   Docker integration job and the SDLC scenarios.
2. **The review agent** (`sdlc/agents/review.py`) on every change made through the orchestrator, sending
   high-severity findings back to development; and on the whole codebase for this report (below).
3. **Human review** of each patch before it was applied, with design decisions recorded in commit messages
   and in `docs/ai-usage.md`.

## Issues found during development and how they were resolved

Every row was a real defect or gap, caught before merge. "Caught by" names the check that found it.

| # | Issue | Caught by | Resolution | Commit |
|---|---|---|---|---|
| 1 | 17 strict typing errors (missing generics, untyped middleware) | mypy --strict (CI introduced) | Typed properly; one documented `cast` | `ci:` walking skeleton |
| 2 | Request log line written after the request ID was cleared (`request_id: "-"`) | Live log inspection | Log before resetting the context variable; regression test | `feat(api): link API` |
| 3 | Uvicorn duplicated every request line in plain text | Live log inspection | Access log off; later all uvicorn lines JSON | `feat(api)`, `feat(logging)` |
| 4 | Test imported from `conftest.py`; import order differed between machines | ruff in the developer's environment | Helpers module with a relative import | `test: move FakeClock...` |
| 5 | Audit chain could fork with two concurrent writers (137 forks in 200 records) | Concurrency test written to measure it | Read-and-append in one `BEGIN IMMEDIATE` transaction | `feat(storage)` |
| 6 | Any integrity error reported as "alias conflict" (409) | Review of error mapping | Only duplicate primary keys map to 409 | `feat(storage)` |
| 7 | Migration first written with f-string SQL and `# nosec` suppressions | Self-review against our own SQL rule | Literal-only DDL, parameterised column check, no suppressions | `feat(analytics)` |
| 8 | Smoke test expected curl clicks to count as human | Running the smoke test | Expectation corrected (curl is a bot); both cases asserted | `feat(analytics)` |
| 9 | Queue log handler mutated the shared record, stripping tracebacks from other handlers | Existing 500-error test | Queue a copy of the record | `feat(logging)` |
| 10 | Redaction could be switched off by passing a custom key list | Review of the logging API | Built-in keys always added, enforced in `configure_logging` | `feat(logging)` |
| 11 | Uvicorn colour-code noise in every log record | Live log audit | Field dropped by the formatter | `feat(logging)` |
| 12 | `create_app` was 136 lines | Review agent rule RV-003 | Split into routers and middleware; longest function 48 lines | `feat(api): trusted proxies...` |
| 13 | A test asserted only that a function existed | Self-review of new tests | Replaced with a real behavioural assertion | `feat(api): trusted proxies...` |
| 14 | A refused production start printed a 54-line raw traceback | Running the container entry point | Entry point logs one JSON line and exits 2 | `feat(config): production profile` |
| 15 | 37 typing errors in the orchestrator, incl. a malformed artifact crashing the policy check | mypy --strict on `sdlc/` | Typed; non-dict artifacts are now blocked, not crashed on | `feat(sdlc): governance core` |
| 16 | `git` called by bare name (PATH hijack) | bandit B607 | Absolute path resolved once | `feat(sdlc): governance core` |
| 17 | **Race:** a human amendment could be overwritten by a stale in-flight result | CI, Python 3.11 job only | Per-stage generations; locked rollbacks; deterministic regression tests | `fix(sdlc): never commit a stale result...` |
| 18 | SQLite WAL files not ignored | `git status` during development | `.gitignore` updated | `chore: ignore SQLite WAL files` |
| 19 | 89 typing errors in the agents | mypy --strict | Checked `require_dict()` accessor instead of casts | `feat(sdlc): agents and CLI` |
| 20 | Impact analysis missed every route defined on a prefixed router | Reading the agent against the new router layout | Router prefixes resolved | `feat(sdlc): agents and CLI` |
| 21 | Design gate failed every cross-cutting change | End-to-end pipeline test | "API unchanged" accepted as a valid design outcome | `feat(sdlc): agents and CLI` |
| 22 | Policy check crashed on `.pyc` files left by test runs | End-to-end pipeline test | Default workspace excludes; binary-safe change listing | `feat(sdlc): agents and CLI` |
| 23 | Baseline export silently fell back to the working tree on any git error | Test of a missing path | Exact export or explicit failure | `feat(sdlc): agents and CLI` |
| 24 | `defusedxml` only installed transitively | Dependency review | Declared and pinned, with type stubs | `feat(sdlc): agents and CLI` |
| 25 | Two lint errors in the generated `max_clicks` change plan | Applying the plan to its baseline before use | Generator fixed | `feat(sdlc): ...scenarios run in CI` |
| 26 | PII rule blocked a test that deliberately logs a synthetic IP | Greenfield scenario | Rule scoped to production code; other rules still apply to tests | `feat(sdlc): ...scenarios run in CI` |
| 27 | A pre-recorded approval was never requested | Reading the run's audit log | Removed (no unused rubber stamps) | `docs: scenario run reports...` |

## Findings the orchestrator's own gates produced during scenario runs

| Scenario | Finding | Gate | Outcome |
|---|---|---|---|
| Brownfield | SQL built with an f-string from request data | Policy SEC-003 (block) | Rolled back; retried with feedback |
| Brownfield | Storage error swallowed, capped links failing open | Review RV-001 (high) | Sent back to development; resolved in round 2 |
| Brownfield | Schema change in `storage.py` | Policy CHG-002 (escalate) | Approved by name; DBA approved the migration plan |

The run reports in `docs/evidence/scenarios/` show each of these with timestamps in the audit timeline.

<!-- automated review below is generated; do not edit by hand -->

## Automated review of every source file

_Generated 2026-10-08: the review agent's rules (`sdlc/agents/review.py`) over all 57 files of `urlshort/` and `sdlc/`._

Findings: **0** (high 0, medium 0, low 0).


<details><summary>Files reviewed</summary>

- `sdlc/__init__.py`
- `sdlc/agents/__init__.py`
- `sdlc/agents/base.py`
- `sdlc/agents/catalog.py`
- `sdlc/agents/design.py`
- `sdlc/agents/development.py`
- `sdlc/agents/docs.py`
- `sdlc/agents/impact.py`
- `sdlc/agents/qa.py`
- `sdlc/agents/registry.py`
- `sdlc/agents/release.py`
- `sdlc/agents/requirements.py`
- `sdlc/agents/requirements_llm.py`
- `sdlc/agents/review.py`
- `sdlc/agents/security.py`
- `sdlc/approvals.py`
- `sdlc/audit.py`
- `sdlc/cli.py`
- `sdlc/context.py`
- `sdlc/engine.py`
- `sdlc/errors.py`
- `sdlc/graph.py`
- `sdlc/llm.py`
- `sdlc/metrics.py`
- `sdlc/policy.py`
- `sdlc/report.py`
- `sdlc/scenario.py`
- `sdlc/workspace.py`
- `urlshort/__init__.py`
- `urlshort/adapters/__init__.py`
- `urlshort/adapters/migrations/env.py`
- `urlshort/adapters/migrations/versions/v2_baseline.py`
- `urlshort/adapters/migrations/versions/v3_max_clicks.py`
- `urlshort/adapters/postgres.py`
- `urlshort/adapters/redis_cache.py`
- `urlshort/adapters/redis_ratelimit.py`
- `urlshort/adapters/wiring.py`
- `urlshort/analytics.py`
- `urlshort/api.py`
- `urlshort/audit.py`
- `urlshort/codegen.py`
- `urlshort/config.py`
- `urlshort/errors.py`
- `urlshort/logging_setup.py`
- `urlshort/main.py`
- `urlshort/models.py`
- `urlshort/ratelimit.py`
- `urlshort/service.py`
- `urlshort/storage.py`
- `urlshort/validation.py`
- `urlshort/web/__init__.py`
- `urlshort/web/clientip.py`
- `urlshort/web/context.py`
- `urlshort/web/middleware.py`
- `urlshort/web/routes_links.py`
- `urlshort/web/routes_ops.py`
- `urlshort/web/routes_redirect.py`

</details>
