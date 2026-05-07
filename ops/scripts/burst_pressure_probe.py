"""burst_pressure_probe.py — Phase 1 PATH_F gate tool (iter-12).

Validates gate criteria: zero 502s + post-burst event_loop_lag p95 < 50 ms
under burst load (default concurrency=12, duration=60s).

Exit codes: 0=PASS, 1=CONCERNS, 2=FAIL, 3=probe-internal-error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Pure helpers (module-level so unit tests can import without network)
# ---------------------------------------------------------------------------

def _parse_status_distribution(codes: list[int]) -> dict[int, int]:
    dist: dict[int, int] = {}
    for c in codes:
        dist[c] = dist.get(c, 0) + 1
    return dist


def _summarize(latencies_ms: list[float]) -> dict[str, float]:
    if not latencies_ms:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    s = sorted(latencies_ms)
    n = len(s)

    def _pct(p: float) -> float:
        idx = max(0, int(p / 100 * n) - 1)
        return round(s[idx], 1)

    return {
        "p50": _pct(50),
        "p95": _pct(95),
        "p99": _pct(99),
        "max": round(s[-1], 1),
    }


def _verdict(r502: float, lag_p95: float) -> str:
    if r502 > 0:
        return "FAIL"
    if lag_p95 >= 50:
        return "CONCERNS"
    return "PASS"


# ---------------------------------------------------------------------------
# Async probe core
# ---------------------------------------------------------------------------

async def _single_get(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
) -> tuple[int, float]:
    """Return (status_code, latency_ms). Returns (0, latency_ms) on error."""
    t0 = time.monotonic()
    try:
        r = await client.get(url, headers=headers, timeout=15.0)
        return r.status_code, (time.monotonic() - t0) * 1000
    except Exception:
        return 0, (time.monotonic() - t0) * 1000


async def _run_burst(
    target: str,
    endpoint: str,
    concurrency: int,
    duration: int,
    headers: dict,
) -> list[tuple[int, float]]:
    url = target.rstrip("/") + endpoint
    results: list[tuple[int, float]] = []
    deadline = time.monotonic() + duration

    async with httpx.AsyncClient(http2=False) as client:
        pending: set[asyncio.Task] = set()

        # Seed initial wave
        for _ in range(concurrency):
            if time.monotonic() < deadline:
                t = asyncio.create_task(_single_get(client, url, headers))
                pending.add(t)

        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                results.append(task.result())
                if time.monotonic() < deadline:
                    nt = asyncio.create_task(_single_get(client, url, headers))
                    pending.add(nt)

        # Drain in-flight with grace period
        if pending:
            try:
                done2, _ = await asyncio.wait(pending, timeout=10.0)
                for t in done2:
                    results.append(t.result())
            except Exception:
                pass

    return results


async def _fetch_loop_lag(target: str, headers: dict) -> dict[str, float]:
    """GET /api/health once and extract event_loop_lag if present."""
    url = target.rstrip("/") + "/api/health"
    try:
        async with httpx.AsyncClient(http2=False) as client:
            r = await client.get(url, headers=headers, timeout=10.0)
            if r.status_code == 200:
                body = r.json()
                lag = body.get("event_loop_lag", {})
                if isinstance(lag, dict):
                    return {
                        "p50_ms": float(lag.get("p50_ms", 0)),
                        "p95_ms": float(lag.get("p95_ms", 0)),
                        "max_ms": float(lag.get("max_ms", 0)),
                    }
    except Exception:
        pass
    return {"p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _build_headers(bearer: Optional[str]) -> dict:
    h: dict = {"Accept": "application/json"}
    if bearer:
        h["Authorization"] = "Bearer <private>[token]</private>"
        # Actual value sent in wire — never printed
        h["Authorization"] = f"Bearer {bearer}"
    return h


async def _main(args: argparse.Namespace) -> int:
    bearer = args.bearer_token or os.environ.get("RAG_PROBE_BEARER")
    headers = _build_headers(bearer)

    print(
        f"burst_pressure_probe target={args.target} "
        f"concurrency={args.concurrency} duration={args.duration}s"
    )
    print(f"endpoint={args.endpoint}  bearer={'set' if bearer else 'none'}")
    print("running...", flush=True)

    try:
        results = await _run_burst(
            args.target, args.endpoint, args.concurrency, args.duration, headers
        )
    except Exception as exc:
        print(f"probe-internal-error: {exc}", file=sys.stderr)
        return 3

    codes = [r[0] for r in results]
    lats = [r[1] for r in results]
    dist = _parse_status_distribution(codes)
    lat_stats = _summarize(lats)

    total = len(results)
    n502 = dist.get(502, 0)
    n503 = dist.get(503, 0)
    r502 = round(n502 / total, 4) if total else 0.0
    r503 = round(n503 / total, 4) if total else 0.0

    lag = await _fetch_loop_lag(args.target, headers)
    verd = _verdict(r502, lag["p95_ms"])

    print(f"total_requests={total}")
    print(f"status_distribution: {dist}")
    print(f"502_rate={r502}  503_rate={r503}")
    print(
        f"latency_ms_p50={lat_stats['p50']}  p95={lat_stats['p95']}  "
        f"p99={lat_stats['p99']}  max={lat_stats['max']}"
    )
    print(
        f"post_burst_event_loop_lag_p50_ms={lag['p50_ms']}  "
        f"p95_ms={lag['p95_ms']}  max_ms={lag['max_ms']}"
    )
    print(f"verdict: {verd}")

    machine = {
        "probe": "burst_pressure_probe",
        "target": args.target,
        "concurrency": args.concurrency,
        "duration_s": args.duration,
        "total": total,
        "status": {str(k): v for k, v in dist.items()},
        "r502": r502,
        "r503": r503,
        "lat": lat_stats,
        "loop_lag": lag,
        "verdict": verd,
    }
    print(json.dumps(machine))

    return {"PASS": 0, "CONCERNS": 1, "FAIL": 2}.get(verd, 3)


def main() -> None:
    parser = argparse.ArgumentParser(description="Burst pressure probe (iter-12 gate)")
    parser.add_argument("--target", default="https://zettelkasten.in")
    parser.add_argument("--endpoint", default="/api/health")
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--bearer-token", default=None, dest="bearer_token")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
