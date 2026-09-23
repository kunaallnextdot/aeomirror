"""Technical SEO & Indexability Intelligence.

Pure, deterministic, read-side derivation over the SAME crawl evidence Phase 1/4
already use (`sections` from a stored scan, plus the per-page HTTP/redirect facts
`fetch()` captures and `routes_scan` persists) — no new scanner signal, no new
scoring, no LLM, no Google Search Console/Indexing API call, no per-URL network
requests of its own. Mirrors `reports.phase4`'s architecture and conventions exactly
(same gating style, same "never invent evidence" discipline).

CRITICAL terminology discipline: this module answers "can a crawler technically
reach and index this URL, based on what THIS scan observed" — it never claims a URL
IS indexed/deindexed by Google, or that any ranking exists. Every field is grounded
in real crawl evidence; where the evidence genuinely doesn't exist, the field is
null/unknown rather than a guess — see `_indexability_status`'s "unknown" branch and
`build_technical_seo_block`'s default-crawlable comment for exactly where that line
is drawn.
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.scanner.bulk import normalize_dedup_key

# ------------------------------- shared lookups -------------------------------
def _section(sections: list[dict] | None, signal_id: str) -> dict:
    for s in sections or []:
        if s.get("id") == signal_id:
            return s
    return {}


def _safe_key(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return normalize_dedup_key(url)
    except Exception:   # noqa: BLE001 — a malformed URL must never break the report
        return None


def _status_bucket(code: int | None) -> str | None:
    if not isinstance(code, int):
        return None
    if 200 <= code < 300: return "2xx"
    if 300 <= code < 400: return "3xx"
    if 400 <= code < 500: return "4xx"
    if 500 <= code < 600: return "5xx"
    return None


def _split_directives(raw: str | None) -> list[str]:
    return [d.strip().lower() for d in (raw or "").split(",") if d.strip()]


def _has_noindex(directives: list[str]) -> bool:
    return any(d in ("noindex", "none") for d in directives)


def _canonical_type(url: str | None, canonical_href: str | None) -> str:
    """missing | invalid | self | other — see `build_technical_seo_block`'s docstring
    for why "canonical to another URL" never by itself implies non-indexable."""
    if not canonical_href:
        return "missing"
    p = urlparse(canonical_href)
    if p.scheme not in ("http", "https") or not p.hostname:
        return "invalid"
    if url and _safe_key(canonical_href) == _safe_key(url):
        return "self"
    return "other"


def _indexability_status(*, status_code: int | None, robots_crawlable: bool | None,
                         meta_directives: list[str], xrobots_directives: list[str],
                         is_redirect: bool) -> str:
    """Deterministic priority order (most fundamental gate first — documented here and
    exercised by tests, per the task's "don't invent an undocumented threshold" rule):

      1. blocked      — robots.txt disallows this URL for crawling at all
      2. error        — HTTP 4xx/5xx: there's no page here to index
      3. not_indexable — a real noindex directive is present (meta OR header)
      4. redirected   — the URL forwards to a different final destination
      5. indexable    — 2xx, not blocked, not noindex, not redirected
      6. unknown      — status/evidence genuinely wasn't captured for this URL
    """
    bucket = _status_bucket(status_code)
    if robots_crawlable is False:
        return "blocked"
    if bucket in ("4xx", "5xx"):
        return "error"
    if _has_noindex(meta_directives) or _has_noindex(xrobots_directives):
        return "not_indexable"
    if is_redirect:
        return "redirected"
    if bucket == "2xx":
        return "indexable"
    return "unknown"


_SUMMARY_KEY = {
    "indexable": "indexable", "not_indexable": "not_indexable", "blocked": "blocked",
    "redirected": "redirected", "error": "errors", "unknown": "unknown",
}


def _build_record(*, url: str, status_code, redirect_chain, final_url, canonical_href,
                  meta_robots_raw, xrobots_raw, robots_exists, robots_crawlable,
                  in_sitemap: bool, status_map: dict, redirect_map: dict) -> dict:
    redirect_chain = redirect_chain or []
    is_redirect = bool(redirect_chain)
    meta_directives = _split_directives(meta_robots_raw)
    xrobots_directives = _split_directives(xrobots_raw)
    canonical_type = _canonical_type(url, canonical_href)
    canonical_key = _safe_key(canonical_href) if canonical_type == "other" else None
    canonical_target_status = status_map.get(canonical_key) if canonical_key else None
    canonical_target_redirects = bool(redirect_map.get(canonical_key)) if canonical_key else False

    status = _indexability_status(
        status_code=status_code, robots_crawlable=robots_crawlable,
        meta_directives=meta_directives, xrobots_directives=xrobots_directives,
        is_redirect=is_redirect)

    bucket = _status_bucket(status_code)
    issues: list[str] = []
    if bucket == "4xx": issues.append("ERROR_4XX")
    if bucket == "5xx": issues.append("ERROR_5XX")
    if robots_crawlable is False: issues.append("ROBOTS_BLOCKED")
    if _has_noindex(meta_directives): issues.append("NOINDEX_META")
    if _has_noindex(xrobots_directives): issues.append("NOINDEX_XROBOTS")
    if is_redirect:
        issues.append("REDIRECT")
        if len(redirect_chain) > 1:
            issues.append("REDIRECT_CHAIN")
        hop_urls = [h.get("url") for h in redirect_chain] + [final_url]
        if len(set(hop_urls)) < len(hop_urls):
            issues.append("REDIRECT_LOOP")
        if bucket in ("4xx", "5xx"):   # status_code IS the final (already-resolved) status
            issues.append("REDIRECT_TO_ERROR")
    if canonical_type == "invalid":
        issues.append("CANONICAL_INVALID")
    if canonical_type == "other":
        issues.append("CANONICAL_EXTERNAL")
        target_bucket = _status_bucket(canonical_target_status)
        if target_bucket == "4xx": issues.append("CANONICAL_4XX")
        if target_bucket == "5xx": issues.append("CANONICAL_5XX")
        if canonical_target_redirects: issues.append("CANONICAL_REDIRECT")
    if status == "indexable" and not in_sitemap:
        issues.append("NOT_IN_SITEMAP")

    evidence: list[str] = [f"HTTP status: {status_code if status_code is not None else 'unknown'}"]
    if meta_directives:
        evidence.append(f"meta robots: {', '.join(meta_directives)}")
    if xrobots_directives:
        evidence.append(f"X-Robots-Tag: {', '.join(xrobots_directives)}")
    if canonical_type == "self":
        evidence.append("canonical: self")
    elif canonical_type == "missing":
        evidence.append("canonical: missing")
    elif canonical_type == "invalid":
        evidence.append(f"canonical: invalid ({canonical_href})")
    else:
        evidence.append(f"canonical: points to {canonical_href}")
    if robots_exists:
        evidence.append("robots.txt: " + ("blocked" if robots_crawlable is False else "allowed"))
    evidence.append("sitemap: " + ("present" if in_sitemap else "not present"))

    return {
        "url": url,
        "status_code": status_code,
        "crawlable": None if robots_crawlable is None else bool(robots_crawlable),
        "indexable": status == "indexable",
        "meta_robots": meta_directives,
        "x_robots_tag": xrobots_directives,
        "canonical": canonical_href or None,
        "canonical_type": canonical_type,
        "canonical_target_status": canonical_target_status,
        "redirect": is_redirect,
        "redirect_chain": redirect_chain,
        "final_url": final_url or url,
        "sitemap": in_sitemap,
        "indexability_status": status,
        "issues": issues,
        "evidence": evidence,
    }


def _status_from_error(error: str | None) -> int | None:
    """Recover a status code from a bulk failed-page error string ("HTTP 404") —
    the only place that value survives for a failed bulk page. None (unknown) for a
    transport-level failure (timeout, DNS, connection reset) with no real status."""
    if not error or not error.startswith("HTTP "):
        return None
    try:
        return int(error.split()[1])
    except (IndexError, ValueError):
        return None


UNAVAILABLE_SCAN_INCOMPLETE = {"available": False, "reason": "scan_incomplete"}


def build_technical_seo_block(*, sections: list[dict] | None, url: str | None,
                              status_code=None, redirect_chain=None, final_url=None,
                              bulk_pages: list[dict] | None = None,
                              scan_ready: bool = True) -> dict:
    """The full, ungated Technical SEO block embedded additively into `build_report`'s
    output (`report["technical_seo"]`) — mirrors `reports.phase4.build_phase4_block`.

    Read-side only: no scanner/scoring change, no persistence, no network call. A
    canonical pointing to ANOTHER (valid) URL is never by itself treated as reducing
    indexability — `canonical_type` records the relationship, `indexability_status`
    stays "indexable" when the page itself is genuinely crawlable/indexable, per the
    task's explicit "canonical to other != not indexable" rule."""
    if not scan_ready:
        return dict(UNAVAILABLE_SCAN_INCOMPLETE)

    meta_ev = _section(sections, "metadata").get("evidence") or {}
    robots_ev = _section(sections, "robots").get("evidence") or {}
    sitemap_ev = _section(sections, "sitemap").get("evidence") or {}
    sitemap_urls = sitemap_ev.get("urls") or sitemap_ev.get("sample_urls") or []
    sitemap_keys = {_safe_key(u) for u in sitemap_urls if u}

    robots_exists = bool(robots_ev.get("exists"))
    # No robots.txt at all -> default-allow (the actual robots.txt spec, not a guess);
    # otherwise reuse the signal's own blanket-disallow evaluation ("Disallow: /").
    default_crawlable = True if not robots_exists else bool(robots_ev.get("crawlable", True))

    entries: list[dict] = []
    if bulk_pages:
        for p in bulk_pages:
            if p.get("error"):
                entries.append({
                    "url": p.get("url"), "status_code": _status_from_error(p.get("error")),
                    "redirect_chain": [], "final_url": p.get("url"),
                    "canonical_href": None, "meta_robots_raw": "", "xrobots_raw": "",
                    "robots_exists": robots_exists, "robots_crawlable": default_crawlable,
                })
                continue
            entries.append({
                "url": p.get("url"), "status_code": p.get("status_code"),
                "redirect_chain": p.get("redirect_chain") or [],
                "final_url": p.get("final_url") or p.get("url"),
                "canonical_href": p.get("canonical"),
                "meta_robots_raw": p.get("meta_robots") or "",
                "xrobots_raw": p.get("x_robots_tag") or "",
                "robots_exists": p.get("robots_exists", robots_exists),
                "robots_crawlable": p.get("robots_crawlable", default_crawlable),
            })
    elif url:
        entries.append({
            "url": url, "status_code": status_code,
            "redirect_chain": redirect_chain or [], "final_url": final_url or url,
            "canonical_href": meta_ev.get("canonical"),
            "meta_robots_raw": meta_ev.get("robots_meta") or "",
            "xrobots_raw": meta_ev.get("x_robots_tag") or "",
            "robots_exists": robots_exists, "robots_crawlable": default_crawlable,
        })

    status_map = {_safe_key(e["url"]): e["status_code"] for e in entries if e.get("url")}
    redirect_map = {_safe_key(e["url"]): bool(e.get("redirect_chain")) for e in entries if e.get("url")}
    records = [
        _build_record(**e, in_sitemap=(_safe_key(e["url"]) in sitemap_keys),
                     status_map=status_map, redirect_map=redirect_map)
        for e in entries if e.get("url")
    ]

    crawled_keys = {_safe_key(e["url"]) for e in entries if e.get("url")}
    uncrawled_sitemap = [u for u in sitemap_urls if u and _safe_key(u) not in crawled_keys]

    summary = {k: 0 for k in ("indexable", "not_indexable", "blocked", "redirected", "errors", "unknown")}
    for r in records:
        summary[_SUMMARY_KEY[r["indexability_status"]]] += 1

    return {
        "available": True,
        "summary": {"total_urls": len(records), **summary},
        "issues": _issue_summary(records, uncrawled_sitemap),
        "urls": records,
        "sitemap_urls_not_crawled": uncrawled_sitemap,
    }


def _issue_summary(records: list[dict], uncrawled_sitemap: list[str]) -> list[dict]:
    """Deterministic, evidence-only issue rollup. Severities follow the task's own
    prioritization guidance (5xx/broken-canonical/loops = Critical; noindex/robots-block
    /broken sitemap entries/4xx = High; multi-hop redirects/malformed canonical =
    Medium; sitemap-presence gaps = Low) — never invented ad hoc per issue."""
    def urls_with(code: str) -> list[str]:
        return [r["url"] for r in records if code in r["issues"]]

    out: list[dict] = []

    def add(code: str, label: str, severity: str) -> None:
        urls = urls_with(code)
        if urls:
            out.append({"code": code, "severity": severity, "count": len(urls),
                        "label": label.format(n=len(urls), s="" if len(urls) == 1 else "s"),
                        "affected_urls": urls})

    add("ERROR_5XX", "{n} URL{s} return 5xx", "Critical")
    add("CANONICAL_4XX", "{n} canonical target{s} return 4xx", "Critical")
    add("CANONICAL_5XX", "{n} canonical target{s} return 5xx", "Critical")
    add("REDIRECT_LOOP", "{n} URL{s} redirect in a loop", "Critical")
    add("NOINDEX_META", "{n} URL{s} use meta robots noindex", "High")
    add("NOINDEX_XROBOTS", "{n} URL{s} use X-Robots-Tag noindex", "High")
    add("ROBOTS_BLOCKED", "{n} URL{s} blocked by robots.txt", "High")
    add("ERROR_4XX", "{n} URL{s} return 4xx", "High")
    add("REDIRECT_CHAIN", "{n} URL{s} redirect through multiple hops", "Medium")
    add("REDIRECT_TO_ERROR", "{n} redirect{s} lead to an error page", "Medium")
    add("CANONICAL_INVALID", "{n} URL{s} have a malformed canonical", "Medium")
    add("CANONICAL_REDIRECT", "{n} canonical target{s} redirect elsewhere", "Medium")
    add("NOT_IN_SITEMAP", "{n} indexable URL{s} not present in the sitemap", "Low")

    if uncrawled_sitemap:
        n = len(uncrawled_sitemap)
        out.append({"code": "SITEMAP_URL_NOT_CRAWLED", "severity": "Low", "count": n,
                    "label": f"{n} sitemap URL{'s' if n != 1 else ''} not present in this scan",
                    "affected_urls": uncrawled_sitemap})
    return out


_SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def gate_technical_seo(block: dict | None, unlocked: bool, *,
                       free_url_limit: int = 3, free_issue_limit: int = 3) -> dict | None:
    """Server-side trim to a free preview — same model as `phase4.gate_phase4`: a
    locked caller only ever receives a small REAL preview (top issues + a few real
    affected URLs) + locked counts. The full issue list / URL table / sitemap-parity
    detail never leaves the server for a free caller."""
    if not block or unlocked or block.get("available") is False:
        return block

    issues = sorted(block.get("issues") or [], key=lambda i: _SEVERITY_RANK.get(i["severity"], 9))
    urls = block.get("urls") or []
    problem_urls = [u for u in urls if u.get("issues")]
    preview_urls = (problem_urls or urls)[:free_url_limit]
    # `affected_urls` is capped for display even though it's kept on a free-preview
    # issue (the label/severity/count are useful without a purchase) — a caller must
    # never receive the FULL locked URL list through this side channel; `count` stays
    # the honest total regardless of how many URLs are shown.
    top_issues = [{**i, "affected_urls": (i.get("affected_urls") or [])[:free_url_limit]}
                 for i in issues[:free_issue_limit]]
    uncrawled = block.get("sitemap_urls_not_crawled") or []

    return {
        **block,
        "preview": True,
        "issues": top_issues,
        "locked_issue_count": max(0, len(issues) - len(top_issues)),
        "urls": preview_urls,
        "locked_url_count": max(0, len(urls) - len(preview_urls)),
        "sitemap_urls_not_crawled": [],
        "locked_sitemap_gap_count": len(uncrawled),
    }


__all__ = ["build_technical_seo_block", "gate_technical_seo", "UNAVAILABLE_SCAN_INCOMPLETE"]
