"""URL Intelligence Engine: sitemap discovery, internal-link discovery, dedup, SSRF/
robots/off-site rejection, and the CrawlJob phase state machine
(app/services/url_intelligence/{discovery,orchestrator}.py). The real network is
never used — `fetch` is monkeypatched, same convention as tests/test_rbac.py. This
codebase has no pytest-asyncio/anyio pytest plugin configured, so each async
operation under test is driven with a plain `asyncio.run(...)` from an ordinary sync
test function, exactly like the production code itself is driven from sync call
sites (e.g. the monitoring worker's `asyncio.run`-free event loop)."""
import asyncio

import pytest

from app.config import settings
from app.db.models import CRAWL_ANALYZING, CRAWL_CRAWLING, CRAWL_DISCOVERING, CrawlJob, CrawlUrl, URL_SKIPPED
from app.db.session import SessionLocal
from app.scanner.models import PageBundle
from app.services.url_intelligence import discovery, orchestrator
from tests.authutil import auth_client

_SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://example.com/a</loc></url>
<url><loc>https://example.com/b</loc></url>
<url><loc>https://not-this-site.example/off-site</loc></url>
</urlset>"""

_PAGE_A_HTML = """<html><head><title>Page A</title></head><body>
<a href="/c">link to c</a><a href="https://external.example/x">external</a>
</body></html>"""

_PAGE_B_HTML = "<html><head><title>Page B</title></head><body>no links here</body></html>"
_PAGE_C_HTML = "<html><head><title>Page C</title></head><body>leaf page</body></html>"

_PAGES = {
    "https://example.com/sitemap.xml": _SITEMAP,
    "https://example.com/robots.txt": "",
    "https://example.com/a": _PAGE_A_HTML,
    "https://example.com/b": _PAGE_B_HTML,
    "https://example.com/c": _PAGE_C_HTML,
}


async def _fake_fetch(url, **kwargs):
    html = _PAGES.get(url, "")
    return PageBundle(url=url, html=html, status_code=200 if url in _PAGES else 404)


def _job(db, org_id, user_id, max_urls=100):
    j = CrawlJob(organization_id=org_id, user_id=user_id, monitor_id="m1",
                seed_url="https://example.com/", normalized_seed_url="https://example.com/",
                max_urls=max_urls)
    db.add(j); db.commit(); db.refresh(j)
    return j


@pytest.fixture(autouse=True)
def _patch_fetch(monkeypatch):
    monkeypatch.setattr(discovery, "fetch", _fake_fetch)
    monkeypatch.setattr(settings, "allow_private_hosts", True)


async def _discover_to_completion(db, job):
    for _ in range(10):
        if await discovery.run_discovery_chunk(db, job):
            return
    raise AssertionError("discovery never finished")


async def _crawl_to_completion(db, job):
    for _ in range(10):
        if await discovery.run_crawl_chunk(db, job):
            return
    raise AssertionError("crawl phase never finished")


def test_sitemap_urls_are_discovered_and_deduped():
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        asyncio.run(_discover_to_completion(db, job))
        eligible = {r.url for r in db.query(CrawlUrl)
                   .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status != URL_SKIPPED).all()}
        assert eligible == {"https://example.com/a", "https://example.com/b"}
        assert job.discovered_count == 3   # a, b, + the off-site sitemap entry (skipped, still counted)
    finally:
        db.close()


def test_crawl_phase_discovers_internal_links_within_depth():
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        asyncio.run(_discover_to_completion(db, job))
        asyncio.run(_crawl_to_completion(db, job))
        urls = {r.url for r in db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == job.id).all()}
        # /a linked to /c (internal) — discovered via crawling, not the sitemap.
        assert "https://example.com/c" in urls
        # The external link is never followed (same-origin only).
        assert "https://external.example/x" not in urls
        assert job.crawled_count == 3   # a, b, c all fetched
    finally:
        db.close()


def test_off_site_links_are_skipped_not_crawled():
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        asyncio.run(_discover_to_completion(db, job))
        skipped = (db.query(CrawlUrl)
                  .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_SKIPPED).all())
        assert any(r.skip_reason == "off_site" for r in skipped)
    finally:
        db.close()


def test_max_urls_budget_is_never_exceeded():
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"], max_urls=1)
        asyncio.run(_discover_to_completion(db, job))
        assert job.discovered_count <= 1
    finally:
        db.close()


def test_orchestrator_advances_through_phases_to_completed(monkeypatch):
    from app.core import ai
    monkeypatch.setattr(ai, "complete_with_usage", lambda *a, **k: (None, None))  # deterministic-only

    async def _drive(db, job):
        seen_phases = set()
        for _ in range(50):
            seen_phases.add(job.phase)
            await orchestrator.run_chunk(db, job)
            if orchestrator.is_terminal(job.phase):
                break
        return seen_phases

    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        seen_phases = asyncio.run(_drive(db, job))
        assert job.phase == "completed"
        assert {CRAWL_DISCOVERING, CRAWL_CRAWLING, CRAWL_ANALYZING} <= seen_phases
        assert job.analyzed_count == job.crawled_count   # every crawled URL got analyzed
    finally:
        db.close()


# ------------------------------- performance: no doomed per-link work -------------------------------

_DUP_PAGE = "<html><body>" + "".join(f'<a href="/p{i % 5}">x</a>' for i in range(200)) + "</body></html>"


def test_repeated_links_are_skipped_before_any_dns_lookup(monkeypatch):
    """A page's links are mostly repeats (nav, footer). Each used to cost a DNS lookup,
    an INSERT and a rollback on the event loop before the unique index rejected it."""
    import socket
    from app.core import ssrf
    monkeypatch.setattr(settings, "allow_private_hosts", False)
    lookups = []
    def fake_getaddrinfo(host, *a, **k):
        lookups.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    pages = {**_PAGES, "https://example.com/a": _DUP_PAGE}

    async def fetch_dup(url, **kwargs):
        return PageBundle(url=url, html=pages.get(url, ""), status_code=200)
    monkeypatch.setattr(discovery, "fetch", fetch_dup)

    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        asyncio.run(_discover_to_completion(db, job))
        lookups.clear()
        asyncio.run(discovery.run_crawl_chunk(db, job))
        new = {r.url for r in db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == job.id,
                                                       CrawlUrl.discovery_source == "internal_link")}
        assert new == {f"https://example.com/p{i}" for i in range(5)}      # same dedup outcome
        assert len(lookups) <= 3     # one per host per chunk — was one per link (200+)
    finally:
        db.close()


def test_dns_cache_reuses_a_host_but_still_rejects_private_addresses(monkeypatch):
    import socket
    from app.core import ssrf
    monkeypatch.setattr(settings, "allow_private_hosts", False)
    calls = []
    def fake(host, *a, **k):
        calls.append(host)
        ip = "10.0.0.5" if host == "internal.example" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake)
    cache: dict = {}
    for p in ("a", "b", "c"):
        ssrf.validate_url(f"https://public.example/{p}", dns_cache=cache)
    assert calls == ["public.example"]
    for _ in range(2):
        with pytest.raises(ssrf.UnsafeUrlError):
            ssrf.validate_url("https://internal.example/x", dns_cache=cache)

    def failing(host, *a, **k):
        raise socket.gaierror("temporary")
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", failing)
    monkeypatch.setattr(ssrf.time, "sleep", lambda s: None)
    with pytest.raises(ssrf.UnsafeUrlError):
        ssrf.validate_url("https://flaky.example/", dns_cache=cache)
    assert "flaky.example" not in cache        # failures are never cached
