"""SSRF protection for the public scan endpoint.

A public "paste any URL" scanner must never be usable to probe internal
infrastructure. This rejects non-HTTP(S) schemes and any URL whose host
resolves to a private, loopback, link-local, or reserved IP range.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.config import settings


class UnsafeUrlError(ValueError):
    """Raised when a URL is blocked by SSRF policy."""


def validate_url(raw: str) -> str:
    """Return a normalized safe URL or raise UnsafeUrlError."""
    if not raw or not raw.strip():
        raise UnsafeUrlError("Empty URL.")

    url = raw.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError("Only http and https URLs are allowed.")
    if not parsed.hostname:
        raise UnsafeUrlError("URL has no host.")

    if settings.allow_private_hosts:
        return url  # test mode only

    # Resolve every address the host maps to and reject unsafe ones.
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise UnsafeUrlError("Host could not be resolved.")

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise UnsafeUrlError("URL resolves to a disallowed address range.")

    return url
