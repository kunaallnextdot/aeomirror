"""URL Intelligence Engine (Phase 3): sitemap discovery, internal-link discovery,
canonicalization/dedup, robots compliance, SSRF-safe fetching, and chunked/resumable
crawling of a CrawlJob.

Design, deliberately reusing existing hardened primitives rather than duplicating them:
- Canonicalization/dedup reuses `app.scanner.bulk.normalize_url`/`normalize_dedup_key`
  (the SAME keys the bulk scanner and Technical SEO already use).
- SSRF protection reuses `app.core.ssrf.validate_url` on every URL before it is ever
  queued or fetched, and on every redirect hop (inside `app.core.fetch.fetch`).
- robots.txt compliance reuses `app.core.robots` (a public port of the matching
  algorithm already proven in `app.services.crawler_access`).
- Page fetch reuses `app.core.fetch.fetch` (bounded size, bounded redirects, retry on
  transient failures only).

Scale/resumability: discovery and crawling never hold more than one bounded BATCH of
URLs in memory. The persistent "have we seen this URL in this job" set is the
`crawl_urls` table's own (crawl_job_id, normalized_key) UNIQUE constraint — not an
in-memory/JSON set — so a 100k-URL job's dedup state lives in the database, not in a
cursor blob, and a crawl can be safely resumed after a worker restart from the DB
alone (job.cursor only carries the small sitemap-file queue).
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.fetch import FetchError, fetch
from app.core.robots import is_allowed
from app.core.ssrf import UnsafeUrlError, validate_url
from app.db.models import (
    CrawlJob, CrawlUrl, URL_CRAWLED, URL_DISCOVERED, URL_ELIGIBLE, URL_FAILED, URL_SKIPPED,
)
from app.scanner.bulk import normalize_dedup_key, normalize_url
from app.services.url_intelligence.classification import classify_page_type, classify_template
from app.services.url_intelligence.evidence import content_fingerprint, extract_evidence

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_MAX_SITEMAP_FETCHES_PER_CHUNK = 5

# The bot identity used for our OWN crawler's robots.txt compliance check — the token
# portion of settings.user_agent (e.g. "AEOMirrorBot/1.0 (...)" -> "AEOMirrorBot").
_BOT_TOKEN = settings.user_agent.split("/")[0].strip() or "AEOMirrorBot"


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _safe(url: str) -> str | None:
    try:
        return validate_url(url)
    except UnsafeUrlError:
        return None


def _try_add_url(db: Session, job: CrawlJob, url: str, *, source: str, depth: int) -> CrawlUrl | None:
    """Insert one candidate URL for this job if it's new, safe, allowed, and within
    budget. Returns the row (new or pre-existing-but-irrelevant-here) or None if
    rejected outright. Dedup is enforced by the DB's own unique constraint — a
    concurrent/duplicate insert simply fails and is treated as "already known"."""
    norm = normalize_url(url)
    if not norm:
        return None
    key = normalize_dedup_key(norm)

    if job.discovered_count >= job.max_urls:
        return None

    safe = _safe(norm)
    row = CrawlUrl(
        crawl_job_id=job.id, organization_id=job.organization_id, url=norm,
        normalized_key=key, discovery_source=source, discovery_depth=depth,
        discovered_at=datetime.utcnow(),
    )
    if not safe:
        row.status, row.skip_reason = URL_SKIPPED, "ssrf_blocked"
    elif _origin(safe) != _origin(job.normalized_seed_url) and _origin(safe) != _origin(job.seed_url):
        # Stay on the seed's own origin — Phase 3 explicitly forbids crawling
        # arbitrary external domains discovered through links.
        row.status, row.skip_reason = URL_SKIPPED, "off_site"
    else:
        row.status, row.skip_reason = URL_DISCOVERED, None
        row.url = safe

    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None   # already discovered for this job — not an error
    job.discovered_count += 1
    if row.status == URL_SKIPPED:
        job.skipped_count += 1
    db.commit()
    return row


def _eligibility_check(db: Session, job: CrawlJob, row: CrawlUrl, robots_txt: str) -> None:
    """Promote a DISCOVERED row to ELIGIBLE, or SKIPPED with a reason, based on robots
    compliance (SSRF was already checked at discovery time)."""
    path = urlparse(row.url).path or "/"
    if not is_allowed(robots_txt, _BOT_TOKEN, path):
        row.status, row.skip_reason = URL_SKIPPED, "robots_blocked"
        job.skipped_count += 1
    else:
        row.status = URL_ELIGIBLE
        job.eligible_count += 1
    db.commit()


async def _fetch_sitemap_body(url: str) -> str | None:
    safe = _safe(url)
    if not safe:
        return None
    try:
        bundle = await fetch(safe)
    except (FetchError, UnsafeUrlError):
        return None
    if bundle.status_code != 200:
        return None
    return bundle.html


async def run_discovery_chunk(db: Session, job: CrawlJob) -> bool:
    """One bounded chunk of the DISCOVERING phase. Returns True when discovery is
    exhausted (ready to move to CRAWLING)."""
    cursor = job.cursor or {}
    sitemap_queue: list[str] = cursor.get("sitemap_queue", [])
    robots_txt: str = cursor.get("robots_txt", "")
    robots_fetched: bool = cursor.get("robots_fetched", False)
    seeded_fallback: bool = cursor.get("seeded_fallback", False)

    origin = _origin(job.normalized_seed_url)

    if not robots_fetched:
        robots_bundle_url = f"{origin}/robots.txt"
        try:
            body = await _fetch_sitemap_body(robots_bundle_url)
        except Exception:   # noqa: BLE001 — discovery must never crash the job
            body = None
        robots_txt = body or ""
        robots_fetched = True
        for m in re.finditer(r"(?im)^\s*sitemap\s*:\s*(\S+)", robots_txt):
            sitemap_queue.append(m.group(1))
        if not sitemap_queue:
            sitemap_queue.append(f"{origin}/sitemap.xml")

    fetched_this_chunk = 0
    while sitemap_queue and fetched_this_chunk < _MAX_SITEMAP_FETCHES_PER_CHUNK:
        if job.discovered_count >= job.max_urls:
            break
        sm_url = sitemap_queue.pop(0)
        fetched_this_chunk += 1
        try:
            body = await _fetch_sitemap_body(sm_url)
        except Exception:   # noqa: BLE001
            body = None
        if not body:
            continue
        if "<sitemapindex" in body.lower():
            sitemap_queue.extend(_LOC_RE.findall(body)[:500])
            continue
        for loc in _LOC_RE.findall(body)[:job.max_urls]:
            if job.discovered_count >= job.max_urls:
                break
            row = _try_add_url(db, job, loc, source="sitemap", depth=0)
            if row and row.status == URL_DISCOVERED:
                _eligibility_check(db, job, row, robots_txt)

    if not sitemap_queue and job.eligible_count == 0 and not seeded_fallback:
        # No usable sitemap at all — fall back to the seed URL itself so internal-link
        # discovery (run during the crawl phase) still has somewhere to start, mirroring
        # the single-page scanner's own "no sitemap" fallback philosophy.
        seeded_fallback = True
        row = _try_add_url(db, job, job.seed_url, source="seed", depth=0)
        if row and row.status == URL_DISCOVERED:
            _eligibility_check(db, job, row, robots_txt)

    job.cursor = {"sitemap_queue": sitemap_queue, "robots_txt": robots_txt,
                  "robots_fetched": robots_fetched, "seeded_fallback": seeded_fallback}
    job.updated_at = datetime.utcnow()
    db.commit()

    # Discovery is "done enough" once the sitemap queue is drained (or the seed-only
    # fallback has run) — crawling will keep discovering more via internal links.
    return not sitemap_queue


def _internal_link_targets(html: str, base_url: str, host: str) -> list[str]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "lxml")
    targets: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        h = urlparse(href).hostname
        if href.startswith("/") or (h and h.lower() == host) or not h:
            targets.append(urljoin(base_url, href))
    return targets


async def run_crawl_chunk(db: Session, job: CrawlJob) -> bool:
    """One bounded chunk of the CRAWLING phase: fetch up to
    `url_crawl_fetch_batch_size` ELIGIBLE URLs, extract evidence, classify, and (within
    max_depth) enqueue newly discovered internal links. Returns True when there is
    nothing ELIGIBLE left (ready to move to ANALYZING)."""
    batch = (db.query(CrawlUrl)
             .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_ELIGIBLE)
             .order_by(CrawlUrl.discovery_depth.asc(), CrawlUrl.discovered_at.asc())
             .limit(settings.url_crawl_fetch_batch_size)
             .all())
    if not batch:
        return True

    robots_txt = (job.cursor or {}).get("robots_txt", "")
    host = (urlparse(job.normalized_seed_url).hostname or "").lower()

    for row in batch:
        safe = _safe(row.url)
        if not safe:
            row.status, row.skip_reason = URL_SKIPPED, "ssrf_blocked"
            job.skipped_count += 1
            db.commit()
            continue
        try:
            bundle = await fetch(safe)
        except (FetchError, UnsafeUrlError) as e:
            row.status, row.error = URL_FAILED, type(e).__name__
            job.failed_count += 1
            db.commit()
            continue

        evidence = extract_evidence(bundle, crawl_depth=row.discovery_depth)
        row.http_status = bundle.status_code
        row.canonical_url = evidence.get("canonical_url")
        row.page_type = classify_page_type(safe, evidence)
        row.template_id = classify_template(safe, row.page_type, evidence)
        row.content_fingerprint = content_fingerprint(evidence)
        row.evidence = evidence
        row.status = URL_CRAWLED
        row.crawled_at = datetime.utcnow()
        job.crawled_count += 1
        db.commit()

        if row.discovery_depth < settings.url_crawl_max_depth and job.discovered_count < job.max_urls:
            for target in _internal_link_targets(bundle.html, safe, host)[:200]:
                if job.discovered_count >= job.max_urls:
                    break
                new_row = _try_add_url(db, job, target, source="internal_link",
                                       depth=row.discovery_depth + 1)
                if new_row and new_row.status == URL_DISCOVERED:
                    _eligibility_check(db, job, new_row, robots_txt)

    job.updated_at = datetime.utcnow()
    db.commit()

    remaining = (db.query(func.count(CrawlUrl.id))
                 .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_ELIGIBLE)
                 .scalar() or 0)
    return remaining == 0
