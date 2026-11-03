"""Fetch layer. Pulls everything the scanner needs about one URL, once.

Hardened (Phase 1):
- Split connect vs. total timeouts.
- Every response body capped WHILE streaming — the page, robots.txt, llms.txt and
  sitemap.xml alike — counting DECOMPRESSED bytes and inflating gzip/deflate in bounded
  steps, so neither a huge file nor a compression bomb can exhaust memory.
- A total wall-clock deadline per request (httpx's own timeouts apply per read).
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
import zlib
from urllib.parse import urljoin, urlparse

import httpx

from app.config import settings
from app.core.ssrf import UnsafeUrlError, validate_url
from app.scanner.models import PageBundle

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_RETRY_STATUS = {502, 503, 504}
# Only encodings read_limited can inflate in bounded steps are advertised.
ACCEPT_ENCODING = "gzip, deflate"


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


async def read_limited(resp: httpx.Response, limit: int) -> tuple[bytes | None, bool]:
    """Stream at most `limit` DECODED bytes of a response body. Returns (body, truncated).
    Reads raw bytes and inflates gzip/deflate itself with a max_length per step (httpx's
    decoder would inflate a whole network chunk at once — up to ~1000x its size). The
    raw stream is capped at `limit` too. body=None when the body uses an encoding that
    can't be inflated safely (we never advertise one; a server sending it anyway is
    treated as "no usable body")."""
    if resp.is_stream_consumed:    # body already in memory (a response built from bytes): just cap it
        data = resp.content
        return data[:limit], len(data) > limit
    encodings = [e.strip().lower() for e in (resp.headers.get("content-encoding") or "").split(",") if e.strip()]
    encodings = [e for e in encodings if e != "identity"]
    if len(encodings) > 1 or (encodings and encodings[0] not in ("gzip", "x-gzip", "deflate")):
        return None, True
    if encodings and encodings[0] in ("gzip", "x-gzip"):
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encodings:
        decoder = zlib.decompressobj(zlib.MAX_WBITS)     # zlib-wrapped; raw deflate handled below
    else:
        decoder = None
    out = bytearray()
    raw_total, first = 0, True
    async for raw in resp.aiter_raw():
        raw_total += len(raw)
        if raw_total > limit:
            return bytes(out[:limit]), True
        if decoder is None:
            out += raw[: limit + 1 - len(out)]
        else:
            data = raw
            while data:
                try:
                    piece = decoder.decompress(data, limit + 1 - len(out))
                except zlib.error:
                    if first and encodings[0] == "deflate":   # some servers send raw deflate
                        decoder, first = zlib.decompressobj(-zlib.MAX_WBITS), False
                        continue
                    return bytes(out), True                   # corrupt stream: keep what decoded
                first = False
                out += piece
                if len(out) > limit:
                    break
                data = decoder.unconsumed_tail
        if len(out) > limit:
            return bytes(out[:limit]), True
    if decoder is not None:
        try:
            out += decoder.flush()[: limit + 1 - len(out)]
        except zlib.error:
            return bytes(out), True
        if len(out) > limit:
            return bytes(out[:limit]), True
    return bytes(out), False


async def with_deadline(coro, seconds: float):
    """Await `coro` under a total wall-clock deadline; a miss is a ReadTimeout (so the
    page fetch's transient-retry logic treats it like any other timeout)."""
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        raise httpx.ReadTimeout(f"no complete response within {seconds:g}s")


def _decode(resp: httpx.Response, body: bytes | None) -> str:
    return (body or b"").decode(resp.encoding or "utf-8", errors="replace")


async def _read_capped(resp: httpx.Response) -> tuple[int, str, dict]:
    body, _ = await read_limited(resp, settings.fetch_max_bytes)
    return resp.status_code, _decode(resp, body), dict(resp.headers)


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
    """Status only: the body is never read (no redirect following for aux probes)."""
    async def probe():
        async with client.stream("GET", url) as r:
            return r.status_code == 200
    try:
        return await with_deadline(probe(), settings.fetch_aux_timeout_seconds)
    except httpx.HTTPError:
        return False


async def _get_text(client: httpx.AsyncClient, url: str, limit: int) -> str | None:
    """Body of a 200 response, capped at `limit` decoded bytes; None otherwise. Never
    raises: an optional resource that is oversized, slow, compressed oddly or missing
    just leaves the scan without it."""
    async def get():
        async with client.stream("GET", url) as r:
            if r.status_code != 200:
                return None
            body, _ = await read_limited(r, limit)
            return None if body is None else _decode(r, body)
    try:
        return await with_deadline(get(), settings.fetch_aux_timeout_seconds)
    except httpx.HTTPError:
        return None


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
    headers = {"User-Agent": settings.user_agent, "Accept-Encoding": ACCEPT_ENCODING}

    async with httpx.AsyncClient(
        headers=headers, timeout=_timeout(), limits=limits,
        follow_redirects=False,  # we follow manually so each hop is revalidated
        transport=transport,
    ) as client:
        status, html, resp_headers, redirect_chain, final_url = await _with_retry(
            lambda: with_deadline(_fetch_page(client, url), settings.fetch_total_timeout_seconds), retries)

        robots = await _get_text(client, f"{origin}/robots.txt", settings.fetch_robots_max_bytes) or ""

        llms_present = await _exists(client, f"{origin}/llms.txt")

        # Capture the sitemap body (capped) so the sitemap signal can validate it.
        sitemap_xml = await _get_text(client, f"{origin}/sitemap.xml", settings.fetch_sitemap_max_bytes) or ""
        sitemap_present = bool(sitemap_xml) or ("sitemap" in robots.lower())

    return PageBundle(
        url=url, html=html, robots_txt=robots,
        llms_txt_present=llms_present, sitemap_present=sitemap_present,
        sitemap_xml=sitemap_xml,
        status_code=status, headers=resp_headers,
        redirect_chain=redirect_chain, final_url=final_url,
    )
