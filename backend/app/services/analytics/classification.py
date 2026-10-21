"""Deterministic traffic-channel classification for first-party website analytics —
the GA4-INDEPENDENT counterpart to app.services.integrations.classification (which
classifies an already-computed GA4 (source, medium) pair). This module instead works
from a raw browser `referrer` URL and the landing page's own `utm_source`/`utm_medium`
query parameters, which is what a first-party tracker actually has available.

Reuses the SAME known-AI-assistant-hostname table and exact-or-subdomain host matching
as the GA4 classifier (`app.services.integrations.classification`) — one centrally
maintained list, not a second divergent one. Version this file (RULESET_VERSION) when
the host/medium lists change, so a stored `channel` value can be understood against
the rules that produced it.

Policy (deliberately conservative, mirrors the GA4 classifier's spirit):
- An explicit `utm_medium` tag (paid/email/social) is the site owner's own deliberate
  attribution and wins over an inferred guess from the referrer.
- A known AI-assistant hostname — via utm_source OR the real browser referrer — maps
  to that assistant. Lookalike domains never match (suffix match only, never substring).
- No referrer AND no UTM at all is "direct" — never guessed as anything else.
- A referrer that is the SAME host as the page itself (internal navigation leaking into
  what should be first-touch data) is treated as if there were no referrer at all.
- Viewing an AEO-related page, or any other on-site signal, NEVER on its own implies an
  AI source — only real referrer/UTM evidence does.
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.services.integrations.classification import (
    _KNOWN_AI_HOSTS, _host_matches, _normalize_host,
)

RULESET_VERSION = "2026-10-19.1"

_PAID_MEDIUMS = {"cpc", "ppc", "paid", "paidsearch", "paid-search", "paid_search",
                 "display", "cpm", "paidsocial", "paid-social", "paid_social"}
_EMAIL_MEDIUMS = {"email", "e-mail", "newsletter"}
_SOCIAL_MEDIUMS = {"social", "social-media", "social_media", "social-network"}

_SEARCH_HOSTS = ("google.com", "google", "bing.com", "yahoo.com", "duckduckgo.com",
                  "baidu.com", "yandex.com", "ecosia.org", "ask.com")
_SOCIAL_HOSTS = ("facebook.com", "instagram.com", "twitter.com", "x.com", "t.co",
                  "linkedin.com", "lnkd.in", "pinterest.com", "reddit.com", "tiktok.com",
                  "youtube.com", "threads.net")


def _referrer_host(referrer: str | None) -> str:
    """Hostname from a full referrer URL (never a bare "source" string — a browser
    Referer header/document.referrer is always a URL, or empty)."""
    if not referrer or not isinstance(referrer, str):
        return ""
    try:
        host = urlparse(referrer).hostname or ""
    except ValueError:
        return ""
    return _normalize_host(host)


def _matches_any(host: str, known_hosts: tuple[str, ...]) -> bool:
    return any(_host_matches(host, h) for h in known_hosts)


def classify_channel(*, referrer: str | None, utm_source: str | None = None,
                     utm_medium: str | None = None, landing_host: str | None = None) -> tuple[str, str | None]:
    """Returns (channel, referrer_host). `channel` is one of
    app.db.models.ANALYTICS_CHANNELS. Never raises; malformed/missing input always
    resolves to "unknown" or "direct", never a specific source guessed without
    evidence."""
    ref_host = _referrer_host(referrer)
    # A same-host "referrer" is internal navigation, not real external evidence —
    # treat it exactly like no referrer at all (this function is only ever called
    # once, at session creation, so this guards against a session that happens to
    # start mid-site, e.g. a restored browser tab).
    if ref_host and landing_host and ref_host == _normalize_host(landing_host):
        ref_host = ""

    medium_norm = _normalize_host(utm_medium) if isinstance(utm_medium, str) else ""
    source_host = _normalize_host(utm_source) if isinstance(utm_source, str) else ""

    # 1. An explicit, deliberate UTM medium tag wins over any inference.
    if medium_norm in _PAID_MEDIUMS:
        return "paid", ref_host or None
    if medium_norm in _EMAIL_MEDIUMS:
        return "email", ref_host or None
    if medium_norm in _SOCIAL_MEDIUMS:
        return "social", ref_host or None

    # 2. A known AI-assistant host — via utm_source or the real referrer.
    for candidate in (source_host, ref_host):
        if not candidate:
            continue
        for known_domain, label in _KNOWN_AI_HOSTS.items():
            if _host_matches(candidate, known_domain):
                return label, ref_host or None

    # 3. No referrer and no UTM source at all -> direct, never guessed further.
    if not ref_host and not source_host:
        return "direct", None

    host_to_check = source_host or ref_host

    # 4. A known search engine (and no UTM override already matched above) -> organic.
    if _matches_any(host_to_check, _SEARCH_HOSTS):
        return "organic_search", ref_host or None

    # 5. A known social platform.
    if _matches_any(host_to_check, _SOCIAL_HOSTS):
        return "social", ref_host or None

    # 6. Some other real, identifiable host we don't specifically recognize.
    if host_to_check:
        return "referral", ref_host or None

    return "unknown", ref_host or None
