#!/usr/bin/env python3
"""Real load test for a Mission Control (or any agentkit HTTP surface). No mocks: open sockets, real requests, real latency.

  python scripts/load_test.py --base http://127.0.0.1:8110 --concurrency 10,100,500 --seconds 10 [--procs 8]
  python scripts/load_test.py --base ... --endpoints /api/status,/.well-known/agent-card.json --json out.json

--procs spreads the connections over N client processes so the generator itself is not the bottleneck (a single Python
process cannot drive 500 connections faster than a few hundred requests/s). Reports, per endpoint and concurrency level:
requests/s, p50/p95/p99 latency, error rate (non-2xx or transport error), slowest request. Exit 1 if any level breaks the bar."""
from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import statistics
import sys
import time

import httpx

DEFAULT_ENDPOINTS = ["/api/status", "/api/runs?limit=50", "/api/approvals", "/api/activity?limit=200", "/api/docs/leads",
                     "/.well-known/agent-card.json", "/api/health", "/api/evidence", "/"]


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, round(p / 100 * (len(xs) - 1))))]


async def _worker(client: httpx.AsyncClient, url: str, deadline: float, lat: list[float], errs: list[str], headers: dict) -> None:
    while time.perf_counter() < deadline:
        t0 = time.perf_counter()
        try:
            r = await client.get(url, headers=headers)
            if r.status_code >= 300:
                errs.append(f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001 — transport errors are part of the measurement
            errs.append(type(e).__name__)
        lat.append((time.perf_counter() - t0) * 1000)


async def _drive(base: str, path: str, conc: int, seconds: float, headers: dict) -> tuple[list[float], list[str], float]:
    lat: list[float] = []
    errs: list[str] = []
    async with httpx.AsyncClient(base_url=base, timeout=30, limits=httpx.Limits(max_connections=conc, max_keepalive_connections=conc)) as client:
        deadline = time.perf_counter() + seconds
        t0 = time.perf_counter()
        await asyncio.gather(*(_worker(client, path, deadline, lat, errs, headers) for _ in range(conc)))
        return lat, errs, time.perf_counter() - t0


def _proc(args) -> tuple[list[float], list[str], float]:
    base, path, conc, seconds, headers = args
    return asyncio.run(_drive(base, path, conc, seconds, headers))


def level(base: str, path: str, conc: int, seconds: float, headers: dict, procs: int) -> dict:
    procs = max(1, min(procs, conc))
    share = [conc // procs + (1 if i < conc % procs else 0) for i in range(procs)]
    if procs == 1:
        parts = [_proc((base, path, conc, seconds, headers))]
    else:
        with mp.Pool(procs) as pool:
            parts = pool.map(_proc, [(base, path, c, seconds, headers) for c in share if c])
    lat = [x for p in parts for x in p[0]]
    errs = [x for p in parts for x in p[1]]
    wall = max(p[2] for p in parts)
    n = len(lat)
    kinds: dict[str, int] = {}
    for e in errs:
        kinds[e] = kinds.get(e, 0) + 1
    return {"endpoint": path, "concurrency": conc, "client_procs": procs, "requests": n, "rps": round(n / wall, 1) if wall else 0,
            "error_rate": round(len(errs) / n, 4) if n else 1.0, "errors": kinds, "p50_ms": round(pct(lat, 50), 1), "p95_ms": round(pct(lat, 95), 1),
            "p99_ms": round(pct(lat, 99), 1), "max_ms": round(max(lat), 1) if lat else 0, "mean_ms": round(statistics.fmean(lat), 1) if lat else 0}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--endpoints", default=",".join(DEFAULT_ENDPOINTS))
    ap.add_argument("--concurrency", default="10,100,500")
    ap.add_argument("--seconds", type=float, default=8)
    ap.add_argument("--procs", type=int, default=1)
    ap.add_argument("--token", default="")
    ap.add_argument("--max-error-rate", type=float, default=0.001)
    ap.add_argument("--max-p95-ms", type=float, default=500)
    ap.add_argument("--json")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    headers = {"X-Agent-Token": a.token} if a.token else {}
    rows = []
    for path in [e for e in a.endpoints.split(",") if e]:
        for conc in [int(c) for c in a.concurrency.split(",")]:
            r = level(a.base, path, conc, a.seconds, headers, a.procs)
            rows.append(r)
            flag = "" if (r["error_rate"] <= a.max_error_rate and r["p95_ms"] <= a.max_p95_ms) else "  <-- FAIL"
            print(f"{path:<34} c={conc:<5} {r['rps']:>8.1f} rps  p50 {r['p50_ms']:>8.1f}  p95 {r['p95_ms']:>8.1f}  p99 {r['p99_ms']:>8.1f}  max {r['max_ms']:>8.1f} ms  err {r['error_rate']*100:5.1f}% {r['errors'] or ''}{flag}", flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"base": a.base, "seconds": a.seconds, "procs": a.procs, "results": rows}, f, indent=2)
    bad = [r for r in rows if r["error_rate"] > a.max_error_rate or r["p95_ms"] > a.max_p95_ms]
    print(f"\n{'PASS' if not bad else 'FAIL'}: {len(rows) - len(bad)}/{len(rows)} endpoint×concurrency levels within error<={a.max_error_rate*100:.1f}% and p95<={a.max_p95_ms}ms")
    return 1 if bad else 0


if __name__ == "__main__":
    mp.freeze_support()
    sys.exit(main())
