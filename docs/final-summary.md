# Final engineering summary

## What was built

A URL shortener service and a governed, agentic SDLC orchestrator that delivered three of its features.

| Area | Result |
|---|---|
| Service | Core API (create, redirect, details, admin takedown), custom aliases, expiry, click caps, privacy-safe analytics with protected stats, clicks by hour, SSRF and look-alike domain protection, GCRA rate limiting, trusted proxies, security headers, CORS, JSON logging with redaction, hash-chained audit, production profile, container |
| Orchestrator | Dependency graph with entry/exit gates, parallel stages, retries, fallback agents, rollback, rework, dynamic re-planning, human approvals (approve/reject/amend/pause), safe-stop, policy guardrails, hash-chained audit, metrics, CLI with gated `promote` |
| Agents | 12 agents including an LLM-backed requirements agent with strict validation and deterministic fallback |
| Scenarios | Greenfield (governed build), brownfield (`max_clicks` → v0.10.0), ambiguous (clarification and mid-run re-plan → v0.11.0), ambiguous via the LLM agent |

## Plan and rationale

1. Walking skeleton with CI first, so every later change was gated from day one.
2. Service features in small, reviewed commits, each hardening an earlier weakness (privacy, abuse, operability).
3. Orchestrator core, then agents, then scenarios, each held to the same gates as the service.
4. Real features delivered through the orchestrator, then merged with `promote`.
5. Required documents generated from code and runs wherever possible, so they cannot drift.

## Artifacts

| Artifact | Location |
|---|---|
| User stories and acceptance criteria, traceability | `docs/requirements.md` |
| Design, architecture, decisions, threat model | `docs/design.md`, `docs/orchestrator.md` |
| Code review report | `docs/code-review-report.md` |
| QA report (unit and functional coverage) | `docs/qa-report.md` |
| Scenario evidence (run reports, metrics, artifacts) | `docs/evidence/scenarios/`, CI artifact `sdlc-run-reports` |
| AI decision and failure ledgers | `docs/ai-usage.md` |
| API, logging, production, scenarios | `docs/api.md`, `docs/logging.md`, `docs/production.md`, `docs/scenarios.md` |

## Validation

- 469 tests; 100% line and branch coverage for both `urlshort` and `sdlc`; 32/32 acceptance criteria
  verified by passing tests; 0 findings from the review agent over every source file.
- Postgres, Redis and event-pipeline adapters tested against real servers (29 tests, 100% coverage, own CI
  job), including reclaiming a crashed consumer's messages and end-to-end delivery with duplicates and replay.
- CI on every pull request: ruff, `mypy --strict`, bandit, pip-audit, tests on Python 3.11 and 3.12,
  Docker image + smoke test + production-mode checks, and four SDLC scenarios with audit-chain verification.
- Concurrency-sensitive code checked under load: click caps (40 parallel redirects), rate limiter (50
  parallel requests), audit chain (concurrent writers), orchestrator (25 consecutive runs per Python version).

## Risks and trade-offs

| Choice | Trade-off |
|---|---|
| SQLite by default | Simple and transactional, one writer at a time; Postgres adapter available (`docs/infrastructure.md`) |
| Redis rate limiter fails open | Service stays up during a Redis outage, without limiting until it recovers |
| Cached links | `click_count` may lag by the cache TTL; caps are enforced in the database, not the cache |
| Read-time analytics | Fine at current volume; optional event pipeline (outbox + Redis Streams) decouples writes; rollups planned |
| Events mode stats are eventually consistent | Counts and caps stay exact; per-click details appear once aggregated |
| Deterministic agents | Reproducible and fully testable, but they apply prepared change plans rather than generate code |
| Per-link stats tokens | Private by default; a lost token cannot be recovered, but owner API keys read their links' stats without one |
| Static shortener and look-alike rules | No network calls on create; a reputation service would catch more |

## Assumptions

- Single region. With SQLite, one instance per database file; with Postgres and Redis, many instances.
- Clients are identified by IP until owner API keys exist; the load balancer's addresses are configured.
- Scenario approvals are recorded decisions by named roles, standing in for real reviewers.

## Known limitations

- No real LLM responses are recorded in the repository (see `docs/ai-usage.md`).
- Greenfield replays the reviewed v0.9.0 baseline; it demonstrates governance, not code generation.
- The audit trail is tamper-evident, not tamper-proof: signed checkpoints detect rewrites, and shipping them
  to write-once storage is an operational step.
- Service API keys only (no end-user accounts or SSO).
