"""Deterministic AI-referral-source classification for one GA4 (source, medium) pair.

Pure function, no I/O, no AI call — a controlled list of known AI-assistant referral
hostnames, exact-or-subdomain matched (never substring matched, which would let
"notchatgpt.com.evil.example" or similar lookalike hosts spoof a match).

Hard policy, deliberately conservative (see the P0 ticket's explicit requirements):
- A recognized AI-assistant hostname -> that assistant's label.
- Ordinary Google organic search ("google" / medium=organic) is NEVER classified as
  a confirmed AI Overview visit — the GA4 Data API has no dimension that separates
  an AI Overview click from any other organic Google result click. It is always
  "unattributable", regardless of how much of it might plausibly be AI Overview
  traffic. Do not "improve" this without a real GA4 capability that didn't exist at
  the time this was written.
- Direct traffic, or anything with no real source value, is "unattributable" — never
  inferred as AI traffic without supporting referrer evidence.
- Any other real, non-empty referral hostname that isn't on the known-assistant list
  is "other_identifiable" (GA4 could identify SOME source, just not an AI assistant).
"""
from __future__ import annotations

from app.db.models import AI_SOURCE_LABELS  # noqa: F401 — re-exported for callers/tests

# Hostname -> label. Suffix-matched (exact host, or a proper subdomain of it) via
# _host_matches below — never a substring check.
_KNOWN_AI_HOSTS: dict[str, str] = {
    "chatgpt.com": "chatgpt",
    "chat.openai.com": "chatgpt",
    "perplexity.ai": "perplexity",
    "gemini.google.com": "gemini",
    "copilot.microsoft.com": "copilot",
    "claude.ai": "claude",
}

# GA4's own sentinel strings for "no value" / unset dimensions.
_UNSET_VALUES = {"", "(not set)", "(none)", "(direct)", "not set", "none", "direct"}


def _normalize_host(value) -> str:
    if not isinstance(value, str):
        return ""
    host = value.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _host_matches(host: str, known_domain: str) -> bool:
    """True if `host` IS `known_domain`, or a proper subdomain of it. Never a
    substring match — "chatgpt.com.evil.example" does NOT match "chatgpt.com", and
    "notchatgpt.com" does NOT match "chatgpt.com"."""
    return host == known_domain or host.endswith("." + known_domain)


def classify_source(source: str | None, medium: str | None) -> str:
    """Classify one GA4 (sessionSource, sessionMedium) pair into one of
    AI_SOURCE_LABELS. Never raises; unrecognized/empty input always resolves to
    "unattributable" (the conservative default), never to a specific AI assistant."""
    host = _normalize_host(source)
    medium_norm = _normalize_host(medium)

    if host and host not in _UNSET_VALUES:
        for known_domain, label in _KNOWN_AI_HOSTS.items():
            if _host_matches(host, known_domain):
                return label

    # Ordinary Google organic search — GA4 cannot separate an AI Overview click from
    # any other organic result click, so this is never upgraded to a specific label.
    if host == "google" and medium_norm == "organic":
        return "unattributable"

    # Direct, or genuinely no source value at all.
    if host in _UNSET_VALUES or not host:
        return "unattributable"

    # Some other real, identifiable referrer GA4 reported, just not a known assistant.
    return "other_identifiable"
