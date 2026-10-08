"""Load test a running service:  python scripts/loadtest.py http://localhost:8000 --seconds 10 --concurrency 32

Creates 100 links, then runs a redirect phase (the hot path) and a create phase, each for `--seconds` with
`--concurrency` concurrent clients. Prints throughput, latency percentiles and errors as JSON. Raise the rate
limits for the server under test (URLSHORT_REDIRECT_RATE_PER_MIN etc.), or you measure the limiter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from collections.abc import Awaitable, Callable

import httpx

Request = Callable[[httpx.AsyncClient, int, int], Awaitable[httpx.Response]]


async def phase(client: httpx.AsyncClient, seconds: float, concurrency: int, request: Request) -> dict[str, float]:
    latencies: list[float] = []
    errors = 0
    deadline = time.perf_counter() + seconds

    async def worker(n: int) -> None:
        nonlocal errors
        i = 0
        while time.perf_counter() < deadline:
            started = time.perf_counter()
            resp = await request(client, n, i)
            latencies.append(time.perf_counter() - started)
            errors += resp.status_code >= 400
            i += 1
    started = time.perf_counter()
    await asyncio.gather(*(worker(n) for n in range(concurrency)))
    elapsed = time.perf_counter() - started
    q = statistics.quantiles(latencies, n=100)
    return {"requests": len(latencies), "rps": round(len(latencies) / elapsed, 1), "errors": errors,
            "p50_ms": round(q[49] * 1000, 2), "p95_ms": round(q[94] * 1000, 2), "p99_ms": round(q[98] * 1000, 2)}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--concurrency", type=int, default=32)
    args = parser.parse_args()
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(base_url=args.base, limits=limits, timeout=10) as client:
        codes = [(await client.post("/api/v1/links", json={"url": f"https://example.com/seed/{i}"})).json()["code"]
                 for i in range(100)]
        ua = {"user-agent": "Mozilla/5.0 Chrome/126.0"}

        async def redirect(c: httpx.AsyncClient, n: int, i: int) -> httpx.Response:
            return await c.get(f"/{random.choice(codes)}", headers=ua)

        async def create(c: httpx.AsyncClient, n: int, i: int) -> httpx.Response:
            return await c.post("/api/v1/links", json={"url": f"https://example.com/load/{n}/{i}"})
        result = {"redirect": await phase(client, args.seconds, args.concurrency, redirect),
                  "create": await phase(client, args.seconds, args.concurrency, create)}
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
