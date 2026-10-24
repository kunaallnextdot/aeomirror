"""Connecting the URL Intelligence Engine into the main report (app/reports/service.py
::build_url_intelligence_summary), and the new confidence/url recommendation fields
(app/reports/engine.py). No code was duplicated — this is a read-only join against
existing CrawlJob/CrawlUrl/UrlFinding data."""
from app.config import settings
from app.db.models import CrawlJob, Monitor, UrlFinding
from app.db.session import SessionLocal
from app.reports.engine import build_recommendations, build_report
from app.reports.service import build_url_intelligence_summary
from tests.authutil import auth_client
from tests.test_reports import FULL, _scan, _sections


# ------------------------------- confidence / url on recommendations -------------------------------

def test_recommendation_confidence_reflects_evidence_richness():
    recs = build_recommendations(_sections(FULL), "example.com", url="https://example.com/")
    for r in recs:
        # Every fixture section carries both issues AND a structured evidence dict
        # (see tests/test_reports.py's _sections) -> confidence must be "high", not
        # a hardcoded/generic value.
        assert r["confidence"] == "high"


def test_recommendation_confidence_is_low_without_specific_issues():
    section = {"id": "robots", "label": "robots.txt", "weight": 10, "score": 40,
              "status": "warn", "issues": [], "recommendations": [], "evidence": {}}
    recs = build_recommendations([section], "example.com")
    assert recs[0]["confidence"] == "low"


def test_recommendation_url_set_for_single_page_scan():
    rep = build_report(_scan())
    assert rep["recommendations"]
    for r in rep["recommendations"]:
        assert r["url"] == "https://example.com"


def test_recommendation_url_unset_for_bulk_scan():
    scan = _scan()
    scan["bulk_pages"] = [{"url": "https://example.com/a", "sections_summary": []}]
    rep = build_report(scan)
    for r in rep["recommendations"]:
        assert r["url"] is None


# ------------------------------- url_intelligence report block -------------------------------

def _make_monitor(org_id, user_id, url="https://example.com/"):
    db = SessionLocal()
    try:
        m = Monitor(organization_id=org_id, user_id=user_id, url=url,
                   normalized_url="example.com", frequency="manual", status="active")
        db.add(m); db.commit(); db.refresh(m)
        return m.id
    finally:
        db.close()


def _make_completed_job(org_id, monitor_id, n_findings=2):
    db = SessionLocal()
    try:
        job = CrawlJob(organization_id=org_id, monitor_id=monitor_id, seed_url="https://example.com/",
                       normalized_seed_url="https://example.com/", max_urls=10, phase="completed")
        db.add(job); db.commit(); db.refresh(job)
        for i in range(n_findings):
            db.add(UrlFinding(
                crawl_job_id=job.id, organization_id=org_id, url=f"https://example.com/{i}",
                finding_title=f"Finding {i}", category="other", priority="high" if i == 0 else "low",
                confidence="high", affected_url_count=1,
            ))
        db.commit()
        return job.id
    finally:
        db.close()


def test_url_intelligence_unavailable_when_no_monitor_matches():
    client, body = auth_client()
    db = SessionLocal()
    try:
        from app.db.models import Scan
        scan = Scan(url="https://no-monitor.example/", normalized_url="no-monitor.example",
                   ars=80, rubric_version="2026.07.1", result={}, organization_id=body["organization"]["id"])
        db.add(scan); db.commit(); db.refresh(scan)
        summary = build_url_intelligence_summary(db, scan)
    finally:
        db.close()
    assert summary["available"] is False
    assert summary["reason"] == "no_monitor"


def test_url_intelligence_unavailable_when_monitor_has_no_completed_crawl():
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    monitor_id = _make_monitor(org_id, user_id)
    db = SessionLocal()
    try:
        from app.db.models import Scan
        scan = Scan(url="https://example.com/", normalized_url="example.com",
                   ars=80, rubric_version="2026.07.1", result={}, organization_id=org_id)
        db.add(scan); db.commit(); db.refresh(scan)
        summary = build_url_intelligence_summary(db, scan)
    finally:
        db.close()
    assert summary["available"] is False
    assert summary["reason"] == "no_completed_crawl"


def test_url_intelligence_available_and_sorted_by_priority_when_a_completed_crawl_exists():
    settings_enforced = settings.billing_enforced
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    monitor_id = _make_monitor(org_id, user_id)
    job_id = _make_completed_job(org_id, monitor_id, n_findings=2)
    db = SessionLocal()
    try:
        from app.db.models import Scan
        scan = Scan(url="https://example.com/", normalized_url="example.com",
                   ars=80, rubric_version="2026.07.1", result={}, organization_id=org_id)
        db.add(scan); db.commit(); db.refresh(scan)
        summary = build_url_intelligence_summary(db, scan)
    finally:
        db.close()
    assert summary["available"] is True
    assert summary["monitor_id"] == monitor_id
    assert summary["crawl_job_id"] == job_id
    assert summary["finding_count"] == 2
    assert summary["findings"][0]["priority"] == "high"   # higher priority sorts first


def test_url_intelligence_is_merged_into_the_main_report_endpoint(monkeypatch):
    import app.api.routes_scan as rs
    from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS
    from app.scanner.models import PageBundle

    async def _fake_fetch(url):
        return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                          llms_txt_present=True, sitemap_present=True, sitemap_xml="")
    monkeypatch.setattr(rs, "fetch", _fake_fetch)

    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    r = client.post("/v1/scan", json={"url": "https://example.com/"})
    assert r.status_code == 200
    scan_id = r.json()["scan_id"]

    # No monitor exists yet for this URL -> the report must honestly say so, never
    # omit the key or fabricate data.
    report = client.get(f"/reports/{scan_id}").json()
    assert report["url_intelligence"]["available"] is False

    monitor_id = _make_monitor(org_id, user_id)
    _make_completed_job(org_id, monitor_id, n_findings=1)
    report2 = client.get(f"/reports/{scan_id}").json()
    assert report2["url_intelligence"]["available"] is True
    assert report2["url_intelligence"]["finding_count"] == 1
