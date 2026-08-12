"""AI Crawler Access Check.

Detects when a site blocks the AI crawlers it needs to be readable by — at BOTH
layers that can block a bot:

  1. robots.txt   — a `Disallow` rule for the bot's token (advisory, the bot's choice)
  2. the server   — a WAF / bot-protection returning 401/403/406/429 or a challenge to
                    the bot's User-Agent while serving a normal browser fine

These have DIFFERENT fixes (edit robots.txt vs. allowlist the UA in the WAF), so the
findings say which one applies.

Security: every request is SSRF-safe. The target is validated by the caller; here each
redirect hop is re-validated with `ssrf.validate_url` (mirroring app.core.fetch), so a
redirect to a private/loopback/metadata address is rejected — we never hand a raw
`follow_redirects=True` client an attacker-influenced Location.

The whole check is best-effort: it never raises, and any failure degrades to an `error`
/ `site_unreachable` result rather than breaking the scan it runs inside.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx

from app.config import settings
from app.core.ssrf import UnsafeUrlError, validate_url

# ------------------------------- crawler set (extend here) -------------------------------
# Each entry: key, display name, provider, the robots.txt token, the live User-Agent, and
# whether a block is CRITICAL (the site won't be readable/citable by a major AI surface)
# or informational. NOTE: OAI-SearchBot (OpenAI's search crawler) is treated as critical —
# blocking it removes the site from ChatGPT search, same class of impact as GPTBot.
CRAWLERS: list[dict] = [
    {"key": "gptbot", "name": "GPTBot", "provider": "OpenAI", "robots_token": "GPTBot",
     "ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot",
     "critical": True},
    {"key": "oai_searchbot", "name": "OAI-SearchBot", "provider": "OpenAI", "robots_token": "OAI-SearchBot",
     "ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot",
     "critical": True},
    {"key": "claudebot", "name": "ClaudeBot", "provider": "Anthropic", "robots_token": "ClaudeBot",
     "ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ClaudeBot/1.0; +claudebot@anthropic.com",
     "critical": True},
    {"key": "perplexitybot", "name": "PerplexityBot", "provider": "Perplexity", "robots_token": "PerplexityBot",
     "ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
     "critical": True},
    {"key": "google_extended", "name": "Google-Extended", "provider": "Google (AI training)",
     "robots_token": "Google-Extended",
     "ua": "Mozilla/5.0 (compatible; Google-Extended/1.0; +https://developers.google.com/search/docs/crawling-indexing/google-common-crawlers)",
     "critical": True},
    {"key": "ccbot", "name": "CCBot", "provider": "Common Crawl", "robots_token": "CCBot",
     "ua": "CCBot/2.0 (https://commoncrawl.org/faq/)",
     "critical": False},
    {"key": "bytespider", "name": "Bytespider", "provider": "ByteDance", "robots_token": "Bytespider",
     "ua": "Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 "
           "(compatible; Bytespider; spider-feedback@bytedance.com)",
     "critical": False},
]

# A current desktop Chrome UA — the CONTROL request. If this fails, the site is down and
# we must NOT emit per-bot "blocked" findings (they'd all be false).
CONTROL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# Server responses that indicate a UA-specific block (not a generic error).
_BLOCK_STATUSES = {401, 403, 406, 429}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

# Statuses
ALLOWED = "allowed"
BLOCKED_BY_ROBOTS = "blocked_by_robots"
BLOCKED_BY_SERVER = "blocked_by_server"
ERROR = "error"

# Severities
CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"


# ------------------------------- robots.txt parsing -------------------------------
def _parse_groups(text: str) -> dict[str, list[tuple[str, str]]]:
    """Parse robots.txt into {user-agent(lower): [(allow|disallow, value), ...]}.
    Consecutive User-agent lines share the rule block that follows them."""
    groups: dict[str, list[tuple[str, str]]] = {}
    current: list[str] = []
    started_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if started_rules:          # a new group begins
                current = []
                started_rules = False
            current.append(value.lower())
            groups.setdefault(value.lower(), [])
        elif field in ("allow", "disallow") and current:
            started_rules = True
            for agent in current:
                groups[agent].append((field, value))
    return groups


def _pattern_matches(pattern: str, path: str) -> bool:
    """robots.txt path match: prefix match, with `*` wildcard and `$` end-anchor."""
    rx = re.escape(pattern).replace(r"\*", ".*")
    if rx.endswith(r"\$"):
        rx = rx[:-2] + "$"
    return re.match(rx, path) is not None


def _path_blocked(rules: list[tuple[str, str]], path: str) -> bool:
    """Longest-match wins; on a tie, Allow beats Disallow (Google's rule). An empty
    Disallow value means 'allow everything' and is a no-op."""
    best_len, best_is_allow = -1, True
    for typ, value in rules:
        if value == "":
            continue
        if _pattern_matches(value, path):
            L = len(value)
            if L > best_len or (L == best_len and typ == "allow"):
                best_len, best_is_allow = L, (typ == "allow")
    if best_len < 0:
        return False
    return not best_is_allow


def _robots_disallows(groups: dict, token: str, path: str) -> bool:
    """Whether `token`'s rules (or the `*` fallback when the bot has no own group)
    disallow `path`."""
    rules = groups.get(token.lower())
    if rules is None:
        rules = groups.get("*", [])
    return _path_blocked(rules, path)


# ------------------------------- SSRF-safe fetching -------------------------------
async def _fetch(client: httpx.AsyncClient, url: str, ua: str, timeout: float) -> dict:
    """One GET with a given UA. Redirects are followed MANUALLY and every hop is
    SSRF-revalidated. Never raises — returns {status_code, response_time_ms, final_url,
    error}. error is None on an HTTP response (even 4xx/5xx); a string on a transport
    failure / timeout / SSRF rejection."""
    start = time.perf_counter()
    current = url

    def elapsed() -> int:
        return int((time.perf_counter() - start) * 1000)

    try:
        for _ in range(settings.fetch_max_redirects + 1):
            validate_url(current, check_chars=False)   # SSRF on every hop
            resp = await client.get(current, headers={"User-Agent": ua},
                                    timeout=timeout, follow_redirects=False)
            if resp.status_code in _REDIRECT_STATUSES and resp.headers.get("location"):
                current = urljoin(current, resp.headers["location"])
                continue
            return {"status_code": resp.status_code, "response_time_ms": elapsed(),
                    "final_url": str(resp.url), "error": None}
        return {"status_code": None, "response_time_ms": elapsed(),
                "final_url": current, "error": "too_many_redirects"}
    except (httpx.TimeoutException,) as e:
        return {"status_code": None, "response_time_ms": elapsed(),
                "final_url": current, "error": "timeout"}
    except (UnsafeUrlError,) as e:
        return {"status_code": None, "response_time_ms": elapsed(),
                "final_url": current, "error": "ssrf_blocked"}
    except Exception as e:   # noqa: BLE001 — DNS/connection/protocol: never fatal
        return {"status_code": None, "response_time_ms": elapsed(),
                "final_url": current, "error": type(e).__name__}


async def _fetch_robots(client: httpx.AsyncClient, origin: str, timeout: float) -> tuple[str, str, str | None]:
    """Fetch and classify robots.txt. Returns (text, status, warning):
      status: 'found' | 'missing' | 'malformed'; warning is a message or None.
    A 404 / non-2xx / transport error => missing (crawling allowed by default)."""
    try:
        resp = await client.get(urljoin(origin, "/robots.txt"),
                                headers={"User-Agent": CONTROL_UA},
                                timeout=timeout, follow_redirects=True)
    except Exception:   # noqa: BLE001
        return "", "missing", None
    if resp.status_code == 404 or not (200 <= resp.status_code < 300):
        return "", "missing", None
    text = resp.text or ""
    # "Malformed" = a body that has content but no parseable directives at all.
    if text.strip() and not re.search(r"(?im)^\s*(user-agent|disallow|allow)\s*:", text):
        return text, "malformed", "robots.txt is present but has no parseable rules — treating access as allowed."
    return text, "found", None


# ------------------------------- classification + findings -------------------------------
def _remediation(status: str, bot_name: str) -> str:
    if status == BLOCKED_BY_ROBOTS:
        return (f"Edit robots.txt: remove the Disallow rule affecting {bot_name} "
                f"(or add an explicit `Allow:` / `User-agent: {bot_name}` block).")
    if status == BLOCKED_BY_SERVER:
        return (f"Allowlist the {bot_name} user-agent in your WAF / bot-protection "
                f"(Cloudflare, Akamai, etc.). robots.txt will NOT fix a server/WAF block.")
    return "Re-run the check — this was a transient request error, not a deliberate block."


def _finding(bot: dict, status: str, detail: str) -> dict:
    critical = bot["critical"]
    if status in (BLOCKED_BY_ROBOTS, BLOCKED_BY_SERVER):
        severity = CRITICAL if critical else WARNING
        title = f"{bot['name']} is blocked"
    else:   # error
        severity = INFO
        title = f"{bot['name']} could not be verified"
    return {"severity": severity, "bot": bot["key"], "bot_name": bot["name"],
            "critical": critical, "status": status, "title": title,
            "cause": detail, "remediation": _remediation(status, bot["name"])}


def _classify(bot: dict, groups: dict, path: str, control_ok: bool, res: dict) -> tuple[str, str]:
    """Return (status, cause). robots is authoritative for 'blocked'; server blocks are
    only asserted when the CONTROL request succeeded."""
    if _robots_disallows(groups, bot["robots_token"], path):
        return BLOCKED_BY_ROBOTS, "Disallowed for this bot in robots.txt."
    if res.get("error"):
        return ERROR, f"Request failed ({res['error']})."
    code = res.get("status_code")
    if control_ok and code in _BLOCK_STATUSES:
        return BLOCKED_BY_SERVER, f"Server returned HTTP {code} to this bot's user-agent while a browser was served normally."
    return ALLOWED, "Reachable and not disallowed."


# ------------------------------- entry point -------------------------------
async def check_crawler_access(url: str, *, transport: "httpx.BaseTransport | None" = None) -> dict:
    """Run the full AI-crawler access check for one URL. Best-effort; never raises.

    `transport` is a test seam (inject an httpx.MockTransport). Returns the structured
    result stored on the scan under result['crawler_access']."""
    checked_at = datetime.now(timezone.utc).isoformat()
    path = urlparse(url).path or "/"
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    timeout = float(settings.crawler_access_timeout_seconds)
    sem = asyncio.Semaphore(max(1, settings.crawler_access_concurrency))

    base = {"checked_at": checked_at, "target_url": url}
    try:
        async with httpx.AsyncClient(follow_redirects=False, transport=transport) as client:
            robots_text, robots_status, robots_warning = await _fetch_robots(client, origin, timeout)
            groups = _parse_groups(robots_text) if robots_status != "missing" else {}

            async def _one(target_ua: str) -> dict:
                async with sem:
                    return await _fetch(client, url, target_ua, timeout)

            # Control + every bot, concurrently (bounded by the semaphore).
            control_task = asyncio.create_task(_one(CONTROL_UA))
            bot_tasks = [asyncio.create_task(_one(b["ua"])) for b in CRAWLERS]
            control = await control_task
            bot_results = [await t for t in bot_tasks]
    except Exception as e:   # noqa: BLE001 — the check must never break the scan
        return {**base, "robots": {"status": "error", "warning": type(e).__name__},
                "control": {"status_code": None, "error": type(e).__name__},
                "site_unreachable": True, "bots": [], "findings": [
                    {"severity": INFO, "bot": None, "bot_name": None, "critical": False,
                     "status": ERROR, "title": "Crawler access check failed",
                     "cause": f"Unexpected error ({type(e).__name__}).",
                     "remediation": "Re-run the scan."}]}

    control_ok = control.get("error") is None and (control.get("status_code") or 0) // 100 == 2

    robots_out = {"status": robots_status, "warning": robots_warning}

    # Site down: emit ONE site_unreachable finding, never seven false per-bot blocks.
    if not control_ok:
        bots = [{"key": b["key"], "name": b["name"], "provider": b["provider"],
                 "critical": b["critical"], "status": ERROR,
                 "status_code": None, "response_time_ms": None, "final_url": None,
                 "error": "control_failed"} for b in CRAWLERS]
        cause = (f"The control (browser) request failed — HTTP "
                 f"{control.get('status_code')}" if control.get("error") is None
                 else f"The control (browser) request failed ({control.get('error')})")
        finding = {"severity": INFO, "bot": None, "bot_name": None, "critical": False,
                   "status": "site_unreachable", "title": "Site unreachable — crawler access not verified",
                   "cause": cause + ". No per-bot block was recorded (it would be a false positive).",
                   "remediation": "Check the site is up and reachable, then re-run the scan."}
        return {**base, "robots": robots_out, "control": control,
                "site_unreachable": True, "bots": bots, "findings": [finding]}

    bots: list[dict] = []
    findings: list[dict] = []
    for bot, res in zip(CRAWLERS, bot_results):
        status, cause = _classify(bot, groups, path, control_ok, res)
        bots.append({"key": bot["key"], "name": bot["name"], "provider": bot["provider"],
                     "critical": bot["critical"], "status": status,
                     "status_code": res.get("status_code"),
                     "response_time_ms": res.get("response_time_ms"),
                     "final_url": res.get("final_url"), "error": res.get("error")})
        if status != ALLOWED:
            findings.append(_finding(bot, status, cause))

    summary = {
        "blocked_critical": sum(1 for f in findings if f["severity"] == CRITICAL),
        "blocked_total": sum(1 for f in findings if f["status"] in (BLOCKED_BY_ROBOTS, BLOCKED_BY_SERVER)),
        "errors": sum(1 for b in bots if b["status"] == ERROR),
    }
    return {**base, "robots": robots_out, "control": control,
            "site_unreachable": False, "bots": bots, "findings": findings, "summary": summary}


def has_critical_block(crawler_access: dict | None) -> bool:
    """True if any CRITICAL crawler is blocked in a stored result (null-safe)."""
    if not crawler_access:
        return False
    return any(f.get("severity") == CRITICAL for f in crawler_access.get("findings", []))
