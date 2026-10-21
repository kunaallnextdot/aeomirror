"""Fix verification via recrawl (Phase 9: "verify fixes through recrawling, compare
previous and current observations"). Evidence-based, same state vocabulary as
`app.services.verification` (VERIFY_* in app.db.models) but comparing deterministic
evidence for one URL rather than scan-signal scores — a genuinely different
comparison, so this is a new small module rather than a forced reuse of that one.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.fetch import FetchError, fetch
from app.core.ssrf import UnsafeUrlError, validate_url
from app.db.models import (
    CrawlUrl, UrlFinding, VERIFY_NOT_COMPARABLE, VERIFY_PARTIALLY_IMPROVED,
    VERIFY_REGRESSED, VERIFY_UNCHANGED, VERIFY_VERIFIED,
)
from app.services.url_intelligence.evidence import content_fingerprint, extract_evidence


async def verify_finding(db: Session, finding: UrlFinding) -> str:
    """Re-fetch the finding's URL, recompute evidence, and compare the deterministic
    `missing_content_elements` before/after. Updates finding.verification_status +
    last_checked_at and, when the URL still belongs to an existing CrawlUrl row,
    refreshes that row's evidence/fingerprint too. Never raises — an unreachable URL
    is NOT_COMPARABLE, not a crash."""
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
