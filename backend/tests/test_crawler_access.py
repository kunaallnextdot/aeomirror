"""AI Crawler Access Check — service (mocked HTTP via httpx.MockTransport) + the
scan-integration path. No real network is used."""
import asyncio

import httpx
import pytest

import app.api.routes_scan as rs
from app.config import settings
from app.services.crawler_access import (
    ALLOWED, BLOCKED_BY_ROBOTS, BLOCKED_BY_SERVER, CONTROL_UA, CRAWLERS, CRITICAL,
    ERROR, INFO, WARNING, check_crawler_access, has_critical_block,
)

URL = "https://site.test/page"
ALLOW_ALL = "User-agent: *\nAllow: /\n"


def _run(url, handler):
    return asyncio.run(check_crawler_access(url, transport=httpx.MockTransport(handler)))


def _by_key(result):
    return {b["key"]: b for b in result["bots"]}


def _is_control(req):
    return "Chrome/125" in req.headers.get("user-agent", "")


# ------------------------------- robots-layer scenarios -------------------------------
def test_all_allowed():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL)
        return httpx.Response(200, text="<html>ok</html>")

    r = _run(URL, handler)
    assert r["site_unreachable"] is False
    assert r["robots"]["status"] == "found"
    assert all(b["status"] == ALLOWED for b in r["bots"])
    assert r["findings"] == []
    assert has_critical_block(r) is False


def test_robots_disallows_one_bot():
    robots = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(200)

    r = _run(URL, handler)
    bots = _by_key(r)
    assert bots["gptbot"]["status"] == BLOCKED_BY_ROBOTS
    assert all(bots[k]["status"] == ALLOWED for k in bots if k != "gptbot")
    f = [f for f in r["findings"] if f["bot"] == "gptbot"][0]
    assert f["severity"] == CRITICAL and "robots.txt" in f["cause"].lower()
    assert "robots.txt" in f["remediation"].lower()   # robots fix, not WAF
    assert has_critical_block(r) is True


def test_missing_robots_txt_is_allowed():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200)

    r = _run(URL, handler)
    assert r["robots"]["status"] == "missing"
    assert all(b["status"] == ALLOWED for b in r["bots"])
    assert r["findings"] == []


def test_malformed_robots_txt_is_allowed_with_warning():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="just some prose, no directives here at all")
        return httpx.Response(200)

    r = _run(URL, handler)
    assert r["robots"]["status"] == "malformed"
    assert r["robots"]["warning"]
    assert all(b["status"] == ALLOWED for b in r["bots"])   # treated as allowed


# ------------------------------- server / WAF scenarios -------------------------------
def test_waf_blocks_bot_but_control_ok():
    """Control (browser) 200, GPTBot UA 403 => blocked_by_server, WAF remediation."""
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL)
        if _is_control(req):
            return httpx.Response(200)
        if "GPTBot" in req.headers.get("user-agent", ""):
            return httpx.Response(403)
        return httpx.Response(200)

    r = _run(URL, handler)
    bots = _by_key(r)
    assert bots["gptbot"]["status"] == BLOCKED_BY_SERVER
    assert bots["gptbot"]["status_code"] == 403
    f = [f for f in r["findings"] if f["bot"] == "gptbot"][0]
    assert f["severity"] == CRITICAL
    assert "waf" in f["remediation"].lower() or "bot-protection" in f["remediation"].lower()


def test_site_fully_unreachable_no_per_bot_blocks():
    """Every request fails (incl. control). Must emit ONE site_unreachable finding and
    NO per-bot blocked findings (they'd be false positives)."""
    def handler(req):
        raise httpx.ConnectError("down")

    r = _run(URL, handler)
    assert r["site_unreachable"] is True
    assert len(r["findings"]) == 1
    assert r["findings"][0]["status"] == "site_unreachable"
    assert r["findings"][0]["severity"] == INFO
    assert not any(b["status"] in (BLOCKED_BY_ROBOTS, BLOCKED_BY_SERVER) for b in r["bots"])
    assert has_critical_block(r) is False


def test_single_bot_timeout_is_error_not_block():
    """Control ok; one bot times out => status 'error' (INFO), not a block."""
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL)
        if "ClaudeBot" in req.headers.get("user-agent", ""):
            raise httpx.TimeoutException("slow")
        return httpx.Response(200)

    r = _run(URL, handler)
    bots = _by_key(r)
    assert bots["claudebot"]["status"] == ERROR
    assert bots["claudebot"]["error"] == "timeout"
    f = [f for f in r["findings"] if f["bot"] == "claudebot"][0]
    assert f["severity"] == INFO


def test_severity_critical_vs_noncritical():
    """A blocked critical bot (GPTBot) => CRITICAL; a blocked informational bot (CCBot)
    => WARNING."""
    robots = ("User-agent: GPTBot\nDisallow: /\n\n"
              "User-agent: CCBot\nDisallow: /\n\n"
              "User-agent: *\nAllow: /\n")

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(200)

    r = _run(URL, handler)
    sev = {f["bot"]: f["severity"] for f in r["findings"]}
    assert sev["gptbot"] == CRITICAL       # critical bot
    assert sev["ccbot"] == WARNING         # informational bot
    assert has_critical_block(r) is True


def test_crawler_set_shape():
    """The crawler set + critical flags match the spec (guards accidental edits)."""
    crit = {c["name"] for c in CRAWLERS if c["critical"]}
    noncrit = {c["name"] for c in CRAWLERS if not c["critical"]}
    assert {"GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"} <= crit
    assert noncrit == {"CCBot", "Bytespider"}


# ------------------------------- scan integration -------------------------------
def test_scan_stores_and_exposes_crawler_access(monkeypatch):
    """run_scan persists crawler_access and the scan API exposes it (feature enabled)."""
    from app.scanner.models import PageBundle
    from tests.authutil import auth_client
    from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS

    async def _fake_fetch(url, *, transport=None, retries=None):
        return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                          llms_txt_present=True, sitemap_present=True)

    canned = {"site_unreachable": False, "bots": [{"key": "gptbot", "status": BLOCKED_BY_ROBOTS}],
              "findings": [{"severity": CRITICAL, "bot": "gptbot", "status": BLOCKED_BY_ROBOTS,
                            "title": "GPTBot is blocked"}]}

    async def _fake_check(url, *, transport=None):
        return canned

    monkeypatch.setattr(settings, "crawler_access_enabled", True)
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    monkeypatch.setattr("app.services.crawler_access.check_crawler_access", _fake_check)

    client, _ = auth_client()
    sid = client.post("/v1/scan", json={"url": "https://ca-int.example/"}).json()["scan_id"]
    body = client.get(f"/api/scans/{sid}").json()
    assert body["crawler_access"] == canned
    assert body["crawler_access"]["findings"][0]["severity"] == CRITICAL


def test_scan_crawler_access_null_when_disabled(monkeypatch):
    """With the feature off (the default in tests), crawler_access is null and the API
    handles it gracefully."""
    from app.scanner.models import PageBundle
    from tests.authutil import auth_client
    from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS

    async def _fake_fetch(url, *, transport=None, retries=None):
        return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                          llms_txt_present=True, sitemap_present=True)

    monkeypatch.setattr(rs, "fetch", _fake_fetch)   # crawler_access_enabled stays False (conftest)
    client, _ = auth_client()
    sid = client.post("/v1/scan", json={"url": "https://ca-off.example/"}).json()["scan_id"]
    assert client.get(f"/api/scans/{sid}").json()["crawler_access"] is None
