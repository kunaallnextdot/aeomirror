"""Fix verification via recrawl (app/services/url_intelligence/verification.py):
evidence-based before/after comparison, never a client-side claim. `fetch` is
monkeypatched — no real network."""
import asyncio

from app.db.models import (
    CrawlJob, CrawlUrl, UrlFinding, VERIFY_NOT_COMPARABLE, VERIFY_REGRESSED,
    VERIFY_UNCHANGED, VERIFY_VERIFIED,
)
from app.db.session import SessionLocal
from app.scanner.models import PageBundle
from app.services.url_intelligence import verification
from app.services.url_intelligence.evidence import content_fingerprint, extract_evidence
from tests.authutil import auth_client

_OLD_HTML = "<html><head></head><body><p>%s</p></body></html>" % ("word " * 200)   # missing everything
_FIXED_HTML = ("""<html><head><title>Fixed Page</title>
<meta name="description" content="A fully fixed page with everything it needs.">
<link rel="canonical" href="https://example.com/a">
<script type="application/ld+json">{"@type": "Article", "name": "Fixed Page"}</script>
</head><body><h1>Fixed Page</h1><p>%s</p></body></html>""" % ("word " * 200))
_REGRESSED_HTML = "<html><head></head><body>too short</body></html>"


def _setup(db):
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    job = CrawlJob(organization_id=org_id, user_id=user_id, monitor_id="m1",
                   seed_url="https://example.com/a", normalized_seed_url="https://example.com/a",
                   max_urls=10)
    db.add(job); db.commit(); db.refresh(job)

    old_evidence = extract_evidence(PageBundle(url="https://example.com/a", html=_OLD_HTML, status_code=200))
    row = CrawlUrl(crawl_job_id=job.id, organization_id=org_id, url="https://example.com/a",
                   normalized_key="example.com/a", discovery_source="seed", status="crawled",
                   evidence=old_evidence, content_fingerprint=content_fingerprint(old_evidence))
    db.add(row); db.commit(); db.refresh(row)

    finding = UrlFinding(crawl_job_id=job.id, crawl_url_id=row.id, organization_id=org_id,
                         url=row.url, finding_title="Missing title/H1", category="other",
                         priority="medium", confidence="medium",
                         evidence=old_evidence, content_fingerprint=row.content_fingerprint)
    db.add(finding); db.commit(); db.refresh(finding)
    return job, row, finding


def _fetch_returning(html, status=200):
    async def _f(url, **kwargs):
        return PageBundle(url=url, html=html, status_code=status)
    return _f


def test_fixed_issue_is_verified(monkeypatch):
    monkeypatch.setattr(verification, "fetch", _fetch_returning(_FIXED_HTML))
    db = SessionLocal()
    try:
        job, row, finding = _setup(db)
        status = asyncio.run(verification.verify_finding(db, finding))
        assert status == VERIFY_VERIFIED
        assert finding.verification_status == VERIFY_VERIFIED
    finally:
        db.close()


def test_new_issue_introduced_is_regressed(monkeypatch):
    monkeypatch.setattr(verification, "fetch", _fetch_returning(_REGRESSED_HTML))
    db = SessionLocal()
    try:
        job, row, finding = _setup(db)
        # Start from a finding whose evidence shows no missing elements (the fixed
        # state), then recrawl into a page that now has NEW gaps -> regressed.
        finding.evidence = {"missing_content_elements": []}
        finding.content_fingerprint = "different-fingerprint-so-it-recomputes"
        db.commit()
        status = asyncio.run(verification.verify_finding(db, finding))
        assert status == VERIFY_REGRESSED
    finally:
        db.close()


def test_unchanged_content_is_unchanged(monkeypatch):
    monkeypatch.setattr(verification, "fetch", _fetch_returning(_OLD_HTML))
    db = SessionLocal()
    try:
        job, row, finding = _setup(db)
        status = asyncio.run(verification.verify_finding(db, finding))
        assert status == VERIFY_UNCHANGED
    finally:
        db.close()


def test_unreachable_url_is_not_comparable(monkeypatch):
    from app.core.fetch import FetchError

    async def _raise(url, **kwargs):
        raise FetchError("boom")
    monkeypatch.setattr(verification, "fetch", _raise)
    db = SessionLocal()
    try:
        job, row, finding = _setup(db)
        status = asyncio.run(verification.verify_finding(db, finding))
        assert status == VERIFY_NOT_COMPARABLE
    finally:
        db.close()
