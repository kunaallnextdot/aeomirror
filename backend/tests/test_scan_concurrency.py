"""Regression: concurrent anonymous scans must never stall the API.

Root cause being guarded: POST /v1/scan is an async handler. It ran synchronous DB
queries on the event loop, and its request session checked out a pool connection at
the first query and held it across the remote page fetch. A burst of scans against
slow sites drained the request pool; the next checkout then blocked the WHOLE event
loop until DB_POOL_TIMEOUT (30 s), freezing every request in the process, /healthz
included.

These tests run in-process against the real QueuePool (SQLite file DB in tests,
QueuePool 5 + 10) with the page fetch stubbed — no network.
"""
import asyncio
import threading
import time

import httpx
import pytest

import app.api.routes_scan as rs
from app.config import settings
from app.core.cache import rate_limiter, scan_cache
from app.db.models import Scan
from app.db.session import SessionLocal, engine
from app.main import app
from app.scanner.models import PageBundle
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS

BASE = "http://test"


def _page(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(rate_limiter, "limit", 10_000)
    rate_limiter._hits.clear()
    scan_cache._store.clear()
    yield
    rate_limiter._hits.clear()
    scan_cache._store.clear()


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=60)


def _scan_rows(prefix):
    db = SessionLocal()
    try:
        return db.query(Scan).filter(Scan.url.like(f"https://{prefix}%")).count()
    finally:
        db.close()


def test_the_event_loop_keeps_serving_while_a_scan_waits_for_an_exhausted_pool(monkeypatch):
    """Something else holds EVERY request-pool connection. The scan's DB step must
    wait in a worker thread, not on the event loop: requests that don't need the
    database keep being served (before the fix: blocked for the whole wait)."""
    async def fake_fetch(url):
        return _page(url)
    monkeypatch.setattr(rs, "fetch", fake_fetch)
    hogs = [engine.connect() for _ in range(engine.pool.size() + engine.pool._max_overflow)]
    for c in hogs:
        c.exec_driver_sql("select 1")
    release = threading.Timer(1.5, lambda: [c.close() for c in hogs])
    release.start()

    async def main():
        async with _client() as c:
            scan = asyncio.create_task(c.post("/v1/scan", json={"url": "https://pool-exhausted.example/"}))
            worst = 0.0
            for _ in range(20):                    # loop lag + a DB-free endpoint, whole window
                t = time.perf_counter()
                await asyncio.sleep(0.05)
                r = await c.get("/")
                worst = max(worst, time.perf_counter() - t - 0.05)
                assert r.status_code == 200
            return worst, await scan
    try:
        worst, res = asyncio.run(main())
    finally:
        release.join()
    assert worst < 0.25, f"the event loop stalled {worst:.2f}s behind a blocked pool checkout"
    assert res.status_code == 200                  # the scan completes once the pool frees


def test_healthz_stays_fast_under_a_burst_of_slow_anonymous_scans(monkeypatch):
    """24 concurrent scans of slow sites (more than the whole request pool): scans are
    capped per process and hold no connection while fetching, so /healthz — which
    itself checks the database — is never starved."""
    async def slow_fetch(url):
        await asyncio.sleep(0.6)
        return _page(url)
    monkeypatch.setattr(rs, "fetch", slow_fetch)

    async def main():
        async with _client() as c:
            scans = [asyncio.create_task(c.post("/v1/scan", json={"url": f"https://burst-{i}.example/"}))
                     for i in range(24)]
            lat = []
            while not all(s.done() for s in scans):
                t = time.perf_counter()
                r = await c.get("/healthz")
                lat.append(time.perf_counter() - t)
                assert r.status_code == 200 and r.json()["database"] == "ok"
                await asyncio.sleep(0.02)
            return lat, [s.result().status_code for s in scans], engine.pool.checkedout()
    lat, codes, checked_out = asyncio.run(main())
    assert codes == [200] * 24
    assert len(lat) >= 20 and max(lat) < 0.25, f"/healthz max {max(lat):.2f}s"
    assert checked_out == 0                        # no session leaked
    assert _scan_rows("burst-") == 24


def test_no_pool_connection_is_held_while_pages_are_fetched(monkeypatch):
    monkeypatch.setattr(settings, "scan_max_concurrency", 6)
    in_fetch, go = [], asyncio.Event()

    async def slow_fetch(url):
        in_fetch.append(url)
        await go.wait()
        return _page(url)
    monkeypatch.setattr(rs, "fetch", slow_fetch)

    async def main():
        async with _client() as c:
            base = engine.pool.checkedout()
            tasks = [asyncio.create_task(c.post("/v1/scan", json={"url": f"https://held-{i}.example/"}))
                     for i in range(6)]
            for _ in range(200):
                if len(in_fetch) == 6:
                    break
                await asyncio.sleep(0.01)
            held = engine.pool.checkedout() - base
            go.set()
            res = await asyncio.gather(*tasks)
            return held, res, engine.pool.checkedout() - base
    held, res, after = asyncio.run(main())
    assert held == 0, f"{held} pool connection(s) pinned across the remote fetch"
    assert [r.status_code for r in res] == [200] * 6
    assert after == 0                                # nothing leaked once requests finish
    assert _scan_rows("held-") == 6                  # one row per scan, no duplicates


def test_concurrent_scans_are_bounded_per_process(monkeypatch):
    monkeypatch.setattr(settings, "scan_max_concurrency", 2)
    active, peak = [0], [0]

    async def slow_fetch(url):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.1)
        active[0] -= 1
        return _page(url)
    monkeypatch.setattr(rs, "fetch", slow_fetch)

    async def main():
        async with _client() as c:
            return await asyncio.gather(*(c.post("/v1/scan", json={"url": f"https://bounded-{i}.example/"})
                                          for i in range(7)))
    res = asyncio.run(main())
    assert [r.status_code for r in res] == [200] * 7   # queued, not refused
    assert peak[0] == 2
    assert _scan_rows("bounded-") == 7


def test_a_scan_that_cannot_get_a_slot_in_time_is_told_to_retry(monkeypatch):
    monkeypatch.setattr(settings, "scan_max_concurrency", 1)
    monkeypatch.setattr(settings, "scan_queue_timeout_seconds", 0.2)
    go = asyncio.Event()

    async def slow_fetch(url):
        await go.wait()
        return _page(url)
    monkeypatch.setattr(rs, "fetch", slow_fetch)

    async def main():
        async with _client() as c:
            first = asyncio.create_task(c.post("/v1/scan", json={"url": "https://slot-a.example/"}))
            await asyncio.sleep(0.05)
            second = await c.post("/v1/scan", json={"url": "https://slot-b.example/"})
            go.set()
            return await first, second
    first, second = asyncio.run(main())
    assert first.status_code == 200
    assert second.status_code == 503 and int(second.headers["retry-after"]) > 0
    assert _scan_rows("slot-b") == 0


def test_the_rate_limit_still_counts_every_concurrent_request(monkeypatch):
    monkeypatch.setattr(rate_limiter, "limit", 3)

    async def fake_fetch(url):
        await asyncio.sleep(0.05)
        return _page(url)
    monkeypatch.setattr(rs, "fetch", fake_fetch)

    async def main():
        async with _client() as c:
            return await asyncio.gather(*(c.post("/v1/scan", json={"url": f"https://limited-{i}.example/"})
                                          for i in range(6)))
    codes = sorted(r.status_code for r in asyncio.run(main()))
    assert codes == [200, 200, 200, 429, 429, 429]
    assert _scan_rows("limited-") == 3


def test_maintenance_mode_still_blocks_anonymous_scans(monkeypatch):
    from app.admin import settings_store

    async def fake_fetch(url):
        return _page(url)
    monkeypatch.setattr(rs, "fetch", fake_fetch)
    db = SessionLocal()
    try:
        settings_store.set_value(db, "maintenance_mode", True)
        settings_store.set_value(db, "maintenance_message", "Back soon.")

        async def main():
            async with _client() as c:
                return await c.post("/v1/scan", json={"url": "https://maint.example/"})
        r = asyncio.run(main())
        assert r.status_code == 503 and r.json()["detail"] == "Back soon."
    finally:
        settings_store.set_value(db, "maintenance_mode", False)
        db.close()


def test_a_cache_hit_returns_the_same_payload_without_a_new_row(monkeypatch):
    calls = []

    async def fake_fetch(url):
        calls.append(url)
        return _page(url)
    monkeypatch.setattr(rs, "fetch", fake_fetch)

    async def main():
        async with _client() as c:
            a = await c.post("/v1/scan", json={"url": "https://cached.example/"})
            b = await c.post("/v1/scan", json={"url": "https://cached.example/"})
            return a, b
    a, b = asyncio.run(main())
    assert a.status_code == b.status_code == 200 and len(calls) == 1
    assert a.json()["scan_id"] == b.json()["scan_id"]
    assert _scan_rows("cached.") == 1


def test_concurrent_first_use_of_redis_never_falls_back_to_the_local_limiter(monkeypatch):
    """The limiter now runs in worker threads: two first calls racing get_redis() must
    both get the shared client — never None (which silently used the per-process
    in-memory window and let a burst exceed the limit across processes)."""
    import sys
    import types

    import app.core.cache as cache_mod
    made = []

    class SlowRedis:
        @staticmethod
        def from_url(url, **kw):
            time.sleep(0.2)                        # client construction takes a moment
            made.append(url)
            return object()
    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=SlowRedis))
    monkeypatch.setattr(cache_mod.settings, "redis_url", "redis://example:6379/0")
    monkeypatch.setattr(cache_mod, "_redis_client", None)
    monkeypatch.setattr(cache_mod, "_redis_init", False)
    got = []
    threads = [threading.Thread(target=lambda: got.append(cache_mod.get_redis())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(made) == 1 and all(g is not None for g in got) and len({id(g) for g in got}) == 1
