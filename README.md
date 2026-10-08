# URL shortener, delivered by a governed AI SDLC

Two things in one repository:

- **`urlshort/`**: a production-minded URL shortener (FastAPI, SQLite).
- **`sdlc/`**: a governed, agentic SDLC orchestrator. Agents do the work; an engine with gates, policies,
  human approvals and a tamper-evident audit log decides whether it passes. Three features of the service
  (click caps, look-alike domain protection, clicks by hour) were delivered through it.

![CI](https://github.com/nmadiraju-maker/url-shortener/actions/workflows/ci.yml/badge.svg)

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
make install-dev
make ci                                   # lint, types, security, 469 tests at 100% coverage
URLSHORT_ADMIN_API_KEY=dev-key make run   # http://localhost:8000/docs
```

```bash
curl -s -X POST localhost:8000/api/v1/links -H 'content-type: application/json' \
  -d '{"url": "https://example.com/offer", "max_clicks": 100}'
curl -i localhost:8000/<code>                                   # 307 to the target
curl -s localhost:8000/api/v1/links/<code>/stats -H 'x-stats-token: <token from create>'
```

Run the orchestrator scenarios (needs the tags `v0.9.0` and `v0.10.0`):

```bash
make scenarios                            # greenfield, brownfield, ambiguous, ambiguous-llm
python -m sdlc.cli verify runs/brownfield # audit hash chain
open runs/brownfield/run-report.md
```

## The service

| Capability | Details |
|---|---|
| Core API | Create (idempotent), 307 redirect, details, admin takedown; JSON error envelope with request IDs |
| Links | Custom aliases, expiry, click caps enforced atomically (`max_clicks`) |
| Analytics | Bots separated, no raw IPs (daily keyed visitor IDs), referrer domains, clicks by day and hour, stats protected by a per-link token |
| Safety | SSRF protection, credentials and shortener chains rejected, look-alike (mixed-script) domains rejected |
| Abuse | GCRA rate limits on creates and redirects, `RateLimit-*` and `Retry-After` headers |
| Operations | JSON logs with redaction, liveness/readiness probes, security headers, CORS, trusted proxies, production profile that refuses unsafe settings, non-root container |

Configuration: `config/urlshort.example.toml` (every setting, documented) or `URLSHORT_*` environment
variables. See `docs/api.md`, `docs/logging.md`, `docs/production.md`. Scale-out: PostgreSQL storage and a
Redis cache, Bloom filter and shared rate limits (`docs/infrastructure.md`; `docker compose up -d`,
`make ci-infra`).

## The orchestrator

```bash
python -m sdlc.cli run scenarios/brownfield.json --approvals scenarios/approvals/brownfield.json
python -m sdlc.cli resume runs/<run> --interactive
python -m sdlc.cli promote runs/<run> --approver "<name>"
```

How it works: `docs/orchestrator.md`. What each scenario demonstrates: `docs/scenarios.md`. Run reports
from the runs that produced v0.10.0 and v0.11.0: `docs/evidence/scenarios/`.

## Documents

| Document | Contents |
|---|---|
| [`docs/requirements.md`](docs/requirements.md) | User stories, acceptance criteria, traceability to tests (generated) |
| [`docs/design.md`](docs/design.md) | Architecture, data model, decisions, threat model, orchestration model |
| [`docs/code-review-report.md`](docs/code-review-report.md) | Review process, every issue found and how it was resolved, automated review of all files |
| [`docs/qa-report.md`](docs/qa-report.md) | Unit and functional coverage (generated) |
| [`docs/ai-usage.md`](docs/ai-usage.md) | How AI was used and verified; decision and failure ledgers |
| [`docs/final-summary.md`](docs/final-summary.md) | Plan, artifacts, validation, risks, assumptions, limitations |

Regenerate the generated documents with `python scripts/generate_sdlc_docs.py`.

## Versions

| Tag | Produced by |
|---|---|
| `v0.9.0` | The service and orchestrator built commit by commit |
| `v0.10.0` | The brownfield scenario: click caps |
| `v0.11.0` | The ambiguous scenario: look-alike domains, clicks by hour |

## Honest limitations

Agents run deterministically (prepared change plans; an LLM-backed requirements agent with fallback). No
real model responses are recorded here. Streaming analytics, owner accounts and observability are
planned. Details: `docs/final-summary.md`.

Built with an AI assistant (Claude); see `docs/ai-usage.md`.
