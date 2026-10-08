"""Chaos drills against a running service: break a dependency during live traffic and record what clients see.

    python scripts/chaos_drills.py http://localhost:8000 redis \\
        --stop "docker compose stop redis" --start "docker compose start redis"
    python scripts/chaos_drills.py http://localhost:8000 postgres \\
        --stop "docker compose stop postgres" --start "docker compose start postgres"

Runs redirect traffic for `--seconds`, stops the dependency at 30% and starts it again at 70% of the run,
then prints, per phase (before / during / after), the status codes clients got and /readyz.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import subprocess
import time
from collections import Counter

import httpx

UA = {"user-agent": "Mozilla/5.0 Chrome/126.0"}


async def drill(base: str, name: str, stop: str, start: str, seconds: float) -> dict[str, object]:
    phases: dict[str, Counter[str]] = {"before": Counter(), "during": Counter(), "after": Counter()}
    ready: dict[str, Counter[int]] = {p: Counter() for p in phases}
    async with httpx.AsyncClient(base_url=base, timeout=5) as client:
        codes = []
        for i in range(20):
            made = await client.post("/api/v1/links", json={"url": f"https://example.com/chaos/{name}/{i}"})
            if made.status_code not in (200, 201):
                raise SystemExit(f"setup failed: creating a link returned {made.status_code} {made.text[:200]} "
                                 "(raise URLSHORT_CREATE_BURST for the server under test)")
            codes.append(made.json()["code"])
        began, phase = time.perf_counter(), "before"

        async def traffic() -> None:
            i = 0
            while time.perf_counter() - began < seconds:
                try:
                    resp = await client.get(f"/{codes[i % len(codes)]}", headers=UA)
                    phases[phase][str(resp.status_code)] += 1
                except httpx.HTTPError as exc:
                    phases[phase][type(exc).__name__] += 1
                i += 1

        async def probes() -> None:
            while time.perf_counter() - began < seconds:
                try:
                    ready[phase][(await client.get("/readyz")).status_code] += 1
                except httpx.HTTPError:
                    ready[phase][0] += 1
                await asyncio.sleep(0.25)

        async def chaos() -> None:
            nonlocal phase
            await asyncio.sleep(seconds * 0.3)
            subprocess.run(shlex.split(stop), check=True, capture_output=True)
            phase = "during"
            await asyncio.sleep(seconds * 0.4)
            subprocess.run(shlex.split(start), check=True, capture_output=True)
            phase = "after"
        await asyncio.gather(traffic(), probes(), chaos())
    return {"drill": name, "redirects": {p: dict(c) for p, c in phases.items()},
            "readyz": {p: dict(c) for p, c in ready.items()}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("name")
    parser.add_argument("--stop", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--seconds", type=float, default=12)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(drill(args.base, args.name, args.stop, args.start, args.seconds))))


if __name__ == "__main__":
    main()
