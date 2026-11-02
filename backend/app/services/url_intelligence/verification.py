"""Fix verification via recrawl (Phase 9: "verify fixes through recrawling, compare
previous and current observations"). Evidence-based, same state vocabulary as
`app.services.verification` (VERIFY_* in app.db.models) but comparing deterministic
evidence for one URL rather than scan-signal scores — a genuinely different
comparison, so this is a new small module rather than a forced reuse of that one.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import anyio

from sqlalchemy.orm import Session

from app.core.fetch import FetchError, fetch
from app.core.ssrf import UnsafeUrlError, validate_url
from app.db.models import (
    CrawlUrl, UrlFinding, VERIFY_NOT_COMPARABLE, VERIFY_PARTIALLY_IMPROVED,
    VERIFY_REGRESSED, VERIFY_UNCHANGED, VERIFY_VERIFIED,
)
from app.services.url_intelligence.evidence import content_fingerprint, extract_evidence
from app.services.url_intelligence.issues import page_issues

_VERIFY_SAMPLE = 5   # affected pages re-checked per verification (bounded request time)


async def _recheck(url: str, code: str, page_type: str | None) -> bool | None:
    """True = issue still present, False = gone, None = page couldn't be re-checked."""
    try:
        safe = validate_url(url)
        bundle = await fetch(safe)
    except (FetchError, UnsafeUrlError):
        return None
    evidence = await anyio.to_thread.run_sync(extract_evidence, bundle)
    return code in page_issues(evidence, page_type)


async def _verify_deterministic(db: Session, finding: UrlFinding) -> str:
    """Re-check THIS issue on a sample of the pages it was found on. Verified only when
    every re-checked page no longer has it; the sample size is recorded with the result
    so a 5-page check is never presented as a check of all pages."""
    ev = dict(finding.evidence or {})
    code = ev.get("issue_code")
    urls = (ev.get("affected_urls") or [])[:_VERIFY_SAMPLE]
    results = await asyncio.gather(*(_recheck(u, code, finding.page_type) for u in urls)) if code else []
    checked = [r for r in results if r is not None]
    if not checked:
        status = VERIFY_NOT_COMPARABLE
    elif not any(checked):
        status = VERIFY_VERIFIED
    elif all(checked):
        status = VERIFY_UNCHANGED
    else:
        status = VERIFY_PARTIALLY_IMPROVED
    ev["last_verification"] = {
        "at": datetime.utcnow().isoformat(), "affected_count": ev.get("affected_count"),
        "pages": [{"url": u, "still_present": r} for u, r in zip(urls, results)],
    }
    finding.evidence = ev          # reassigned so the JSON change is persisted
    finding.verification_status = status
    finding.last_checked_at = datetime.utcnow()
    db.commit()
    return status


async def verify_finding(db: Session, finding: UrlFinding) -> str:
    """Re-fetch the finding's URL, recompute evidence, and compare the deterministic
    `missing_content_elements` before/after. Updates finding.verification_status +
    last_checked_at and, when the URL still belongs to an existing CrawlUrl row,
    refreshes that row's evidence/fingerprint too. Never raises — an unreachable URL
    is NOT_COMPARABLE, not a crash."""
    if finding.evidence_basis == "deterministic":
        return await _verify_deterministic(db, finding)
    if not finding.url:
        finding.verification_status = VERIFY_NOT_COMPARABLE
        finding.last_checked_at = datetime.utcnow()
        db.commit()
        return finding.verification_status

    try:
        safe = validate_url(finding.url)
        bundle = await fetch(safe)
    except (FetchError, UnsafeUrlError):
        finding.verification_status = VERIFY_NOT_COMPARABLE
        finding.last_checked_at = datetime.utcnow()
        db.commit()
        return finding.verification_status

    new_evidence = extract_evidence(bundle)
    new_fingerprint = content_fingerprint(new_evidence)

    old_missing = set((finding.evidence or {}).get("missing_content_elements") or [])
    new_missing = set(new_evidence.get("missing_content_elements") or [])

    if new_fingerprint == finding.content_fingerprint:
        status = VERIFY_UNCHANGED
    elif not new_missing and old_missing:
        status = VERIFY_VERIFIED
    elif len(new_missing) < len(old_missing):
        status = VERIFY_PARTIALLY_IMPROVED
    elif len(new_missing) > len(old_missing):
        status = VERIFY_REGRESSED
    else:
        status = VERIFY_UNCHANGED

    finding.verification_status = status
    finding.last_checked_at = datetime.utcnow()

    if finding.crawl_url_id:
        row = db.get(CrawlUrl, finding.crawl_url_id)
        if row:
            row.evidence = new_evidence
            row.content_fingerprint = new_fingerprint
            row.http_status = bundle.status_code
            row.crawled_at = datetime.utcnow()

    db.commit()
    return status
