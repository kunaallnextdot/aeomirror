"""Public scan endpoints. This is the free lead magnet's server side."""
from __future__ import annotations

import hashlib
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import time

from app.config import settings
from app.core.cache import rate_limiter, scan_cache
from app.core.fetch import fetch
from app.core.observability import metrics
from app.core.ssrf import UnsafeUrlError, validate_url
from app.db.models import Scan
from app.db.session import get_db
from app.scanner.engine import score
from app.scanner.rubric_provider import get_active_rubric
from app.schemas.scan import LeadRequest, ScanRequest, ScanResponse
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


def _report_payload(scan_id: str, report) -> dict:
    """JSON-serializable representation cached in Redis and reused to build the
    response. No live objects are cached (JSON only, never pickle)."""
    return {
        "scan_id": scan_id, "url": report.url, "domain": report.domain,
        "ars": report.ars, "rubric_version": report.rubric_version,
        "families": _families_payload(report),
        "top_issues": report.top_issues, "crawlers": report.crawlers,
    }


def _response_from_payload(payload: dict, remaining: int) -> ScanResponse:
    return ScanResponse(**payload, remaining_free_scans=remaining)


@router.post("/scan", response_model=ScanResponse)
async def create_scan(body: ScanRequest, request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    ip = _client_ip(request)

    # 1. rate limit anonymous scans per IP (shared across workers via Redis)
    allowed, remaining, retry_after = rate_limiter.check(_ip_hash(ip))
    if not allowed:
        metrics.incr("rate_limited_429")
        raise HTTPException(status_code=429,
            detail="Free scan limit reached. Add an email to keep scanning.",
            headers={"Retry-After": str(retry_after)})

    # 2. SSRF-validate the URL
    try:
        safe_url = validate_url(body.url)
    except UnsafeUrlError as e:
        raise HTTPException(status_code=422, detail=str(e))

    normalized = _normalize(safe_url)

    # 3. cache: identical URL within TTL returns the stored result, no refetch
    cached = scan_cache.get(normalized)
    if cached:
        metrics.incr("cache_hits")
        metrics.incr("total_scans")
        metrics.observe_scan_latency((time.perf_counter() - started) * 1000)
        return _response_from_payload(cached, remaining)
    metrics.incr("cache_misses")

    # 4. fetch + score with the active rubric (weights sourced from the DB; falls
    #    back to the code default, which is identical, so scores never change)
    try:
        page = await fetch(safe_url)
    except UnsafeUrlError as e:
        # a redirect resolved to a disallowed address (SSRF via redirect)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400,
            detail="Could not fetch that URL. Check the address and try again.")
    rubric = get_active_rubric(db)
    report = score(page, rubric)

    # 5. persist (record which rubric version scored this scan)
    row = Scan(
        url=report.url, normalized_url=normalized, ars=report.ars,
        rubric_version=report.rubric_version,
        rubric_version_id=rubric.version,
        result={
            "families": _families_payload(report),
            "top_issues": report.top_issues, "crawlers": report.crawlers,
        },
        requester_ip_hash=_ip_hash(ip),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    payload = _report_payload(row.id, report)
    scan_cache.set(normalized, payload)
    metrics.incr("total_scans")
    metrics.observe_scan_latency((time.perf_counter() - started) * 1000)
    return _response_from_payload(payload, remaining)


@router.get("/scan/{scan_id}", response_model=ScanResponse)
def get_scan(scan_id: str, db: Session = Depends(get_db)):
    row = db.get(Scan, scan_id)
    if not row:
        raise HTTPException(status_code=404, detail="Scan not found.")
    r = row.result
    return ScanResponse(
        scan_id=row.id, url=row.url, domain=_normalize(row.url), ars=row.ars,
        rubric_version=row.rubric_version, families=r["families"],
        top_issues=r["top_issues"], crawlers=r["crawlers"],
        remaining_free_scans=0,
    )


@router.post("/lead")
def create_lead(body: LeadRequest, db: Session = Depends(get_db)):
    if "@" not in body.email:
        raise HTTPException(status_code=422, detail="Enter a valid email.")
    capture_lead(db, body.email, body.url)
    return {"ok": True}
