# URL Shortener

A URL shortener service with core APIs, analytics and reliability features, built incrementally.
Every change goes through the CI quality gates below.

**Status:** walking skeleton — the service exposes `/healthz` only. Features arrive in later commits.

## Setup
```bash
python3.11 -m venv .venv && source .venv/bin/activate   # Python 3.11+
make install-dev
```

## Run
```bash
make run                     # http://localhost:8000/healthz, API docs at /docs
```

## Quality gates
`.github/workflows/ci.yml` runs on every push and pull request:

| Job | What it enforces |
|---|---|
| Lint | ruff (pyflakes, pycodestyle, import order, bugbear, pyupgrade) |
| Type check | `mypy --strict` on the service package |
| Security | bandit (no findings at any severity), pip-audit (no known vulnerable dependencies) |
| Tests | Python 3.11 and 3.12, **100% line and branch coverage** |
| Integration | Docker image builds, runs as non-root, passes a black-box smoke test |

Run the same gates locally with `make ci`.
