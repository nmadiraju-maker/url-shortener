# Chaos drills

`scripts/chaos_drills.py` runs redirect traffic against a live service (Postgres + Redis configuration), stops a
dependency at 30% of the run, starts it again at 70%, and records what clients received and what `/readyz`
reported. Two findings from the first runs were fixed and the drills re-run.

| Drill | First run | Finding | Fix | After the fix |
|---|---|---|---|---|
| Redis stopped | All redirects 307; `/readyz` 200; **2,825 warning lines** | Correct degradation, but a log storm: one warning per request | Failure logs throttled to one line per 30 s with a suppressed count | All 1,277 redirects 307; **3** warning lines |
| Postgres stopped | **3 redirects** completed during the outage; each waited the 30 s pool timeout | Requests hung instead of failing; cached links could have been served | 3 s pool timeout; database errors become 503 `storage_unavailable` with `Retry-After`; **circuit breaker** fails at once for 5 s after a failure | **1,871 redirects** served (cached links; click writes fail open at once); `/readyz` 503 during; recovered without a restart; **1** error line |

A third drill, a consumer crashing mid-stream in events mode, is covered by an automated test: the unacknowledged
message is reclaimed by another consumer and applied exactly once (`tests/infra/test_events.py`).

Run the drills against docker compose:

```bash
python scripts/chaos_drills.py http://localhost:8000 redis --stop "docker compose stop redis" --start "docker compose start redis"
python scripts/chaos_drills.py http://localhost:8000 postgres --stop "docker compose stop postgres" --start "docker compose start postgres"
```
