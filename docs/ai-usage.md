# AI usage: decisions and failures

This project was built with an AI assistant (Claude, by Anthropic) as a pair programmer: it proposed
designs, wrote code, tests, documents and git instructions; the developer reviewed every patch, ran it,
applied it through pull requests and made the decisions recorded below. Every commit carries an
`Assisted-by: Claude (Anthropic)` trailer. Commits authored by `sdlc-dev-agent` were produced by the
project's own orchestrator in scenario runs, with the developer recorded as approver in the audit log.

## How AI output was verified

No AI output was trusted on sight. Every patch had to pass, before it was handed over and again in CI:
ruff, `mypy --strict`, bandit and pip-audit, the full suite on Python 3.11 and 3.12 with 100% line and
branch coverage, a Docker integration job, the SDLC scenarios, and a simulated application on a copy of
the developer's repository. Behaviour that unit tests cannot see was checked live: real servers, real log
output, real containers, real concurrent requests. Change plans were applied to their baseline and gated
before any scenario used them.

## Decision ledger

| # | Decision | Proposed by | Alternatives considered | Developer's notes |
|---|---|---|---|---|
| 1 | Build commit by commit from an empty repository, each through a PR with green CI | AI, accepted | One large upload | _add your reasoning_ |
| 2 | Hexagonal service with framework-free rules | AI, accepted | Fat handlers | |
| 3 | 100% line and branch coverage as a CI gate for both packages | AI, accepted | A lower threshold | |
| 4 | Privacy by design: keyed daily visitor IDs, redaction, per-link stats tokens | AI, accepted | Raw IPs; public stats | |
| 5 | GCRA rate limiting (same algorithm as a future Redis backend) | AI, accepted | Token bucket per process | |
| 6 | Production profile that refuses unsafe configuration | AI, accepted | Warnings only | |
| 7 | Orchestrator split into core, agents, scenarios (3 PRs) | AI proposed options; developer chose | One big commit | |
| 8 | Deterministic agents by default; LLM agent validated against a catalog with fallback | AI, accepted | LLM everywhere; no LLM | |
| 9 | Scenarios deliver real features, merged with `promote` | AI, accepted | Demo-only scenarios | |
| 10 | Compress remaining work into 6 functional steps; required deliverables first | Developer asked; AI proposed the split | Continue one commit at a time | |
| 11 | Do not re-run flaky CI until green; fix the cause | AI, accepted | Re-run | |
| 12 | Keep agent authorship on orchestrator-produced commits | AI, accepted | Re-author everything | |

## Failure ledger: where the AI was wrong

| Area | What went wrong | Caught by | Guardrail added |
|---|---|---|---|
| Code | Request log written after the request ID was cleared | Live log inspection | Regression test |
| Code | Test imported from `conftest.py`; import order differed by machine | ruff on the developer's machine | Helpers module; no conftest imports |
| Code | Migration first used f-string SQL with `# nosec` suppressions | Self-review against the project's own SQL rule | Literal-only DDL, no suppressions |
| Code | Smoke-test expectation wrong (curl is a bot) | Running the smoke test | Both cases asserted |
| Code | Queue log handler mutated shared records (lost tracebacks) | Existing 500-error test | Copy before queueing |
| Code | Redaction could be switched off by a caller | Self-review | Enforced in one place |
| Code | Raw traceback on refused production start | Running the entry point | Process entry point with one JSON line |
| Code | **Race in the orchestrator**: a human amendment could be lost | CI (Python 3.11 job only) | Generations, locked rollbacks, deterministic tests, 25-run stress check |
| Code | Design gate failed every cross-cutting change | End-to-end pipeline test | Test kept in CI |
| Code | Baseline export could silently use uncommitted working-tree files | Test of a missing path | Exact export or failure |
| Code | Regex edit broke two files' syntax | ruff | Edits verified by the gates before packaging |
| Code | Two lint errors in a generated change plan | Applying the plan to its baseline | Plans verified before use |
| Policy | PII rule blocked a redaction test | Greenfield scenario | Rule scoped to production code |
| Tests | A test that asserted nothing meaningful | Self-review | Replaced |
| Process | Patches built against an assumed repository state (missing `ruff` setting, extra CI edit) | `git am` failures on the developer's machine | Simulations rebuilt from the developer's real state |
| Process | Instructions that failed or misled: multi-argument `git rev-parse`; tag commands that silently tagged the wrong commit when a patch had not been applied; amending after a failed `git am` | Developer's terminal output | Every instruction now checks its precondition ("must show ...") and stops on failure |
| Process | A pre-recorded approval that was never requested | Reading the audit log | Removed |

## Limits of AI involvement

- The LLM-backed requirements agent is implemented and tested, but **no real model responses are
  recorded in this repository**: the cassette entry used in CI is a labelled hand-written fixture, and run
  reports state this in `interpreted_by`. Record real responses with
  `ANTHROPIC_API_KEY=... SDLC_LLM_RECORD=1 make ambiguous-llm`.
- Approver names in scenario files are illustrative roles; the developer is the approver of record for
  promotions.
