"""Public scan endpoints. This is the free lead magnet's server side."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import time
from datetime import datetime, timezone

from app.api.deps import AuthContext, get_context, get_optional_user
from app.config import settings
from app.core.cache import rate_limiter, scan_cache
from app.core.fetch import fetch
from app.core.observability import metrics
from app.core.ssrf import UnsafeUrlError, validate_url
from app.db.models import (
    SCAN_COMPLETED, SCAN_PENDING, SCAN_RUNNING, USAGE_SCAN_JOB,
    Monitor, OrganizationMember, Scan, User,
)
from app.db.session import get_db
from app.scanner import bulk
from app.scanner.engine import score
from app.scanner.rubric_provider import get_active_rubric
from app.scanner.signals.aggregate import run_signals
from app.scanner.signals.base import clamp, status_from_score
from app.schemas.scan import BulkScanRequest, LeadRequest, ScanRequest, ScanResponse
from app.services.leads import capture_lead

router = APIRouter(prefix="/v1", tags=["scan"])


def _client_ip(request: Request) -> str:
    # Only trust X-Forwarded-For when explicitly configured behind a trusted proxy;
    # otherwise the header is attacker-controlled and would let anyone spoof an IP.
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _ip_hash(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:32]


def _normalize(url: str) -> str:
    return (url.strip().lower()
            .replace("https://", "").replace("http://", "")
            .replace("www.", "").rstrip("/"))




def _families_payload(report) -> list[dict]:
    return [{
        "id": f.id, "label": f.label, "weight": f.weight, "earned": f.earned,
        "checks": [asdict(c) for c in f.checks],
    } for f in report.families]


def _report_payload(scan_id: str, report, signals: dict, scanned_at: str,
                    duration_ms: int, crawler_access: dict | None = None,
                    page=None) -> dict:
    """JSON-serializable representation cached in Redis and reused to build the
    response. No live objects are cached (JSON only, never pickle)."""
    return {
        "scan_id": scan_id, "url": report.url, "domain": report.domain,
        "ars": report.ars, "rubric_version": report.rubric_version,
        "families": _families_payload(report),
        "top_issues": report.top_issues, "crawlers": report.crawlers,
        # Phase 3 signal report (additive)
        "overall_score": signals["overall_score"],
        "scanner_version": signals["scanner_version"],
        "scanned_at": scanned_at,
        "duration_ms": duration_ms,
        "sections": signals["sections"],
        "crawler_access": crawler_access,
        # Single-page scans are complete the moment they return (only site scans run
        # in the background), so the response carries the terminal status directly.
        "status": SCAN_COMPLETED,
        # Technical SEO & Indexability (additive) — see run_scan's row.result.
        "status_code": getattr(page, "status_code", None),
        "redirect_chain": getattr(page, "redirect_chain", None) or [],
        "final_url": (getattr(page, "final_url", None) or getattr(page, "url", None)),
    }


def _response_from_payload(payload: dict, remaining: int) -> ScanResponse:
    return ScanResponse(**payload, remaining_free_scans=remaining)


def _strip_bulk_details(bulk: dict) -> dict:
    """Reduce each bulk page to score-level info only (url, overall_score,
    status_label, top_issue) and flag details_locked. Used when a Free org views a
    bulk scan — the detailed per-page sections are a Pro feature. Error rows carry no
    details and pass through unchanged."""
    keep = ("url", "overall_score", "status_label", "top_issue")
    pages = [p if "error" in p else {k: p[k] for k in keep if k in p}
             for p in bulk.get("pages", [])]
    return {**bulk, "pages": pages, "details_locked": True}


def build_scan_response(row: Scan, remaining: int = 0, *,
                        hide_page_details: bool = False) -> ScanResponse:
    """Serialize a stored Scan row to a ScanResponse. Shared by GET /v1/scan/{id}
    and the dashboard endpoints. .get() keeps pre-Phase-3 rows deserializable. When
    `hide_page_details` is set (Free org viewing a bulk scan), per-page sections are
    stripped server-side — the client never receives locked detail data."""
    r = row.result or {}
    bulk = r.get("bulk")
    # A bulk scan's top-level sections/top_issues are ONE page's full breakdown, so
    # they are stripped alongside the per-page detail — the client only ever gets
    # scores + top-issue one-liners for a locked bulk scan.
    hide = bool(bulk and hide_page_details)
    if hide:
        bulk = _strip_bulk_details(bulk)
    return ScanResponse(
        scan_id=row.id, url=row.url, domain=_normalize(row.url), ars=row.ars,
        rubric_version=row.rubric_version, families=[] if hide else r.get("families", []),
        top_issues=[] if hide else r.get("top_issues", []), crawlers=r.get("crawlers", []),
        remaining_free_scans=remaining,
        overall_score=r.get("overall_score"), scanner_version=r.get("scanner_version"),
        scanned_at=r.get("scanned_at"), duration_ms=r.get("duration_ms"),
        sections=[] if hide else r.get("sections", []), bulk=bulk,
        status=getattr(row, "status", SCAN_COMPLETED), progress=row.progress,
        error=r.get("error"), crawler_access=r.get("crawler_access"),
    )


def _persist_from_payload(db: Session, payload: dict, ip: str,
                          org_id: str | None, user_id: str | None) -> Scan:
    """Persist a Scan row from an already-computed (cached) payload, attributed to
    an organization. No refetch/rescore — used on the cache-hit path for signed-in
    users so the result still lands in their private history."""
    row = Scan(
        url=payload["url"], normalized_url=_normalize(payload["url"]),
        ars=payload["ars"], rubric_version=payload["rubric_version"],
        # Keep the rubric linkage on cache-hit rows too. The cached payload's
        # rubric_version IS the value run_scan writes to rubric_version_id (both are
        # the active rubric's version), so reuse it — no get_active_rubric round-trip.
        rubric_version_id=payload["rubric_version"],
        result={
            "families": payload.get("families", []),
            "top_issues": payload.get("top_issues", []),
            "crawlers": payload.get("crawlers", []),
            "overall_score": payload.get("overall_score"),
            "scanner_version": payload.get("scanner_version"),
            "scanned_at": payload.get("scanned_at"),
            "duration_ms": payload.get("duration_ms"),
            "sections": payload.get("sections", []),
            "status_code": payload.get("status_code"),
            "redirect_chain": payload.get("redirect_chain") or [],
            "final_url": payload.get("final_url"),
        },
        requester_ip_hash=_ip_hash(ip),
        organization_id=org_id, user_id=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _sync_monitor_latest_scan(db, org_id, row.normalized_url, row.id, row.result.get("overall_score"))
    return row


def _sync_monitor_latest_scan(db: Session, org_id: str | None, normalized_url: str,
                              scan_id: str, overall_score: int | None) -> None:
    """Keep Monitor.latest_scan_id current for any ORDINARY scan of a URL this org
    already monitors (a manual rerun from Scan Details, a fresh or cache-hit scan
    from the scanner) — not only scans the monitor scheduler itself triggered.

    Without this, anything that reads latest_scan_id (e.g. the Answer Simulator's
    scan-derived question bank, GET /monitors/{id}/answer-simulator/questions) keeps
    reflecting whichever scan the scheduler last ran, even after a newer manual scan
    exists for the same site — the root cause of an older scan's keywords/questions
    still showing up as suggestions for a site that has since been rescanned.

    Only the pointer/score/timestamp are touched here — never MonitorHistory/alerts/
    notifications, which stay exclusively the monitor scan's own responsibility (see
    monitoring/runner.py::run_scan_for_monitor)."""
    if not org_id:
        return
    monitor = (db.query(Monitor)
               .filter(Monitor.organization_id == org_id,
                       Monitor.normalized_url == normalized_url)
               .first())
    if not monitor:
        return
    monitor.latest_scan_id = scan_id
    monitor.latest_score = overall_score
    monitor.last_scan_at = datetime.utcnow()
    db.commit()


async def _crawler_access_for(safe_url: str) -> dict | None:
    """Run the AI-crawler access check, best-effort. Returns None when the feature is
    disabled or the check errors — the scan must never fail because of it."""
    if not settings.crawler_access_enabled:
        return None
    try:
        from app.services.crawler_access import check_crawler_access
        return await check_crawler_access(safe_url)
    except Exception:   # noqa: BLE001 — a crawler-check failure must not break the scan
        return None


_snapshot_log = logging.getLogger("app.api.scan")


def _prepare_snapshot(db: Session, page, crawler_access: dict | None,
                      monitor_id: str | None) -> tuple[dict | None, dict | None]:
    """For a MONITOR scan, build this page's deterministic snapshot and capture the
    monitor's PREVIOUS snapshot payload as the diff baseline (before the new one is
    persisted). Returns (new_payload, prev_payload). ( None, None ) for non-monitor scans
    or on any build failure — attribution must never break a scan."""
    if not monitor_id:
        return None, None
    try:
        from app.services import snapshot as snap
        payload = snap.build_snapshot(page, crawler_access)
        prev = snap.previous_snapshot(db, monitor_id)
        return payload, (prev.payload if prev else None)
    except Exception:   # noqa: BLE001 — snapshot build is best-effort
        _snapshot_log.exception("snapshot build failed for monitor %s", monitor_id)
        return None, None


def _safe_diff(prev_payload: dict | None, curr_payload: dict, monitor_id: str | None) -> list:
    """Diff, but NEVER raise: a schema_version mismatch (or any diff error) yields an
    empty change set and a WARNING — the new snapshot has already been persisted as the
    baseline, so the monitor self-recovers on the next scan instead of deadlocking."""
    from app.services.snapshot_diff import SnapshotVersionMismatch, diff_snapshots
    try:
        return diff_snapshots(prev_payload, curr_payload)
    except SnapshotVersionMismatch as e:
        _snapshot_log.warning("snapshot version mismatch for monitor %s (%s); baseline "
                              "advanced, empty change set", monitor_id, e)
        return []
    except Exception:   # noqa: BLE001 — a diff bug must not block the baseline
        _snapshot_log.warning("snapshot diff failed for monitor %s", monitor_id)
        return []


async def run_scan(db: Session, safe_url: str, ip: str,
                   org_id: str | None = None, user_id: str | None = None,
                   monitor_id: str | None = None) -> dict:
    """Fetch, score, run signals, persist a NEW scan row, cache, return the payload.
    Single source of truth for executing a scan (used by create_scan and rerun).
    When org_id/user_id are given the scan is private to that organization;
    otherwise it stays anonymous (IP-scoped). Raises HTTPException on fetch/SSRF
    failure. Rate limiting/caching are the caller's responsibility."""
    started = time.perf_counter()
    normalized = _normalize(safe_url)
    try:
        page = await fetch(safe_url)
    except UnsafeUrlError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400,
            detail="Could not fetch that URL. Check the address and try again.")
    rubric = get_active_rubric(db)
    report = score(page, rubric)
    # Phase 3: modular AI-visibility signals (pure functions; no LLM/paid API).
    # `overall_score` is the honest weighted aggregate of the 10 signals that the UI
    # actually displays (robots, sitemap, metadata, schema, content, links,
    # performance, accessibility, freshness, ai_readiness). This is THE AI Readiness
    # Score shown everywhere — homepage gauge, dashboard, scan details, report — so the
    # headline always reflects the checks (incl. failures) shown beneath it. The legacy
    # 6-family `ars` is kept in the payload for reference but is no longer the headline.
    signals = run_signals(page)
    # AI crawler access check (single-page + monitor scans only; never bulk). Best-effort
    # and gated by a flag so it can be disabled without a deploy. Null for old rows / when
    # disabled — the API reads it null-safely.
    crawler_access = await _crawler_access_for(safe_url)
    # Change attribution (monitor scans only): build this page's snapshot and capture the
    # previous baseline. The DIFF runs later — AFTER the new snapshot is persisted — so a
    # version mismatch can never skip persistence and deadlock the monitor.
    snapshot_payload, prev_payload = _prepare_snapshot(db, page, crawler_access, monitor_id)
    scanned_at = datetime.now(timezone.utc).isoformat()
    duration_ms = int((time.perf_counter() - started) * 1000)

    row = Scan(
        url=report.url, normalized_url=normalized, ars=report.ars,
        rubric_version=report.rubric_version, rubric_version_id=rubric.version,
        result={
            "families": _families_payload(report),
            "top_issues": report.top_issues, "crawlers": report.crawlers,
            "overall_score": signals["overall_score"],
            "scanner_version": signals["scanner_version"],
            "scanned_at": scanned_at, "duration_ms": duration_ms,
            "sections": signals["sections"],
            "crawler_access": crawler_access,
            "change_set": [],   # filled below, only after the snapshot baseline is safe
            # Technical SEO & Indexability: the raw HTTP/redirect facts for this page,
            # already captured by `fetch()` — stored so `scan_to_input` can hand them to
            # `build_technical_seo_block` without a second fetch.
            "status_code": page.status_code,
            "redirect_chain": page.redirect_chain,
            "final_url": page.final_url or page.url,
        },
        requester_ip_hash=_ip_hash(ip),
        organization_id=org_id, user_id=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # A scan job is billed the MOMENT its row is durably persisted — never after any
    # later step (snapshot/diff/report-payload building below) that could still raise.
    # Recording usage here (rather than in each caller, after awaiting this function)
    # closes the exact window that could leave a real, completed, quota-consuming scan
    # with no recorded usage event — the cause of "Recent Scans" and the sidebar's
    # quota meter disagreeing on how many scans an org has used. Monitor-triggered
    # scans (monitor_id set) are deliberately excluded, matching the existing quota
    # contract (see entitlements.scans_this_month's docstring) — they don't consume
    # scan-job quota.
    if org_id and not monitor_id:
        from app.billing import entitlements
        entitlements.record_usage(db, org_id, USAGE_SCAN_JOB)
    if not monitor_id:
        _sync_monitor_latest_scan(db, org_id, normalized, row.id, signals["overall_score"])

    change_set: list = []
    if monitor_id and snapshot_payload is not None:
        # (a) Persist the new snapshot FIRST so the baseline always advances...
        try:
            from app.services.snapshot import create_snapshot
            create_snapshot(db, scan_id=row.id, monitor_id=monitor_id,
                            org_id=org_id, payload=snapshot_payload)
        except Exception:   # noqa: BLE001 — never fail a scan on snapshot persistence
            db.rollback()
        # (b) ...THEN diff (never raises: mismatch/error -> [] + WARNING), and record it.
        change_set = _safe_diff(prev_payload, snapshot_payload, monitor_id)
        if change_set:
            row.result = {**row.result, "change_set": change_set}
            db.commit()

    payload = _report_payload(row.id, report, signals, scanned_at, duration_ms, crawler_access, page=page)
    scan_cache.set(normalized, payload)
    return payload


# How often (in pages) the running bulk scan flushes its progress to the DB. Small
# enough that the client's poll sees frequent movement; large enough to avoid a
# commit per page on a big list.
_PROGRESS_FLUSH_EVERY = 5


def _page_failed(res) -> bool:
    """A page counts as failed (excluded from the average) when the fetch raised or the
    response was an error status (>=400)."""
    return isinstance(res, Exception) or getattr(res, "status_code", 200) >= 400


def _page_error(res) -> str:
    return str(res) if isinstance(res, Exception) else f"HTTP {getattr(res, 'status_code', '?')}"


def _top_issue(sig: dict) -> str | None:
    """The single most relevant issue for a page: the first issue of the
    lowest-scoring signal that has one. None if the page has no issues."""
    for s in sorted(sig["sections"], key=lambda x: x["score"]):
        if s.get("issues"):
            return s["issues"][0]
    return None


def _bulk_page_entry(url: str, sig: dict, page) -> dict:
    """The per-page record stored under result['bulk']['pages'].

    `sections_summary` intentionally drops each signal's `evidence` (see
    reports/phase4.py's `_bulk_signal_summary` docstring) to keep a bulk scan's stored
    result small. Technical SEO & Indexability, the Real Crawl Graph, and Content
    Intelligence still need a handful of per-page facts though (status/redirects +
    the metadata/robots/links/content signals' own evidence), so those are pulled out
    explicitly here — the SAME evidence `run_signals` already computed for this page,
    just not otherwise discarded, and never a second fetch or a second HTML parse.

    `body_evidence` (bounded, chunked body-content text — see
    scanner/signals/content.py::build_body_evidence) is pulled through the same way
    for the AEO Answer Simulator's retrieval; execute_bulk_scan() additionally caps
    the RUNNING TOTAL across the whole scan (MAX_TOTAL_BODY_WORDS_PER_SCAN), since
    only that layer has cross-page state — this function only knows about one page."""
    meta_ev = next((s["evidence"] for s in sig["sections"] if s["id"] == "metadata"), {}) or {}
    robots_ev = next((s["evidence"] for s in sig["sections"] if s["id"] == "robots"), {}) or {}
    links_ev = next((s["evidence"] for s in sig["sections"] if s["id"] == "links"), {}) or {}
    content_ev = next((s["evidence"] for s in sig["sections"] if s["id"] == "content"), {}) or {}
    return {
        "url": url,
        "overall_score": sig["overall_score"],
        "status_label": status_from_score(sig["overall_score"]),
        "top_issue": _top_issue(sig),
        "sections_summary": [
            {"id": s["id"], "label": s["label"], "score": s["score"], "status": s["status"],
             "issues": s.get("issues", []), "recommendations": s.get("recommendations", [])}
            for s in sig["sections"]
        ],
        "status_code": page.status_code,
        "redirect_chain": page.redirect_chain,
        "final_url": page.final_url or page.url,
        "canonical": meta_ev.get("canonical"),
        "meta_robots": meta_ev.get("robots_meta") or "",
        "x_robots_tag": meta_ev.get("x_robots_tag") or "",
        "robots_exists": robots_ev.get("exists", False),
        "robots_crawlable": robots_ev.get("crawlable", True),
        # Real Crawl Graph: this page's own outgoing internal link targets + anchor
        # text (already extracted by the `links` signal — see scanner/signals/links.py).
        "link_targets": links_ev.get("link_targets") or [],
        # Content Intelligence: title (already parsed by `metadata`), H1 text/word
        # count/content fingerprint (already parsed by `content` — see
        # scanner/signals/content.py).
        "title": meta_ev.get("title") or None,
        "description": meta_ev.get("description"),
        "h1": content_ev.get("h1_text"),
        "word_count": content_ev.get("word_count", 0),
        "content_shingles": content_ev.get("content_shingles") or [],
        "body_evidence": content_ev.get("body_evidence"),
    }


def _apply_scan_wide_body_cap(entry: dict, *, words_used: int) -> int:
    """Enforces MAX_TOTAL_BODY_WORDS_PER_SCAN across an entire bulk scan. Once the
    running total is exhausted, later pages keep every other field (title/H1/word
    count/score/etc — nothing else is affected) but their body_evidence chunks are
    dropped and flagged truncated=true, never silently omitted without a reason.
    Returns the updated running total."""
    from app.scanner.signals.content import MAX_TOTAL_BODY_WORDS_PER_SCAN

    body_ev = entry.get("body_evidence")
    if not body_ev or not body_ev.get("chunks"):
        return words_used
    if words_used >= MAX_TOTAL_BODY_WORDS_PER_SCAN:
        entry["body_evidence"] = {**body_ev, "chunks": [], "truncated": True}
        return words_used
    page_words = sum(len(c["text"].split()) for c in body_ev["chunks"])
    return words_used + page_words


async def execute_bulk_scan(db: Session, scan: Scan, *, transport=None) -> dict:
    """Run a bulk scan for an already-created PENDING Scan row (the background job
    body). The URL list lives on the Scan row (result['bulk']['urls']); this fetches +
    scores each URL concurrently with progress written as pages complete, and
    aggregates avg / best / worst.

    Contract:
    - Advances scan.status PENDING -> RUNNING -> COMPLETED and fills scan.result.
    - Per-URL failures are recorded ({url, error}) and never abort the job.
    - The whole run honours bulk_total_budget_seconds; on exhaustion it finalizes with
      the pages completed so far and sets result['bulk']['truncated'] = True.

    `transport` is a test-only fetch seam (httpx.MockTransport)."""
    started = time.perf_counter()
    urls = list(((scan.result or {}).get("bulk") or {}).get("urls") or [])
    total = len(urls)
    scan.status = SCAN_RUNNING
    scan.progress = {"total": total, "done": 0, "failed": 0, "current_url": None}
    db.commit()

    rubric = get_active_rubric(db)
    pages: list[dict] = []
    score_sum = 0.0
    scored = 0
    failed = 0
    best = worst = None
    first_report = None            # keeps one report so the row has single-page fields
    first_signals: dict | None = None
    budget_truncated = False
    processed = 0
    body_words_used = 0            # running total for MAX_TOTAL_BODY_WORDS_PER_SCAN

    async for u, res in bulk.fetch_pages(
        urls, concurrency=settings.bulk_concurrency,
        budget_seconds=settings.bulk_total_budget_seconds,
        page_timeout=settings.bulk_page_timeout_seconds, transport=transport,
    ):
        processed += 1
        if res is None:                     # URL not reached before the budget expired
            budget_truncated = True
            continue
        if _page_failed(res):
            pages.append({"url": u, "error": _page_error(res)})
            failed += 1
        else:
            sig = run_signals(res)
            entry = _bulk_page_entry(u, sig, res)
            body_words_used = _apply_scan_wide_body_cap(entry, words_used=body_words_used)
            pages.append(entry)
            sc = sig["overall_score"]
            score_sum += sc
            scored += 1
            if best is None or sc > best["score"]:
                best = {"url": u, "score": sc}
            if worst is None or sc < worst["score"]:
                worst = {"url": u, "score": sc}
            if first_report is None:
                first_report, first_signals = score(res, rubric), sig
        if processed % _PROGRESS_FLUSH_EVERY == 0:
            scan.progress = {"total": total, "done": scored, "failed": failed, "current_url": u}
            db.commit()

    avg_score = clamp(score_sum / scored) if scored else None
    scanned_at = datetime.now(timezone.utc).isoformat()
    duration_ms = int((time.perf_counter() - started) * 1000)

    bulk_block = {
        "pages": pages, "page_count": scored, "avg_score": avg_score,
        "best": best, "worst": worst, "requested": total,
        "truncated": bool(budget_truncated),
        # Real Crawl Graph: preserve the ORIGINAL user-submitted URL order (distinct
        # from `pages`, which is stored in concurrent-fetch completion order and is
        # therefore not deterministic run-to-run) — this is the crawl graph's "seed"
        # source (see reports/crawl_graph.py). Without this, the pending row's own
        # `bulk.urls` (see _create_pending_bulk_scan) would be lost the moment the scan
        # completes, since this dict wholesale-replaces scan.result["bulk"].
        "urls": urls,
    }
    # Single-page fields come from the first successful page so the row deserializes
    # like any scan; the headline overall_score is the bulk average.
    if first_report is not None:
        scan.ars = first_report.ars
        scan.rubric_version = first_report.rubric_version
        scan.rubric_version_id = rubric.version
        result = {
            "families": _families_payload(first_report),
            "top_issues": first_report.top_issues, "crawlers": first_report.crawlers,
            "overall_score": avg_score,
            "scanner_version": first_signals["scanner_version"],
            "scanned_at": scanned_at, "duration_ms": duration_ms,
            "sections": first_signals["sections"], "bulk": bulk_block,
        }
    else:
        # Every URL failed — still complete with the (all-error) bulk block.
        result = {
            "families": [], "top_issues": [], "crawlers": [],
            "overall_score": None, "scanner_version": None,
            "scanned_at": scanned_at, "duration_ms": duration_ms,
            "sections": [], "bulk": bulk_block,
        }
    scan.result = result
    scan.status = SCAN_COMPLETED
    scan.progress = {"total": total, "done": scored, "failed": failed, "current_url": None}
    db.commit()
    return result


def _scan_quota_message(plan: str) -> str:
    """The 402 message for an exhausted scan-job quota, tailored to the plan."""
    from app.billing.plans import PLAN_PRO
    pro = settings.pro_monthly_scan_jobs
    if plan == PLAN_PRO:
        return f"You've used all {pro} scan jobs this month."
    return f"Your free scan is used for this month. Upgrade to Pro for {pro} scan jobs/month."


def _owner_ids(db: Session, user: User | None) -> tuple[str | None, str | None]:
    """Resolve (organization_id, user_id) for scan attribution. Anonymous scans
    return (None, None) and remain IP-scoped."""
    if not user:
        return None, None
    member = (db.query(OrganizationMember)
              .filter(OrganizationMember.user_id == user.id).first())
    return (member.organization_id if member else None), user.id


@router.post("/scan", response_model=ScanResponse)
async def create_scan(body: ScanRequest, request: Request,
                      db: Session = Depends(get_db),
                      user: User | None = Depends(get_optional_user)):
    """Run a scan.

    Access model:
    - A signed-out visitor can run single-page scans with NO login (bounded only by the
      per-IP abuse limiter). The product "one free scan then sign up" gate is enforced
      per-browser on the client — a per-IP hard cap would wrongly block real users
      behind a shared IP / NAT. Full-site scans always require an account (401).
    - Signed-in scans are metered as "scan jobs" against the org's monthly quota
      (Free 1, Pro 15) → 402 when exhausted. Monitor-triggered scans go through
      run_scan directly and never reach here, so they never consume scan-job quota.
    """
    started = time.perf_counter()
    ip = _client_ip(request)
    from app.admin import settings_store
    if settings_store.is_maintenance(db) and not (user and user.is_platform_admin):
        raise HTTPException(status_code=503, detail=settings_store.get(db, "maintenance_message"))

    org_id, user_id = _owner_ids(db, user)

    # Modest per-IP abuse ceiling for EVERYONE (NOT a billing limit — blunts bursts).
    allowed, remaining, retry_after = rate_limiter.check(_ip_hash(ip))
    if not allowed:
        metrics.incr("rate_limited_429")
        raise HTTPException(status_code=429,
            detail="Too many scans from your network right now. Please wait a moment and try again.",
            headers={"Retry-After": str(retry_after)})

    # SSRF-validate the URL (before the quota gate, so a bad URL never burns a job).
    try:
        safe_url = validate_url(body.url)
    except UnsafeUrlError as e:
        raise HTTPException(status_code=422, detail=str(e))

    from app.billing import entitlements
    if org_id:
        # Signed-in: metered scan-job quota (Free 1 / Pro 15). The usage event is
        # recorded only AFTER the scan is accepted, so a failed fetch doesn't burn a job.
        quota = entitlements.scan_quota(db, org_id)
        if not quota["unlimited"] and quota["remaining"] <= 0:
            raise HTTPException(status_code=402,
                detail=_scan_quota_message(entitlements.current_plan(db, org_id)))

    normalized = _normalize(safe_url)

    # Cache: identical URL within TTL returns the stored result, no refetch. A signed-in
    # caller still gets their own org-attributed row (and it counts as a scan job); an
    # anonymous caller just gets the cached payload back.
    cached = scan_cache.get(normalized)
    if cached:
        metrics.incr("cache_hits")
        metrics.incr("total_scans")
        metrics.observe_scan_latency((time.perf_counter() - started) * 1000)
        if org_id:
            new_row = _persist_from_payload(db, cached, ip, org_id, user_id)
            entitlements.record_usage(db, org_id, USAGE_SCAN_JOB)
            return _response_from_payload({**cached, "scan_id": new_row.id}, remaining)
        return _response_from_payload(cached, remaining)
    metrics.incr("cache_misses")

    # Fetch + score + signals + persist (caches its own payload). run_scan() itself
    # records the scan job (for signed-in callers) the moment the row is persisted —
    # see its own docstring/comment — so there is nothing to do here on success.
    payload = await run_scan(db, safe_url, ip, org_id, user_id)
    metrics.incr("total_scans")
    metrics.observe_scan_latency((time.perf_counter() - started) * 1000)
    return _response_from_payload(payload, remaining)


def _create_pending_bulk_scan(db: Session, ip: str, urls: list[str],
                              org_id: str, user_id: str | None) -> Scan:
    """Create the PENDING Scan row for a bulk scan. The URL list is stored under
    result['bulk']['urls'] for the worker to read; ars/rubric_version are placeholders
    overwritten on completion."""
    rubric = get_active_rubric(db)
    row = Scan(
        url=urls[0], normalized_url=_normalize(urls[0]), ars=0,
        rubric_version=rubric.version, rubric_version_id=rubric.version,
        result={"bulk": {"requested": len(urls), "urls": urls}}, status=SCAN_PENDING,
        progress={"total": len(urls), "done": 0, "failed": 0, "current_url": None},
        requester_ip_hash=_ip_hash(ip), organization_id=org_id, user_id=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


async def _read_upload_capped(upload, max_bytes: int) -> bytes:
    """Read an UploadFile in bounded chunks, rejecting anything over `max_bytes` with
    HTTP 413 BEFORE the bytes ever reach a parser. We stop the moment the cap is
    exceeded, so we never buffer a whole oversized file (important for zip-container
    formats like .xlsx that decompress far larger than their upload size)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. The maximum upload size is "
                       f"{max_bytes // 1_000_000} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_bulk_urls(request: Request) -> list[str]:
    """Read the raw URL list from EITHER a JSON body ({"urls": [...]}) or a multipart
    file upload (field "file", .csv/.xlsx). Raises HTTPException(422) on a malformed
    request or unsupported file type."""
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            raise HTTPException(status_code=422,
                detail="Upload a .csv, .xlsx, .txt, .json, .jsonl, or .tsv file.")
        data = await _read_upload_capped(upload, settings.bulk_upload_max_bytes)
        try:
            return bulk.extract_urls_from_file(upload.filename, data)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    try:
        payload = BulkScanRequest(**(await request.json()))
    except Exception:
        raise HTTPException(status_code=422,
            detail="Send a JSON list of URLs or upload a .csv/.xlsx/.txt/.json/.tsv file.")
    return payload.urls


@router.post("/scan/bulk")
async def create_bulk_scan(request: Request, db: Session = Depends(get_db),
                           ctx: AuthContext = Depends(get_context)):
    """Start a BULK scan of up to bulk_max_urls user-provided URLs (JSON list or a
    CSV/XLSX upload). Validates + dedupes + SSRF-checks the URLs, enforces the plan
    quota (one scan job) and the Free one-time bulk trial, then creates a PENDING Scan
    row + enqueues a background bulk_scan job and returns 202 with the accept/skip
    summary. The client polls GET /api/scans/{id}/status."""
    from app.admin import settings_store
    if settings_store.is_maintenance(db) and not ctx.user.is_platform_admin:
        raise HTTPException(status_code=503, detail=settings_store.get(db, "maintenance_message"))
    org_id = ctx.org_id
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization for this account.")

    raw_urls = await _read_bulk_urls(request)
    # Normalize + SSRF-validate + dedupe (DNS I/O → run off the event loop).
    accepted, skipped = await run_in_threadpool(bulk.build_url_list, raw_urls, settings.bulk_max_urls)
    summary = {"accepted": len(accepted), "skipped": skipped}
    if not accepted:
        raise HTTPException(status_code=422,
            detail={"message": "No valid URLs found. Provide up to "
                    f"{settings.bulk_max_urls} website URLs, one per line or in a "
                    ".csv/.txt/.json file. HTML pages aren't parsed for links — "
                    "paste the URLs or re-save them as a list.", "summary": summary})

    from app.billing import entitlements
    is_pro = entitlements.is_pro(db, org_id)
    if not is_pro:
        # Free: the one-time bulk trial, limited to a single website.
        if not entitlements.bulk_trial_available(db, org_id):
            raise HTTPException(status_code=402,
                detail="Your one-time bulk trial is used. Upgrade to Pro to run bulk scans anytime.")
        if not bulk.is_single_registered_domain(accepted):
            raise HTTPException(status_code=422,
                detail="Free bulk trial supports one website at a time.")

    # One scan job from the monthly quota (Free 1 / Pro 15).
    quota = entitlements.scan_quota(db, org_id)
    if not quota["unlimited"] and quota["remaining"] <= 0:
        raise HTTPException(status_code=402,
            detail=_scan_quota_message(entitlements.current_plan(db, org_id)))

    ip = _client_ip(request)
    row = _create_pending_bulk_scan(db, ip, accepted, org_id, ctx.user.id)
    from app.monitoring import scheduler
    scheduler.enqueue_bulk_scan(db, row.id, org_id)
    entitlements.record_usage(db, org_id, USAGE_SCAN_JOB)
    if not is_pro:
        entitlements.mark_bulk_trial_used(db, org_id)   # consume the one-time trial
    metrics.incr("total_scans")
    return JSONResponse(status_code=202,
                        content={"scan_id": row.id, "status": SCAN_PENDING, "summary": summary})


@router.get("/scan/{scan_id}", response_model=ScanResponse)
def get_scan(scan_id: str, db: Session = Depends(get_db)):
    """Public restore of an ANONYMOUS scan (homepage flow). Org-owned scans are
    private and must be read via the authenticated /api/scans/{id} endpoint."""
    row = db.get(Scan, scan_id)
    if not row or row.organization_id is not None:
        raise HTTPException(status_code=404, detail="Scan not found.")
    return build_scan_response(row)


@router.post("/lead")
def create_lead(body: LeadRequest, db: Session = Depends(get_db)):
    if "@" not in body.email:
        raise HTTPException(status_code=422, detail="Enter a valid email.")
    capture_lead(db, body.email, body.url)
    return {"ok": True}
