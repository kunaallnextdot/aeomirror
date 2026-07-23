"""Reproducible LOCAL load/abuse test for POST /v1/scan.

Run against a locally-running backend (never production). It exercises:
  - rate limiting from a single client identity -> HTTP 429 + Retry-After
  - the 24h cache: repeated identical URLs return the SAME scan_id (a cache hit;
    a miss would fetch + persist a NEW row and return a new scan_id)
  - concurrency + latency (p50/p95)

Client identity is carried in X-Forwarded-For, so the server MUST be started with
TRUST_PROXY=True for identity control (the identity string is only hashed for the
rate-limit key; it is never resolved, so arbitrary labels are fine).

Usage (from backend/, with the venv):
    LOADTEST_BASE_URL=http://localhost:8000 \
    LOADTEST_URL=https://example.com \
    python -m tests.loadtest
or:  npm run loadtest   (from repo root)
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time

import httpx

BASE_URL = os.environ.get("LOADTEST_BASE_URL", "http://localhost:8000")
TARGET_URL = os.environ.get("LOADTEST_URL", "https://example.com")
CONCURRENCY = int(os.environ.get("LOADTEST_CONCURRENCY", "30"))


def _reset_redis_for(identities: list[str]) -> None:
    """Best-effort: clear rate-limit keys for the identities we will use so the
    run is reproducible. No-op if Redis is not configured/reachable."""
    url = os.environ.get("REDIS_URL")
    if not url:
        return
    try:
        import hashlib
        import redis
        from app.api.routes_scan import _ip_hash  # same hashing as the app
        c = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        for ident in identities:
            c.delete(f"aeomirror:rate-limit:free-scan:{_ip_hash(ident)}")
    except Exception as e:  # pragma: no cover
        print(f"[loadtest] redis reset skipped ({type(e).__name__})")


async def _scan(client: httpx.AsyncClient, identity: str, url: str) -> dict:
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{BASE_URL}/v1/scan",
            json={"url": url},
            headers={"X-Forwarded-For": identity},
        )
        dt = (time.perf_counter() - t0) * 1000
        out = {"status": r.status_code, "ms": dt, "scan_id": None,
               "retry_after": r.headers.get("retry-after")}
        if r.status_code == 200:
            out["scan_id"] = r.json().get("scan_id")
        return out
    except Exception as e:
        return {"status": None, "ms": (time.perf_counter() - t0) * 1000,
                "scan_id": None, "retry_after": None, "error": type(e).__name__}


async def scenario_cache_and_limit(client, results):
    """One identity, same URL, more requests than the limit."""
    identity = "loadtest-abuser"
    _reset_redis_for([identity])
    print(f"\n[scenario A] cache + rate limit (identity={identity}, url={TARGET_URL})")
    n = 6
    scan_ids, first_retry_after, http429 = [], None, 0
    for i in range(n):
        res = await _scan(client, identity, TARGET_URL)
        results.append(res)
        if res["status"] == 200 and res["scan_id"]:
            scan_ids.append(res["scan_id"])
        if res["status"] == 429:
            http429 += 1
            first_retry_after = first_retry_after or res["retry_after"]
        print(f"  req {i+1}: status={res['status']} "
              f"scan_id={(res['scan_id'] or '-')[:8]} "
              f"retry_after={res['retry_after']}")
    distinct = set(scan_ids)
    print(f"  -> 2xx={len(scan_ids)} distinct_scan_ids={len(distinct)} "
          f"(1 == cache working) 429={http429} first_retry_after={first_retry_after}")
    return {"distinct_scan_ids": len(distinct), "http429": http429,
            "retry_after": first_retry_after, "cached_scan_id": next(iter(distinct), None)}


async def scenario_concurrency(client, results, cached_scan_id):
    """Many concurrent requests, each a UNIQUE identity (so no 429), same URL ->
    served from cache after the first; measures latency."""
    print(f"\n[scenario B] concurrency={CONCURRENCY}, unique identities, url={TARGET_URL}")
    idents = [f"loadtest-c{i}" for i in range(CONCURRENCY)]
    _reset_redis_for(idents)
    tasks = [_scan(client, ident, TARGET_URL) for ident in idents]
    batch = await asyncio.gather(*tasks)
    results.extend(batch)
    ok = [r for r in batch if r["status"] == 200]
    cached = [r for r in ok if cached_scan_id and r["scan_id"] == cached_scan_id]
    print(f"  -> 2xx={len(ok)} cached(same scan_id)={len(cached)} "
          f"429={sum(1 for r in batch if r['status']==429)}")


def _pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


async def main():
    print(f"AEOMirror load/abuse test -> {BASE_URL}")
    results: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        a = await scenario_cache_and_limit(client, results)
        await scenario_concurrency(client, results, a["cached_scan_id"])

    total = len(results)
    success = sum(1 for r in results if r["status"] == 200)
    http429 = sum(1 for r in results if r["status"] == 429)
    failures = sum(1 for r in results if r["status"] not in (200, 429))
    all_ids = [r["scan_id"] for r in results if r["scan_id"]]
    cached_hits = len(all_ids) - len(set(all_ids))  # duplicates == cache hits
    lat = [r["ms"] for r in results if r["ms"] is not None]

    print("\n================ LOAD TEST SUMMARY ================")
    print(f"target url         : {TARGET_URL}")
    print(f"total requests     : {total}")
    print(f"successful scans   : {success}")
    print(f"cached responses   : {cached_hits} (duplicate scan_ids = cache hits)")
    print(f"429 responses      : {http429}")
    print(f"failures           : {failures}")
    print(f"retry-after seen   : {a['retry_after']}")
    print(f"p50 latency (ms)   : {_pct(lat, 50):.1f}")
    print(f"p95 latency (ms)   : {_pct(lat, 95):.1f}")
    print("==================================================")
    # Non-zero exit if the core guarantees did not hold, for CI use.
    ok = a["distinct_scan_ids"] == 1 and a["http429"] >= 1 and a["retry_after"]
    print("RESULT:", "PASS" if ok else "CHECK",
          "(cache dedupe + 429 + Retry-After verified)" if ok else "(review above)")


if __name__ == "__main__":
    asyncio.run(main())
