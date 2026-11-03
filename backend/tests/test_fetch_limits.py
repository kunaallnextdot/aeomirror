"""P0-6 regression: an anonymous scan can't exhaust memory or hang on robots.txt,
llms.txt, sitemap.xml or the AI-crawler checks. Bodies are capped while STREAMING
(no Content-Length needed), decompressed size is capped (gzip/deflate bombs), every
request has a total deadline, oversized/slow/odd optional resources just leave the
scan without them, and robots.txt redirects are SSRF-checked on every hop. Bodies are
streamed from generators through httpx.MockTransport — nothing large is ever built in
memory by the test itself, and there is no network."""
import asyncio
import time
import tracemalloc
import zlib

import httpx
import pytest

from app.config import settings
from app.core import fetch as f
from app.core.fetch import FetchError, fetch
from app.services.crawler_access import CONTROL_UA, check_crawler_access

URL = "https://site.test/"
PAGE = b"<html><head><title>Hello</title></head><body><h1>Hi</h1><p>" + b"word " * 50 + b"</p></body></html>"
CHUNK = 64 * 1024


class Stream(httpx.AsyncByteStream):
    """An endless (or `total`-byte) body served in network-sized chunks, counting how
    much the client actually pulled. No Content-Length header is sent."""

    def __init__(self, total=None, chunk=b"x" * CHUNK, delay=0.0, chunks=None):
        self.total, self.chunk, self.delay, self.chunks = total, chunk, delay, chunks
        self.sent = 0

    async def __aiter__(self):
        if self.chunks is not None:
            for c in self.chunks:
                self.sent += len(c)
                yield c
            return
        while self.total is None or self.sent < self.total:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.sent += len(self.chunk)
            yield self.chunk


def _gzip_bomb(decompressed_mb: int, kind="gzip") -> list[bytes]:
    """`decompressed_mb` MB of zeros, compressed (≈1000:1), produced incrementally."""
    wbits = {"gzip": 16 + zlib.MAX_WBITS, "deflate": zlib.MAX_WBITS, "raw": -zlib.MAX_WBITS}[kind]
    c = zlib.compressobj(9, zlib.DEFLATED, wbits)
    zeros = b"\0" * (1024 * 1024)
    out = [c.compress(zeros) for _ in range(decompressed_mb)]
    out.append(c.flush())
    data = b"".join(out)
    return [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]


def _site(routes):
    """routes: {path: Response or callable(request) -> Response}; page served at '/'."""
    seen = []

    def handler(req):
        seen.append(str(req.url))
        r = routes.get(req.url.path)
        if callable(r):
            return r(req)
        return r if r is not None else httpx.Response(404)
    return httpx.MockTransport(handler), seen


def _fetch(transport, **kw):
    return asyncio.run(fetch(URL, transport=transport, **kw))


def test_normal_responses_are_unchanged():
    transport, _ = _site({"/": httpx.Response(200, content=PAGE),
                          "/robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /\nSitemap: /s.xml"),
                          "/llms.txt": httpx.Response(200, text="# llms"),
                          "/sitemap.xml": httpx.Response(200, text="<urlset><url><loc>https://site.test/</loc></url></urlset>")})
    b = _fetch(transport)
    assert "<title>Hello</title>" in b.html and b.status_code == 200
    assert b.robots_txt.startswith("User-agent") and b.llms_txt_present and b.sitemap_present
    assert "<urlset>" in b.sitemap_xml


@pytest.mark.parametrize("path", ["/sitemap.xml", "/robots.txt"])
def test_an_endless_aux_file_without_content_length_is_cut_off_while_streaming(path):
    body = Stream(total=300 * 1024 * 1024)                     # 300 MB if read whole
    transport, _ = _site({"/": httpx.Response(200, content=PAGE), path: httpx.Response(200, stream=body)})
    tracemalloc.start()
    b = _fetch(transport)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    cap = settings.fetch_sitemap_max_bytes if path == "/sitemap.xml" else settings.fetch_robots_max_bytes
    kept = b.sitemap_xml if path == "/sitemap.xml" else b.robots_txt
    assert len(kept) <= cap
    assert body.sent <= cap + 2 * CHUNK                         # stopped pulling at the cap
    assert peak < 20 * 1024 * 1024                              # was ~613 MB before the fix
    assert "<title>Hello</title>" in b.html                     # the scan itself still works


@pytest.mark.parametrize("kind", ["gzip", "deflate", "raw"])
def test_a_compression_bomb_is_capped_by_decompressed_size(kind):
    bomb = Stream(chunks=_gzip_bomb(200, kind))                 # ~200 KB on the wire, 200 MB inflated
    enc = "gzip" if kind == "gzip" else "deflate"
    transport, _ = _site({"/": httpx.Response(200, content=PAGE),
                          "/sitemap.xml": httpx.Response(200, headers={"Content-Encoding": enc}, stream=bomb)})
    tracemalloc.start()
    b = _fetch(transport)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(b.sitemap_xml) <= settings.fetch_sitemap_max_bytes
    assert peak < 20 * 1024 * 1024


def test_a_compressed_page_is_decoded_and_capped():
    bomb = Stream(chunks=_gzip_bomb(100))
    transport, _ = _site({"/": httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=bomb)})
    tracemalloc.start()
    b = _fetch(transport)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(b.html) <= settings.fetch_max_bytes and peak < 40 * 1024 * 1024


@pytest.mark.parametrize("kind", ["gzip", "deflate", "raw"])
def test_normal_compressed_bodies_still_decode(kind):
    wbits = {"gzip": 16 + zlib.MAX_WBITS, "deflate": zlib.MAX_WBITS, "raw": -zlib.MAX_WBITS}[kind]
    c = zlib.compressobj(6, zlib.DEFLATED, wbits)
    data = c.compress(PAGE) + c.flush()
    enc = "gzip" if kind == "gzip" else "deflate"
    transport, _ = _site({"/": httpx.Response(200, headers={"Content-Encoding": enc}, stream=Stream(chunks=[data]))})
    assert _fetch(transport).html == PAGE.decode()


def test_an_encoding_that_cant_be_inflated_safely_is_skipped_not_crashed():
    transport, _ = _site({"/": httpx.Response(200, content=PAGE),
                          "/robots.txt": httpx.Response(200, headers={"Content-Encoding": "br"},
                                                        stream=Stream(total=10 * 1024 * 1024))})
    b = _fetch(transport)
    assert b.robots_txt == "" and "<title>Hello</title>" in b.html


def test_only_safely_decodable_encodings_are_requested():
    got = {}

    def page(req):
        got["ae"] = req.headers.get("accept-encoding")
        return httpx.Response(200, content=PAGE)
    transport, _ = _site({"/": page})
    _fetch(transport)
    assert got["ae"] == "gzip, deflate"


def test_the_llms_probe_never_downloads_the_body():
    body = Stream(total=500 * 1024 * 1024)
    transport, _ = _site({"/": httpx.Response(200, content=PAGE), "/llms.txt": httpx.Response(200, stream=body)})
    b = _fetch(transport)
    assert b.llms_txt_present is True and body.sent <= CHUNK


def test_a_slow_drip_aux_file_hits_the_total_deadline(monkeypatch):
    monkeypatch.setattr(settings, "fetch_aux_timeout_seconds", 0.3)
    drip = Stream(chunk=b"x", delay=0.02)                       # never ends, never idles long enough to time out per read
    transport, _ = _site({"/": httpx.Response(200, content=PAGE), "/sitemap.xml": httpx.Response(200, stream=drip)})
    t = time.perf_counter()
    b = _fetch(transport)
    assert time.perf_counter() - t < 3 and b.sitemap_xml == ""


def test_a_slow_drip_page_fails_cleanly_after_the_deadline(monkeypatch):
    monkeypatch.setattr(settings, "fetch_total_timeout_seconds", 0.3)
    transport, _ = _site({"/": httpx.Response(200, stream=Stream(chunk=b"<", delay=0.02))})
    t = time.perf_counter()
    with pytest.raises(FetchError):
        _fetch(transport, retries=0)
    assert time.perf_counter() - t < 3


def test_read_limited_caps_raw_and_decoded_bytes():
    async def run(resp, limit):
        return await f.read_limited(resp, limit)
    body, cut = asyncio.run(run(httpx.Response(200, stream=Stream(total=10 * CHUNK)), CHUNK))
    assert cut and len(body) == CHUNK
    body, cut = asyncio.run(run(httpx.Response(200, stream=Stream(chunks=[b"abc"])), 10))
    assert (body, cut) == (b"abc", False)


# ------------------------------- AI-crawler check -------------------------------

def _crawler(handler):
    return asyncio.run(check_crawler_access("https://site.test/page", transport=httpx.MockTransport(handler)))


def test_crawler_checks_read_status_only_and_cap_robots():
    streams = []

    def handler(req):
        s = Stream(total=200 * 1024 * 1024)
        streams.append((req.url.path, s))
        return httpx.Response(200, stream=s)
    tracemalloc.start()
    r = _crawler(handler)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert r["site_unreachable"] is False
    page_pulls = [s.sent for p, s in streams if p == "/page"]
    assert page_pulls and max(page_pulls) <= CHUNK              # bot probes never download the page
    robots = [s.sent for p, s in streams if p == "/robots.txt"]
    assert robots and robots[0] <= settings.fetch_robots_max_bytes + 2 * CHUNK
    assert peak < 20 * 1024 * 1024


@pytest.fixture
def real_ssrf_checks(monkeypatch):
    """Test mode skips the IP-range checks (allow_private_hosts); turn them back on with
    a resolver stub: site.test is public, IP literals resolve to themselves."""
    from app.core import ssrf
    monkeypatch.setattr(settings, "allow_private_hosts", False)
    monkeypatch.setattr(ssrf, "_resolve", lambda host, cache=None: [
        (None, None, None, None, ("93.184.216.34" if host == "site.test" else host, 0))])


def test_a_robots_redirect_to_an_internal_address_is_never_requested(real_ssrf_checks):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.path == "/robots.txt" and req.url.host == "site.test":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="ok")
    r = _crawler(handler)
    assert any(u.endswith("/robots.txt") for u in seen)          # the first hop was requested…
    assert not any("169.254.169.254" in u for u in seen)          # …the internal redirect never was
    assert r["robots"]["status"] == "missing" and r["site_unreachable"] is False


def test_a_robots_redirect_to_a_public_page_is_followed_and_parsed():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(301, headers={"Location": "https://site.test/robots-v2.txt"})
        if req.url.path == "/robots-v2.txt":
            return httpx.Response(200, text="User-agent: GPTBot\nDisallow: /\n")
        return httpx.Response(200, text="ok")
    r = _crawler(handler)
    assert r["robots"]["status"] == "found"
    assert {b["key"]: b["status"] for b in r["bots"]}["gptbot"] == "blocked_by_robots"


def test_a_slow_crawler_probe_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "crawler_access_timeout_seconds", 0.2)

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, stream=Stream(chunk=b"#", delay=0.02))
        return httpx.Response(200, text="ok")
    t = time.perf_counter()
    r = _crawler(handler)
    assert time.perf_counter() - t < 5 and r["robots"]["status"] == "missing"
