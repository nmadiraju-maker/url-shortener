# QA report

_Generated 2026-10-08 from a fresh run: `pytest --cov=urlshort --cov=sdlc --cov-branch tests`._

## Results
- Tests: **537**, failures 0, errors 0, skipped 0
- Functional coverage: **32/32 acceptance criteria** verified by passing tests

## Unit coverage: `urlshort`
1875/1875 lines and 428/428 branches.

| File | Lines | Branches | Missing |
|---|---|---|---|
| `urlshort/__init__.py` | 1/1 | 0/0 | - |
| `urlshort/adapters/__init__.py` | 0/0 | 0/0 | - |
| `urlshort/adapters/migrations/env.py` | 9/9 | 0/0 | - |
| `urlshort/adapters/migrations/versions/v2_baseline.py` | 13/13 | 0/0 | - |
| `urlshort/adapters/migrations/versions/v3_max_clicks.py` | 7/7 | 0/0 | - |
| `urlshort/adapters/migrations/versions/v4_click_events.py` | 12/12 | 0/0 | - |
| `urlshort/adapters/migrations/versions/v5_api_keys.py` | 7/7 | 0/0 | - |
| `urlshort/adapters/postgres.py` | 131/131 | 12/12 | - |
| `urlshort/adapters/redis_cache.py` | 119/119 | 22/22 | - |
| `urlshort/adapters/redis_events.py` | 45/45 | 6/6 | - |
| `urlshort/adapters/redis_ratelimit.py` | 24/24 | 2/2 | - |
| `urlshort/adapters/wiring.py` | 28/28 | 4/4 | - |
| `urlshort/adapters/workers.py` | 43/43 | 14/14 | - |
| `urlshort/admin_cli.py` | 50/50 | 14/14 | - |
| `urlshort/analytics.py` | 46/46 | 16/16 | - |
| `urlshort/api.py` | 47/47 | 8/8 | - |
| `urlshort/audit.py` | 56/56 | 20/20 | - |
| `urlshort/auth.py` | 49/49 | 14/14 | - |
| `urlshort/codegen.py` | 9/9 | 2/2 | - |
| `urlshort/config.py` | 227/227 | 106/106 | - |
| `urlshort/errors.py` | 29/29 | 0/0 | - |
| `urlshort/events.py` | 118/118 | 30/30 | - |
| `urlshort/logging_setup.py` | 92/92 | 24/24 | - |
| `urlshort/main.py` | 17/17 | 0/0 | - |
| `urlshort/models.py` | 39/39 | 0/0 | - |
| `urlshort/ratelimit.py` | 52/52 | 12/12 | - |
| `urlshort/service.py` | 115/115 | 22/22 | - |
| `urlshort/storage.py` | 202/202 | 24/24 | - |
| `urlshort/validation.py` | 82/82 | 42/42 | - |
| `urlshort/web/__init__.py` | 0/0 | 0/0 | - |
| `urlshort/web/clientip.py` | 29/29 | 6/6 | - |
| `urlshort/web/context.py` | 40/40 | 6/6 | - |
| `urlshort/web/middleware.py` | 56/56 | 10/10 | - |
| `urlshort/web/routes_links.py` | 52/52 | 12/12 | - |
| `urlshort/web/routes_ops.py` | 16/16 | 0/0 | - |
| `urlshort/web/routes_redirect.py` | 13/13 | 0/0 | - |

## Unit coverage: `sdlc`
1907/1907 lines and 548/548 branches.

| File | Lines | Branches | Missing |
|---|---|---|---|
| `sdlc/__init__.py` | 1/1 | 0/0 | - |
| `sdlc/agents/__init__.py` | 0/0 | 0/0 | - |
| `sdlc/agents/base.py` | 27/27 | 0/0 | - |
| `sdlc/agents/catalog.py` | 5/5 | 0/0 | - |
| `sdlc/agents/design.py` | 64/64 | 20/20 | - |
| `sdlc/agents/development.py` | 98/98 | 40/40 | - |
| `sdlc/agents/docs.py` | 57/57 | 14/14 | - |
| `sdlc/agents/impact.py` | 79/79 | 34/34 | - |
| `sdlc/agents/qa.py` | 93/93 | 20/20 | - |
| `sdlc/agents/registry.py` | 17/17 | 0/0 | - |
| `sdlc/agents/release.py` | 31/31 | 4/4 | - |
| `sdlc/agents/requirements.py` | 57/57 | 14/14 | - |
| `sdlc/agents/requirements_llm.py` | 50/50 | 18/18 | - |
| `sdlc/agents/review.py` | 70/70 | 34/34 | - |
| `sdlc/agents/security.py` | 23/23 | 6/6 | - |
| `sdlc/approvals.py` | 49/49 | 12/12 | - |
| `sdlc/audit.py` | 41/41 | 12/12 | - |
| `sdlc/cli.py` | 109/109 | 12/12 | - |
| `sdlc/context.py` | 94/94 | 8/8 | - |
| `sdlc/engine.py` | 429/429 | 160/160 | - |
| `sdlc/errors.py` | 4/4 | 0/0 | - |
| `sdlc/graph.py` | 102/102 | 42/42 | - |
| `sdlc/llm.py` | 81/81 | 16/16 | - |
| `sdlc/metrics.py` | 59/59 | 6/6 | - |
| `sdlc/policy.py` | 120/120 | 46/46 | - |
| `sdlc/report.py` | 45/45 | 10/10 | - |
| `sdlc/scenario.py` | 31/31 | 6/6 | - |
| `sdlc/workspace.py` | 71/71 | 14/14 | - |

## Where 100% was not literally achieved
- **Protocol class bodies are excluded from coverage** (`pyproject.toml`): they are interface declarations whose `...` bodies never execute.
- **Bandit low-severity B404/B603 in `sdlc/`** are accepted by design: the orchestrator must run git (argument lists, no shell, absolute executable path). The service has no findings at any severity.
- **Infrastructure adapters** (`urlshort/adapters/`) need real Postgres and Redis: CI measures them in its own job (`make ci-infra` locally). They are included here only if the report was generated with `URLSHORT_TEST_DATABASE_URL` and `URLSHORT_TEST_REDIS_URL` set.
- **Docker-dependent checks** (container smoke test, production refusal) run only in CI, not in this report's local run.

## How CI enforces this
Every pull request runs lint, `mypy --strict`, bandit + pip-audit, the suite on Python 3.11 and 3.12 with separate 100% line-and-branch gates for `urlshort` and `sdlc`, the Docker integration job and the four SDLC scenarios.
