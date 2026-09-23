"""Fetch layer. Pulls everything the scanner needs about one URL, once.

Hardened (Phase 1):
- Split connect vs. total timeouts.
- Response body capped WHILE streaming (never buffers an unbounded body).
- Redirects followed manually and **every hop is SSRF-revalidated** — a redirect
  to a private/loopback/reserved address is rejected, not just the initial URL.
- Bounded exponential backoff + jitter, retrying ONLY transient failures
  (connection reset, timeout, 502/503/504). Validation/SSRF/4xx are never retried.
- http/https only (enforced by ssrf.validate_url).

Builds a PageBundle the scanner's pure-function checks read from.
"""
from __future__ import annotations

import asyncio
import random
from urllib.parse import urljoin, urlparse

import httpx

from app.config import settings
from app.core.ssrf import UnsafeUrlError, validate_url
from app.scanner.models import PageBundle

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_RETRY_STATUS = {502, 503, 504}


class FetchError(Exception):
    """Non-SSRF fetch failure (timeout, too many redirects, transport error)."""


class _Transient(Exception):
    """Internal marker for a retryable condition."""


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _timeout() -> httpx.Timeout:
    # Separate connect budget; the rest share the total request budget.
    return httpx.Timeout(
        settings.fetch_timeout_seconds,
        connect=settings.fetch_connect_timeout_seconds,
    )


async def _sleep_backoff(attempt: int) -> None:
    base = settings.fetch_retry_backoff_base * (2 ** attempt)
    capped = min(settings.fetch_retry_backoff_max, base)
    await asyncio.sleep(capped * (0.5 + random.random() * 0.5))  # full-ish jitter


async def _with_retry(factory, retries: int | None = None):
    """Run an async operation with bounded retries on transient failures only.

    `retries` overrides the global `fetch_retry_count` for this call only (used by the
    interactive content-insight re-fetch, which doesn't need the scanner's full budget);
    None keeps the global default."""
    attempts = (settings.fetch_retry_count if retries is None else retries) + 1
    for i in range(attempts):
        try:
            return await factory()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.RemoteProtocolError, _Transient):
            if i == attempts - 1:
                raise FetchError("Upstream request failed after retries.")
            await _sleep_backoff(i)


async def _read_capped(resp: httpx.Response) -> tuple[int, str, dict]:
    chunks, total = [], 0
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > settings.fetch_max_bytes:
            break  # stop streaming; do not buffer more than the cap
        chunks.append(chunk)
    body = b"".join(chunks)
    text = body.decode(resp.encoding or "utf-8", errors="replace")
    return resp.status_code, text, dict(resp.headers)


async def _fetch_page(client: httpx.AsyncClient, start_url: str) -> tuple[int, str, dict, list, str]:
    """Follow redirects manually, revalidating each hop for SSRF, and stream the
    final body under the size cap. Raises UnsafeUrlError on a disallowed hop.

    Every hop was already being fetched to follow the redirect; this just RECORDS what
    was already seen (no extra requests) so Technical SEO indexability intelligence can
    tell a caller a URL redirected, and to where, instead of only ever seeing the
    already-resolved final response. Returns (status, html, headers, redirect_chain,
    final_url) — `redirect_chain` is `[]` and `final_url == start_url` when there was no
    redirect."""
    current = start_url
    chain: list[dict] = []
    for _ in range(settings.fetch_max_redirects + 1):
        async with client.stream("GET", current) as resp:
            if resp.status_code in _REDIRECT_CODES:
                loc = resp.headers.get("location")
                if loc:
                    target = urljoin(current, loc)
                    # SSRF re-check on the redirect target. check_chars=False: a sloppy
                    # Location (raw space/pipe) must not kill an otherwise-safe scan; the
                    # IP-range checks still run. httpx encodes the path when it requests.
                    validate_url(target, check_chars=False)
                    chain.append({"url": current, "status_code": resp.status_code, "to": target})
                    current = target
                    continue
            if resp.status_code in _RETRY_STATUS:
                raise _Transient()
            status, html, headers = await _read_capped(resp)
            return status, html, headers, chain, current
    raise FetchError("Too many redirects.")


async def _exists(client: httpx.AsyncClient, url: str) -> bool:
    try:
        r = await client.get(url)  # no redirect following for aux probes
        return r.status_code == 200
    except httpx.HTTPError:
        return False


async def fetch(url: str, *, transport: "httpx.BaseTransport | None" = None,
                retries: int | None = None) -> PageBundle:
    """Live fetch. The URL must already be SSRF-validated by the caller; redirect
    targets are validated here. Raises UnsafeUrlError (SSRF) or FetchError.

    `transport` is a test-only seam for injecting an httpx.MockTransport. `retries`
    overrides the global `fetch_retry_count` for this call only (None → global)."""
    # defense-in-depth: reject non-http(s) even if a caller skipped validation
    if urlparse(url).scheme not in ("http", "https"):
        raise UnsafeUrlError("Only http and https URLs are allowed.")

    origin = _origin(url)
    limits = httpx.Limits(max_connections=10)
    headers = {"User-Agent": settings.user_agent}

    async with httpx.AsyncClient(
        headers=headers, timeout=_timeout(), limits=limits,
        follow_redirects=False,  # we follow manually so each hop is revalidated
        transport=transport,
    ) as client:
        status, html, resp_headers, redirect_chain, final_url = await _with_retry(
            lambda: _fetch_page(client, url), retries)

        robots = ""
        try:
            r = await client.get(f"{origin}/robots.txt")
            if r.status_code == 200:
                robots = r.text
        except httpx.HTTPError:
            pass

        llms_present = await _exists(client, f"{origin}/llms.txt")

        # Capture the sitemap body (capped) so the sitemap signal can validate it.
        sitemap_xml = ""
        try:
            sm = await client.get(f"{origin}/sitemap.xml")
            if sm.status_code == 200:
                sitemap_xml = sm.text[:200_000]
        except httpx.HTTPError:
            pass
        sitemap_present = bool(sitemap_xml) or ("sitemap" in robots.lower())

    return PageBundle(
        url=url, html=html, robots_txt=robots,
        llms_txt_present=llms_present, sitemap_present=sitemap_present,
        sitemap_xml=sitemap_xml,
        status_code=status, headers=resp_headers,
        redirect_chain=redirect_chain, final_url=final_url,
    )
