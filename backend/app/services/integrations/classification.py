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

# Other AI assistants that send a recognisable referrer host. Curated and conservative:
# a host is listed only when it IS an AI assistant's chat product, so a session from it
# can honestly be called AI referral traffic. Same exact-or-subdomain matching.
_OTHER_AI_HOSTS: tuple[str, ...] = (
    "you.com", "poe.com", "meta.ai", "chat.deepseek.com", "chat.mistral.ai", "grok.com",
    "phind.com", "duck.ai", "kimi.ai", "chat.qwen.ai",
)

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
        if any(_host_matches(host, h) for h in _OTHER_AI_HOSTS):
            return "other_ai"

    # Ordinary Google organic search — GA4 cannot separate an AI Overview click from
    # any other organic result click, so this is never upgraded to a specific label.
    if host == "google" and medium_norm == "organic":
        return "unattributable"

    # Direct, or genuinely no source value at all.
    if host in _UNSET_VALUES or not host:
        return "unattributable"

    # Some other real, identifiable referrer GA4 reported, just not a known assistant.
    return "other_identifiable"


# GA4 default channel groups -> reporting class. Anything not listed (Email,
# Affiliates, Display, Audio, SMS, Cross-network, Organic Video, Unassigned, …) is
# "other": counted, never folded into a class it doesn't belong to.
_CHANNEL_CLASS = {
    "organic search": "organic_search", "organic shopping": "organic_search",
    "paid search": "paid_search", "paid shopping": "paid_search",
    "direct": "direct", "referral": "referral",
    "organic social": "social", "paid social": "social",
}


def traffic_class(channel_grouping: str | None, source: str | None, medium: str | None,
                  ai_label: str | None = None) -> str:
    """One of app.db.models.TRAFFIC_CLASSES for a GA4 (channel group, source, medium).

    1. A recognised AI assistant referrer (classify_source) -> that assistant.
    2. Otherwise GA4's OWN default channel grouping decides — AEOMirror does not
       re-derive search/social/paid from hosts when GA4 already has.
    3. Google organic is organic_search, never AI: an AI Overview click is
       indistinguishable from any other organic click in GA4 data.
    Unknown or unassigned traffic is "other" — never guessed into a class."""
    label = ai_label or classify_source(source, medium)
    if label in ("chatgpt", "perplexity", "gemini", "claude", "copilot", "other_ai"):
        return label
    group = (channel_grouping or "").strip().lower()
    if group in _CHANNEL_CLASS:
        return _CHANNEL_CLASS[group]
    if _normalize_host(medium) == "organic":
        return "organic_search"
    return "other"
