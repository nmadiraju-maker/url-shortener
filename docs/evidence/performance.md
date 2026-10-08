# Performance evidence

Measured with `scripts/loadtest.py` (16 concurrent clients, 8 s per phase, 100 seeded links; rate limits raised
so the service rather than the limiter is measured; request logs sampled at 1%).

**Environment, read this first:** a sandbox with **one CPU core**, with the load generator, the service (one
uvicorn worker), Postgres and Redis all competing for it. These numbers compare configurations; they are a
lower bound, not a capacity claim. In particular, **NFR-1 (redirect p99 < 50 ms at 500 rps) is not demonstrated
here**: the p99 values below are dominated by CPU contention between 16 clients and the server.

| Configuration | Redirects/s | Redirect p50 | Redirect p95 | Redirect p99 | Creates/s | Create p50 | Create p99 | Errors |
|---|---|---|---|---|---|---|---|---|
| SQLite, sync analytics | 292 | 33 ms | 158 ms | 311 ms | 266 | 53 ms | 158 ms | 0 |
| SQLite, events mode | 292 | 30 ms | 165 ms | 283 ms | 263 | 54 ms | 158 ms | 0 |
| Postgres + Redis | 232 | 34 ms | 213 ms | 352 ms | 162 | 56 ms | 522 ms | 0 |

Reading the results:

- No configuration produced an error under sustained load.
- Events mode matches sync mode here: with SQLite in the same process, moving the click insert to an outbox row
  saves little. Its value is decoupling analytics from redirects, not single-node speed.
- Postgres + Redis is slower on one core: every request makes extra round trips to two more processes competing
  for the same CPU. Its value is horizontal scale (shared state across instances), which one core cannot show.

To validate NFR-1, run the same script from a separate machine against several instances behind a load balancer.
