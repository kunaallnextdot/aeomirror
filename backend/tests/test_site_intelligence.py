"""Site Intelligence v2: deterministic per-page issues, the PREPARING phase (template
findings counted only on pages that have the issue), status carry-over between crawls,
cancellation safety, stalled-chunk recovery, and the overview / inventory / URL detail /
comparison read models — including org isolation and incompatible baselines.
The network is never used (fetch is monkeypatched)."""
import asyncio
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.db.models import (
    CRAWL_CANCELLED, CRAWL_COMPLETED, CRAWL_ENGINE_VERSION, CRAWL_PREPARING, FINDING_FIXED,
    FINDING_IN_PROGRESS, FINDING_OPEN, JOB_KIND_URL_CRAWL, JOB_PENDING, JOB_RUNNING,
    VERIFY_UNCHANGED, VERIFY_VERIFIED, CrawlJob, CrawlUrl, Monitor, ScheduledJob, UrlFinding,
)
from app.db.session import SessionLocal
from app.monitoring import scheduler
from app.scanner.models import PageBundle
from app.services.url_intelligence import discovery, issues, orchestrator, recommendations
from tests.authutil import auth_client


# ------------------------------------------------------------------ helpers

def _monitor(org_id, user_id, url="https://example.com/"):
    db = SessionLocal()
    try:
        m = Monitor(organization_id=org_id, user_id=user_id, url=url,
                    normalized_url=url.replace("https://", "").rstrip("/"), frequency="manual", status="active")
        db.add(m); db.commit(); db.refresh(m)
        return m.id
    finally:
        db.close()


def _job(db, org_id, monitor_id, *, phase=CRAWL_COMPLETED, created_at=None, engine=CRAWL_ENGINE_VERSION):
    j = CrawlJob(organization_id=org_id, monitor_id=monitor_id, seed_url="https://example.com/",
                 normalized_seed_url="https://example.com/", max_urls=500, phase=phase,
                 engine_version=engine, created_at=created_at or datetime.utcnow(),
                 updated_at=datetime.utcnow(), finished_at=datetime.utcnow() if phase == CRAWL_COMPLETED else None)
    db.add(j); db.commit(); db.refresh(j)
    return j


def _page(db, job, path, *, codes=(), template="t-prod", page_type="product", status="crawled",
          http=200, title=None, evidence=None):
    codes = list(codes)
    r = CrawlUrl(crawl_job_id=job.id, organization_id=job.organization_id,
                 url=f"https://example.com{path}", normalized_key=f"example.com{path}".rstrip("/"),
                 discovery_source="sitemap", status=status, http_status=http if status == "crawled" else None,
                 page_type=page_type, template_id=template, page_title=title or path,
                 issue_codes=codes if status == "crawled" else None,
                 issue_count=len(codes) if status == "crawled" else None,
                 issue_severity=issues.severity_rank(codes) if status == "crawled" else None,
                 evidence=evidence or ({"http_status": http, "missing_content_elements": codes} if status == "crawled" else None),
                 crawled_at=datetime.utcnow() if status == "crawled" else None, analyzed=status == "crawled")
    db.add(r); db.commit(); db.refresh(r)
    return r


def _ctx():
    client, body = auth_client()
    return client, body["organization"]["id"], body["user"]["id"]


# ------------------------------------------------------------------ per-page issue rules

def test_an_error_page_gets_only_its_http_issue_never_content_findings():
    ev = {"http_status": 404, "missing_content_elements": ["missing_h1", "missing_title", "thin_content"]}
    assert issues.page_issues(ev, "other") == ["http_4xx"]
    assert issues.page_issues({**ev, "http_status": 503}, "other") == ["http_5xx"]


def test_rules_are_skipped_where_they_do_not_apply_and_absent_evidence_is_not_clean():
    ev = {"http_status": 200, "missing_content_elements": ["thin_content"]}
    assert issues.page_issues(ev, "legal_utility") == []          # short by design
    assert issues.page_issues(ev, "product") == ["thin_content"]
    assert issues.page_issues(None) == [] and issues.page_issues({}) == []   # nothing checked


def test_noindex_and_missing_alt_are_potential_not_confirmed():
    ev = {"http_status": 200, "noindex": True, "robots_meta": "noindex", "images_missing_alt": 2,
          "image_count": 5, "missing_content_elements": []}
    codes = issues.page_issues(ev, "product")
    assert codes == ["noindex", "images_missing_alt"]
    assert {issues.CATALOG[c]["kind"] for c in codes} == {"potential"}
    assert issues.observed_value("images_missing_alt", ev) == "2 of 5 images have no alt attribute"


def test_images_missing_alt_ignores_decorative_images():
    from app.services.url_intelligence.evidence import extract_evidence
    html = ('<html><head><title>t</title></head><body><img src=a><img src=b alt="">'
            '<img src=c role="presentation"><img src=d alt="Chart"></body></html>')
    ev = extract_evidence(PageBundle(url="https://example.com/x", html=html, status_code=200))
    assert ev["image_count"] == 4 and ev["images_missing_alt"] == 1


# ------------------------------------------------------------------ PREPARING phase

def test_template_findings_count_only_pages_that_actually_have_the_issue():
    _, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        job = _job(db, org, mid, phase=CRAWL_PREPARING)
        _page(db, job, "/p/1", codes=["missing_meta_description"])
        _page(db, job, "/p/2", codes=["missing_meta_description", "missing_h1"])
        _page(db, job, "/p/3", codes=[])
        _page(db, job, "/gone", codes=["http_4xx"], template="t-other", page_type="other", http=404)
        assert recommendations.build_deterministic_findings(db, job) == 3
        rows = {f.issue_key: f for f in db.query(UrlFinding).filter_by(crawl_job_id=job.id)}
        meta = rows["det:missing_meta_description:t-prod"]
        assert meta.affected_url_count == 2 and meta.evidence["group_size"] == 3
        assert meta.evidence["affected_urls"] == ["https://example.com/p/1", "https://example.com/p/2"]
        assert meta.finding_title == "Missing meta description — 2 of 3 product pages"
        assert meta.evidence_basis == "deterministic" and meta.confidence == "high"
        assert rows["det:missing_h1:t-prod"].affected_url_count == 1
        assert rows["det:http_4xx:t-other"].priority == "high"
        # Idempotent: a retried chunk replaces, never duplicates.
        recommendations.build_deterministic_findings(db, job)
        assert db.query(UrlFinding).filter_by(crawl_job_id=job.id).count() == 3
    finally:
        db.close()


def test_affected_url_sample_is_bounded_but_the_count_is_exact():
    _, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        job = _job(db, org, mid, phase=CRAWL_PREPARING)
        for i in range(120):
            _page(db, job, f"/p/{i:03d}", codes=["no_structured_data"])
        recommendations.build_deterministic_findings(db, job)
        f = db.query(UrlFinding).filter_by(crawl_job_id=job.id).one()
        assert f.affected_url_count == 120
        assert len(f.evidence["affected_urls"]) == 50 and len(f.evidence["observations"]) == 5
    finally:
        db.close()


def test_user_decisions_carry_forward_to_the_next_crawl_of_the_same_site():
    _, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        old = _job(db, org, mid, created_at=datetime.utcnow() - timedelta(days=7))
        _page(db, old, "/p/1", codes=["missing_meta_description", "missing_h1", "no_structured_data"])
        recommendations.build_deterministic_findings(db, old)
        first_seen = datetime.utcnow() - timedelta(days=7)
        for f in db.query(UrlFinding).filter_by(crawl_job_id=old.id):
            f.first_detected_at = first_seen
            f.status = {"det:missing_meta_description:t-prod": FINDING_IN_PROGRESS,
                        "det:missing_h1:t-prod": FINDING_FIXED}.get(f.issue_key, FINDING_OPEN)
        db.commit()

        new = _job(db, org, mid, phase=CRAWL_PREPARING)
        _page(db, new, "/p/1", codes=["missing_meta_description", "missing_h1"])
        recommendations.build_deterministic_findings(db, new)
        assert recommendations.carry_forward_status(db, new) == 2
        rows = {f.issue_key: f for f in db.query(UrlFinding).filter_by(crawl_job_id=new.id)}
        assert rows["det:missing_meta_description:t-prod"].status == FINDING_IN_PROGRESS
        h1 = rows["det:missing_h1:t-prod"]   # marked fixed, but still there
        assert h1.status == FINDING_OPEN and h1.verification_status == VERIFY_UNCHANGED
        assert h1.first_detected_at == first_seen
    finally:
        db.close()


def test_status_never_carries_across_engine_versions_or_websites():
    _, org, user = _ctx()
    a, b = _monitor(org, user, "https://a.example/"), _monitor(org, user, "https://b.example/")
    db = SessionLocal()
    try:
        legacy = _job(db, org, a, created_at=datetime.utcnow() - timedelta(days=3), engine=None)
        other_site = _job(db, org, b, created_at=datetime.utcnow() - timedelta(days=2))
        new = _job(db, org, a, phase=CRAWL_PREPARING)
        assert recommendations.previous_comparable_job(db, new) is None
        assert legacy and other_site
    finally:
        db.close()


def test_a_full_crawl_reaches_completed_through_preparing_with_real_page_issues(monkeypatch):
    pages = {
        "https://example.com/robots.txt": "",
        "https://example.com/sitemap.xml": "<urlset><url><loc>https://example.com/a</loc></url>"
                                           "<url><loc>https://example.com/missing</loc></url></urlset>",
        "https://example.com/a": "<html><head><title>A</title></head><body><h1>A</h1>short</body></html>",
    }

    async def fake_fetch(url, **kw):
        if url in pages:
            return PageBundle(url=url, html=pages[url], status_code=200)
        return PageBundle(url=url, html="<html><body>Not found</body></html>", status_code=404)
    monkeypatch.setattr(discovery, "fetch", fake_fetch)
    monkeypatch.setattr(settings, "allow_private_hosts", True)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    _, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        job = _job(db, org, mid, phase="pending")
        seen = set()
        for _ in range(30):
            seen.add(job.phase)
            asyncio.run(orchestrator.run_chunk(db, job))
            if orchestrator.is_terminal(job.phase):
                break
        assert job.phase == CRAWL_COMPLETED and CRAWL_PREPARING in seen
        missing = db.query(CrawlUrl).filter_by(crawl_job_id=job.id, url="https://example.com/missing").one()
        assert missing.issue_codes == ["http_4xx"]          # a 404 is not judged on content
        a = db.query(CrawlUrl).filter_by(crawl_job_id=job.id, url="https://example.com/a").one()
        assert "missing_meta_description" in a.issue_codes and a.page_title == "A"
        keys = {f.issue_key.split(":")[1] for f in db.query(UrlFinding).filter_by(crawl_job_id=job.id)}
        assert {"http_4xx", "missing_meta_description"} <= keys
        # No AI configured → no AI finding, and no placeholder pretending to be one.
        assert not db.query(UrlFinding).filter(UrlFinding.crawl_job_id == job.id,
                                               UrlFinding.evidence_basis != "deterministic").count()
    finally:
        db.close()


def test_a_cancel_during_a_chunk_is_never_overwritten_by_its_phase_transition(monkeypatch):
    _, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        job = _job(db, org, mid, phase="crawling")
        _page(db, job, "/p/1", status="eligible")
        job_id = job.id

        async def fetch_then_user_cancels(url, **kw):
            other = SessionLocal()   # the user's cancel request, committed mid-chunk
            other.query(CrawlJob).filter_by(id=job_id).update({"phase": CRAWL_CANCELLED}); other.commit(); other.close()
            return PageBundle(url=url, html="<html><title>x</title></html>", status_code=200)
        monkeypatch.setattr(discovery, "fetch", fetch_then_user_cancels)
        monkeypatch.setattr(settings, "allow_private_hosts", True)
        asyncio.run(orchestrator.run_chunk(db, job))   # this chunk finishes the crawl phase
        db.refresh(job)
        assert job.phase == CRAWL_CANCELLED and orchestrator.is_terminal(job.phase)
    finally:
        db.close()


def test_a_crawl_chunk_abandoned_by_a_stopped_worker_is_requeued():
    _, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        job = _job(db, org, mid, phase="crawling")
        sj = ScheduledJob(crawl_job_id=job.id, organization_id=org, kind=JOB_KIND_URL_CRAWL,
                          status=JOB_RUNNING, attempts=1, max_attempts=3,
                          started_at=datetime.utcnow() - timedelta(minutes=scheduler.STALLED_CRAWL_CHUNK_MINUTES + 5),
                          scheduled_for=datetime.utcnow(), created_at=datetime.utcnow(), updated_at=datetime.utcnow())
        fresh = ScheduledJob(crawl_job_id=_job(db, org, mid, phase="crawling").id, organization_id=org,
                             kind=JOB_KIND_URL_CRAWL, status=JOB_RUNNING, attempts=1, max_attempts=3,
                             started_at=datetime.utcnow(), scheduled_for=datetime.utcnow(),
                             created_at=datetime.utcnow(), updated_at=datetime.utcnow())
        db.add_all([sj, fresh]); db.commit()
        assert scheduler.requeue_stalled_crawl_chunks(db) == 1
        db.refresh(sj); db.refresh(fresh)
        assert sj.status == JOB_PENDING and fresh.status == JOB_RUNNING
    finally:
        db.close()


# ------------------------------------------------------------------ overview

def test_overview_with_no_analysis_offers_start_and_shows_no_fake_results():
    client, org, user = _ctx()
    mid = _monitor(org, user)
    r = client.get(f"/monitors/{mid}/site-intelligence").json()
    assert r["can_start"] is True and r["latest_job"] is None and r["results"] is None


def test_overview_reports_real_progress_and_flags_a_stall():
    client, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        job = _job(db, org, mid, phase="crawling")
        for i in range(3):
            _page(db, job, f"/p/{i}")
        _page(db, job, "/q", status="eligible")
        _page(db, job, "/blocked", status="skipped")
        job.updated_at = datetime.utcnow() - timedelta(minutes=40); db.commit()
    finally:
        db.close()
    r = client.get(f"/monitors/{mid}/site-intelligence").json()
    st = r["status"]
    assert st["label"] == "Crawling pages" and st["active"] and st["can_cancel"]
    assert st["progress"] == {"done": 3, "total": 4, "unit": "pages fetched", "total_is_final": False,
                              "note": "The total grows as links on crawled pages are discovered."}
    assert st["counts"]["crawl_coverage_pct"] == 75.0 and st["counts"]["skipped"] == 1
    assert st["stalled"] is True and r["can_start"] is False


def test_overview_results_come_from_the_last_completed_analysis_with_honest_warnings():
    client, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        done = _job(db, org, mid, created_at=datetime.utcnow() - timedelta(hours=2))
        _page(db, done, "/p/1", codes=["missing_title"])
        _page(db, done, "/p/2", codes=[], status="failed")
        recommendations.build_deterministic_findings(db, done)
        _job(db, org, mid, phase="failed")   # a later failed run never replaces results
        done_id = done.id
    finally:
        db.close()
    r = client.get(f"/monitors/{mid}/site-intelligence").json()
    assert r["status"]["phase"] == "failed"
    res = r["results"]
    assert res["job"]["id"] == done_id and res["status"]["outcome"] == "completed_with_warnings"
    assert any("could not be fetched" in w for w in res["status"]["warnings"])
    assert any("AI analysis did not run" in w for w in res["status"]["warnings"])
    top = res["top_priorities"][0]
    assert top["finding_title"] == "Missing page title" and top["kind"] == "confirmed"
    assert top["affected_urls"] == ["https://example.com/p/1"]
    assert res["comparison"]["available"] is False and res["comparison"]["reason"] == "no_baseline"


def test_overview_and_detail_are_isolated_by_organization():
    client_a, org_a, user_a = _ctx()
    client_b, org_b, user_b = _ctx()
    mid_b = _monitor(org_b, user_b)
    db = SessionLocal()
    try:
        job = _job(db, org_b, mid_b)
        page = _page(db, job, "/p/1", codes=["missing_title"])
        jid, pid = job.id, page.id
    finally:
        db.close()
    assert client_a.get(f"/monitors/{mid_b}/site-intelligence").status_code == 404
    assert client_a.get(f"/monitors/{mid_b}/crawl-jobs/{jid}/urls/{pid}").status_code == 404
    assert client_a.get(f"/monitors/{mid_b}/site-intelligence/compare").status_code == 404


# ------------------------------------------------------------------ inventory

def _inventory_fixture(org, user):
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        job = _job(db, org, mid)
        _page(db, job, "/b", codes=["missing_meta_description"], title="Beta")
        _page(db, job, "/a", codes=["missing_title", "missing_h1"], title="Alpha")
        _page(db, job, "/c", codes=[], title="Gamma pricing")
        _page(db, job, "/d", codes=["http_5xx"], http=503)
        _page(db, job, "/e", status="failed")
        _page(db, job, "/f", status="eligible")
        return mid, job.id
    finally:
        db.close()


def test_inventory_filters_search_and_sorts_server_side_without_evidence():
    client, org, user = _ctx()
    mid, jid = _inventory_fixture(org, user)
    base = f"/monitors/{mid}/crawl-jobs/{jid}/urls"
    urls = lambda **p: [u["url"].rsplit("/", 1)[1] for u in client.get(base, params=p).json()["urls"]]   # noqa: E731
    assert urls(view="has_issues", sort="url") == ["a", "b", "d"]
    assert urls(view="high_priority", sort="-issues") == ["d", "a"]       # critical before high
    assert urls(view="failed") == ["e"] and urls(view="not_crawled") == ["f"]
    assert urls(http="5xx") == ["d"] and urls(q="pricing") == ["c"]
    page = client.get(base, params={"sort": "url", "page_size": 2, "page": 2}).json()
    assert page["total"] == 6 and [u["url"][-1] for u in page["urls"]] == ["c", "d"]
    row = client.get(base, params={"q": "/a"}).json()["urls"][0]
    assert row["page_title"] == "Alpha" and row["issue_count"] == 2 and row["priority"] == "high"
    assert "evidence" not in row
    assert client.get(base, params={"view": "nope"}).status_code == 422
    assert client.get(base, params={"page_size": 201}).status_code == 422


def test_url_detail_shows_checked_issues_with_evidence_and_why_each_finding_applies():
    client, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        job = _job(db, org, mid)
        page = _page(db, job, "/p/1", codes=["missing_title"],
                     evidence={"http_status": 200, "missing_content_elements": ["missing_title"]})
        _page(db, job, "/p/2", codes=[])
        recommendations.build_deterministic_findings(db, job)
        db.add(UrlFinding(crawl_job_id=job.id, organization_id=org, url="https://example.com/p/2",
                          template_id="t-prod", affected_url_count=2, page_type="product",
                          finding_title="Thin product specs", category="missing_information",
                          priority="medium", confidence="medium", model_used="m",
                          evidence_basis="ai_representative", issue_key="ai:t-prod:missing_information"))
        db.commit()
        jid, pid = job.id, page.id
    finally:
        db.close()
    d = client.get(f"/monitors/{mid}/crawl-jobs/{jid}/urls/{pid}").json()
    assert d["issues_evaluated"] is True
    assert d["issues"][0]["code"] == "missing_title" and d["issues"][0]["observed"] == "no <title> element"
    because = {f["finding_title"]: f["applies_because"] for f in d["findings"]}
    assert because["Missing page title — 1 of 2 product pages"] == "checked on this page"
    assert because["Thin product specs"].startswith("AI analysis of this template's representative page")
    assert d["template"]["page_count"] == 2
    assert d["traffic"]["first_party"]["status"] == "not_configured"
    assert d["traffic"]["ga4"]["status"] == "not_configured"


# ------------------------------------------------------------------ comparison

def test_comparison_refuses_missing_and_incompatible_baselines():
    client, org, user = _ctx()
    mid = _monitor(org, user)
    assert client.get(f"/monitors/{mid}/site-intelligence/compare").json()["reason"] == "no_completed_analysis"
    db = SessionLocal()
    try:
        _job(db, org, mid, created_at=datetime.utcnow() - timedelta(days=1), engine=None)
        _job(db, org, mid)
    finally:
        db.close()
    r = client.get(f"/monitors/{mid}/site-intelligence/compare").json()
    assert r["available"] is False and r["reason"] == "no_baseline"   # legacy runs are never a baseline


def test_comparison_reports_new_resolved_recurring_findings_and_page_changes():
    client, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        old = _job(db, org, mid, created_at=datetime.utcnow() - timedelta(days=1))
        _page(db, old, "/p/1", codes=["missing_title", "missing_h1"])
        _page(db, old, "/p/gone", codes=[])
        recommendations.build_deterministic_findings(db, old)
        new = _job(db, org, mid)
        _page(db, new, "/p/1", codes=["missing_title"])
        _page(db, new, "/p/new", codes=["no_structured_data"])
        recommendations.build_deterministic_findings(db, new)
    finally:
        db.close()
    r = client.get(f"/monitors/{mid}/site-intelligence/compare").json()
    assert r["available"] is True
    assert (r["findings"]["new"], r["findings"]["resolved"], r["findings"]["recurring"]) == (1, 1, 1)
    assert r["pages"]["added_sample"] == ["https://example.com/p/new"]
    assert r["pages"]["removed_sample"] == ["https://example.com/p/gone"]
    assert r["pages"]["fewer_issues_sample"] == [{"url": "https://example.com/p/1", "before": 2, "after": 1}]


# ------------------------------------------------------------------ findings + Action Center

def test_legacy_placeholder_and_insufficient_evidence_findings_are_never_actionable():
    client, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        job = _job(db, org, mid, engine=None)
        common = dict(crawl_job_id=job.id, organization_id=org, url="https://example.com/x", priority="low")
        db.add_all([
            UrlFinding(**common, finding_title="No deterministic content issues detected", category="other",
                       confidence="insufficient_evidence"),
            UrlFinding(**common, finding_title="Unclear", category="other", confidence="insufficient_evidence",
                       model_used="m", evidence_basis="ai_representative"),
            UrlFinding(**common, finding_title="Real issue", category="metadata", confidence="high",
                       evidence_basis="deterministic", evidence={"kind": "confirmed"}),
        ])
        db.commit()
        jid = job.id
    finally:
        db.close()
    titles = [f["finding_title"] for f in client.get(f"/monitors/{mid}/crawl-jobs/{jid}/findings").json()["findings"]]
    assert titles == ["Real issue"]
    insufficient = client.get(f"/monitors/{mid}/crawl-jobs/{jid}/findings",
                              params={"kind": "insufficient_evidence"}).json()["findings"]
    assert {f["finding_title"] for f in insufficient} == {"No deterministic content issues detected", "Unclear"}


def test_deterministic_verification_rechecks_the_issue_on_the_affected_pages(monkeypatch):
    from app.services.url_intelligence import verification
    client, org, user = _ctx()
    mid = _monitor(org, user)
    db = SessionLocal()
    try:
        job = _job(db, org, mid)
        _page(db, job, "/p/1", codes=["missing_title"])
        _page(db, job, "/p/2", codes=["missing_title"])
        recommendations.build_deterministic_findings(db, job)
        f = db.query(UrlFinding).filter_by(crawl_job_id=job.id).one()

        async def fixed(url, **kw):
            return PageBundle(url=url, html="<html><head><title>Now titled</title></head></html>", status_code=200)
        monkeypatch.setattr(verification, "fetch", fixed)
        monkeypatch.setattr(settings, "allow_private_hosts", True)
        assert asyncio.run(verification.verify_finding(db, f)) == VERIFY_VERIFIED
        db.refresh(f)
        lv = f.evidence["last_verification"]
        assert lv["affected_count"] == 2 and [p["still_present"] for p in lv["pages"]] == [False, False]
    finally:
        db.close()
