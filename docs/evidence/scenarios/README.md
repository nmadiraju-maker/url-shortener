# Scenario evidence

Run reports and key artifacts from the runs that produced v0.10.0 (brownfield) and v0.11.0 (ambiguous),
plus the greenfield build of v0.9.0. CI re-runs all three on every pull request (artifact `sdlc-run-reports`).

| Scenario | Status | Human checkpoints | Retries | Rollbacks | Reworks | Fallbacks | MTTR (s) | End to end (s) |
|---|---|---|---|---|---|---|---|---|
| greenfield | COMPLETED | 3 | 1 | 0 | 0 | 0 | 8.626 | 8.984 |
| brownfield | COMPLETED | 5 | 1 | 3 | 1 | 1 | 0.163 | 8.711 |
| ambiguous | COMPLETED | 6 | 0 | 0 | 0 | 0 | None | 9.886 |

Each folder holds `run-report.md` (stage graph, approvals, policy results, re-plans, decision lineage,
metrics, timeline), `metrics.json`, and the final requirements, design, review, QA and release artifacts.
