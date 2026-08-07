"""SSRF protection for the public scan endpoint.

A public "paste any URL" scanner must never be usable to probe internal
infrastructure. This rejects non-HTTP(S) schemes and any URL whose host
resolves to a private, loopback, link-local, or reserved IP range.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
from urllib.parse import urlparse

from app.config import settings

# Characters RFC 3986 forbids in a URL unencoded: ASCII controls, space, and the
# delimiters < > " { } | \ ^ ` . A legitimate URL percent-encodes these (e.g. '<' →
# %3C); a raw one is malformed or mis-copied (a stray trailing '<' from HTML/text).
# We REJECT rather than strip, so a URL is never mutated near its host in a way that
# could loosen the SSRF checks below.
_FORBIDDEN_URL_CHARS = re.compile(r'[\x00-\x20\x7f<>"{}|\\^`]')


def has_forbidden_url_chars(url: str) -> bool:
    """True if the string contains a raw RFC-3986-forbidden character (see above)."""
    return bool(_FORBIDDEN_URL_CHARS.search(url or ""))

# A transient DNS blip (getaddrinfo raising gaierror for a moment) must not reject a
# perfectly resolvable public site. Retry a couple of times before giving up. This is
# especially important for site scans, which resolve the root more than once.
_DNS_ATTEMPTS = 3
_DNS_BACKOFF_SECONDS = 0.15


class UnsafeUrlError(ValueError):
    """Raised when a URL is blocked by SSRF policy."""


def _resolve(hostname: str) -> list:
    """Resolve a hostname to addrinfo records, retrying transient failures.

    Raises UnsafeUrlError only if every attempt fails, so a momentary resolver
    hiccup no longer surfaces as "Host could not be resolved" for a live domain.
    """
    last_err: socket.gaierror | None = None
    for attempt in range(_DNS_ATTEMPTS):
        try:
            return socket.getaddrinfo(hostname, None)
        except socket.gaierror as e:
            last_err = e
            if attempt < _DNS_ATTEMPTS - 1:
                time.sleep(_DNS_BACKOFF_SECONDS)
    raise UnsafeUrlError("Host could not be resolved.") from last_err


def validate_url(raw: str, *, check_chars: bool = True) -> str:
    """Return a normalized safe URL or raise UnsafeUrlError.

    `check_chars` gates the raw-forbidden-character rejection: it is input hygiene for
    mis-pasted USER input, so it defaults on at the entry points. Redirect revalidation
    (_fetch_page) passes check_chars=False — a server-issued Location header is not user
    input, and httpx percent-encodes an unescaped path when it builds the request. The
    SSRF IP-range checks below always run, on every hop, regardless of this flag."""
    if not raw or not raw.strip():
        raise UnsafeUrlError("Empty URL.")

    url = raw.strip()
    if check_chars and has_forbidden_url_chars(url):
        raise UnsafeUrlError("URL contains invalid characters.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError("Only http and https URLs are allowed.")
    if not parsed.hostname:
        raise UnsafeUrlError("URL has no host.")

    if settings.allow_private_hosts:
        return url  # test mode only

    # Resolve every address the host maps to and reject unsafe ones. Resolution
    # retries transient failures; a genuinely unknown host still raises after that.
    infos = _resolve(parsed.hostname)

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise UnsafeUrlError("URL resolves to a disallowed address range.")

    return url
