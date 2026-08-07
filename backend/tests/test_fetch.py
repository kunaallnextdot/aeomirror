"""Fetch-hardening tests. Drive the real fetch() logic through an injected
httpx.MockTransport (test-only seam) and assert:
  - SSRF: localhost/private/link-local rejected; redirect to a private IP blocked
  - only http/https allowed
  - response body capped
  - excessive redirects rejected
  - transient failures (502) are retried; validation/SSRF errors are NOT retried
No real network is used.
"""
import asyncio

import httpx
import pytest

from app.config import settings
from app.core.fetch import FetchError, fetch
from app.core.ssrf import UnsafeUrlError, validate_url

AUX = {"/robots.txt", "/llms.txt", "/sitemap.xml"}


def run(coro):
    return asyncio.run(coro)


# ---------------- SSRF (validate_url) ----------------
@pytest.mark.parametrize("target", [
    "http://localhost/", "http://127.0.0.1/", "http://10.0.0.1/",
    "http://169.254.169.254/", "http://192.168.1.1/",
])
def test_validate_url_rejects_internal(monkeypatch, target):
    monkeypatch.setattr(settings, "allow_private_hosts", False)
    with pytest.raises(UnsafeUrlError):
        validate_url(target)


def test_validate_url_rejects_raw_forbidden_chars(monkeypatch):
    """A raw RFC-3986-forbidden char (e.g. a stray trailing '<') is rejected, not
    stripped — the check runs before host resolution, so it holds even in allow-private
    test mode. Percent-encoded equivalents (%3C) stay valid and must still pass."""
    monkeypatch.setattr(settings, "allow_private_hosts", True)
    with pytest.raises(UnsafeUrlError):
        validate_url("https://www.thedocmirror.com/resources/ai-visibility-for-doctors<")
    assert validate_url("https://example.com/a%3Cb") == "https://example.com/a%3Cb"


def test_fetch_rejects_non_http_scheme():
    with pytest.raises(UnsafeUrlError):
        run(fetch("ftp://example.com/resource"))


# ---------------- redirect SSRF re-validation ----------------
def test_redirect_to_private_ip_is_blocked_and_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "allow_private_hosts", False)
    monkeypatch.setattr(settings, "fetch_retry_count", 2)
    calls = {"main": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in AUX:
            return httpx.Response(404)
        calls["main"] += 1
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    with pytest.raises(UnsafeUrlError):
        run(fetch("http://redirector.example/", transport=httpx.MockTransport(handler)))
    assert calls["main"] == 1  # SSRF rejection is not retried


def test_redirect_with_raw_space_in_location_is_followed(monkeypatch):
    """A sloppy Location header with a raw space no longer kills the scan: the redirect
    is followed (httpx encodes the path) and the final page is returned. The char check
    is entry-point-only; SSRF still runs on the hop."""
    monkeypatch.setattr(settings, "allow_private_hosts", True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in AUX:
            return httpx.Response(404)
        if request.url.host == "start.example":
            return httpx.Response(302, headers={"location": "http://dest.example/a b?q=1 2"})
        return httpx.Response(200, content=b"<html>ok</html>")

    bundle = run(fetch("http://start.example/", transport=httpx.MockTransport(handler)))
    assert bundle.status_code == 200 and "ok" in bundle.html


# ---------------- body cap ----------------
def test_oversized_body_is_capped(monkeypatch):
    monkeypatch.setattr(settings, "allow_private_hosts", True)
    monkeypatch.setattr(settings, "fetch_max_bytes", 100)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in AUX:
            return httpx.Response(404)
        return httpx.Response(200, content=b"x" * 5000)

    bundle = run(fetch("http://big.example/", transport=httpx.MockTransport(handler)))
    assert len(bundle.html.encode("utf-8", "ignore")) <= settings.fetch_max_bytes


# ---------------- redirect cap ----------------
def test_excessive_redirects_raise(monkeypatch):
    monkeypatch.setattr(settings, "allow_private_hosts", True)
    monkeypatch.setattr(settings, "fetch_max_redirects", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        # always redirect -> exceeds the cap
        return httpx.Response(302, headers={"location": "http://loop.example/next"})

    with pytest.raises(FetchError):
        run(fetch("http://loop.example/", transport=httpx.MockTransport(handler)))


# ---------------- timeout ----------------
def test_timeout_raises_fetcherror(monkeypatch):
    monkeypatch.setattr(settings, "allow_private_hosts", True)
    monkeypatch.setattr(settings, "fetch_retry_count", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(FetchError):
        run(fetch("http://slow.example/", transport=httpx.MockTransport(handler)))


# ---------------- transient retry ----------------
def test_transient_502_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "allow_private_hosts", True)
    monkeypatch.setattr(settings, "fetch_retry_count", 2)
    monkeypatch.setattr(settings, "fetch_retry_backoff_base", 0.0)  # keep test fast
    state = {"main": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in AUX:
            return httpx.Response(404)
        state["main"] += 1
        if state["main"] == 1:
            return httpx.Response(502)          # transient
        return httpx.Response(200, content=b"<html><title>ok</title></html>")

    bundle = run(fetch("http://flaky.example/", transport=httpx.MockTransport(handler)))
    assert state["main"] == 2                    # retried exactly once
    assert "ok" in bundle.html
