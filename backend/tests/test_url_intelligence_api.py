"""URL Intelligence / Expert Action Center API (app/api/routes_url_intelligence.py):
crawl-job creation + plan caps, multi-tenant isolation (404, never leaking existence
across orgs), Pro-gating of the full findings list / mutations, and the free-plan
preview. No network — crawl jobs are created but never actually ticked here (that is
covered by tests/test_url_intelligence_engine.py); these tests drive the HTTP API."""
from app.billing import entitlements
from app.config import settings
from app.db.models import CrawlJob, CrawlUrl, Monitor, UrlFinding
from app.db.session import SessionLocal
from tests.authutil import auth_client


def _make_monitor(client) -> str:
    r = client.post("/monitors", json={"url": "https://example.com/", "frequency": "manual"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _make_job(db, org_id, user_id, monitor_id, phase="completed", max_urls=100):
    j = CrawlJob(organization_id=org_id, user_id=user_id, monitor_id=monitor_id,
                seed_url="https://example.com/", normalized_seed_url="https://example.com/",
                max_urls=max_urls, phase=phase)
    db.add(j); db.commit(); db.refresh(j)
    return j


def _make_finding(db, job, *, priority="medium", confidence="high", status="open",
                  page_type="product", affected=1, url="https://example.com/a"):
    f = UrlFinding(crawl_job_id=job.id, organization_id=job.organization_id, url=url,
                   page_type=page_type, finding_title="A finding", category="other",
                   priority=priority, confidence=confidence, status=status,
                   affected_url_count=affected)
    db.add(f); db.commit(); db.refresh(f)
    return f


# ------------------------------- create -------------------------------

def test_create_crawl_job_caps_max_urls_to_free_plan_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, body = auth_client()
    monitor_id = _make_monitor(client)
    r = client.post(f"/monitors/{monitor_id}/crawl-jobs", json={"max_urls": 100000})
    assert r.status_code == 200, r.text
    assert r.json()["max_urls"] == settings.url_crawl_free_max_urls


def test_create_crawl_job_rejects_a_second_active_job_for_the_same_monitor():
    client, body = auth_client()
    monitor_id = _make_monitor(client)
    r1 = client.post(f"/monitors/{monitor_id}/crawl-jobs", json={})
    assert r1.status_code == 200
    r2 = client.post(f"/monitors/{monitor_id}/crawl-jobs", json={})
    assert r2.status_code == 409


def test_create_crawl_job_on_another_orgs_monitor_is_404():
    client_a, _ = auth_client()
    client_b, _ = auth_client()
    monitor_b = _make_monitor(client_b)
    r = client_a.post(f"/monitors/{monitor_b}/crawl-jobs", json={})
    assert r.status_code == 404


# ------------------------------- get/list/cancel -------------------------------

def test_get_and_list_crawl_jobs_round_trip():
    client, body = auth_client()
    monitor_id = _make_monitor(client)
    created = client.post(f"/monitors/{monitor_id}/crawl-jobs", json={}).json()
    r = client.get(f"/monitors/{monitor_id}/crawl-jobs/{created['id']}")
    assert r.status_code == 200
    assert r.json()["phase"] == "pending"
    listed = client.get(f"/monitors/{monitor_id}/crawl-jobs").json()
    assert any(j["id"] == created["id"] for j in listed["jobs"])


def test_crawl_job_detail_is_404_across_orgs():
    client_a, body_a = auth_client()
    client_b, body_b = auth_client()
    monitor_a = _make_monitor(client_a)
    created = client_a.post(f"/monitors/{monitor_a}/crawl-jobs", json={}).json()
    # org B doesn't even own monitor_a, so this must 404, not leak the job.
    r = client_b.get(f"/monitors/{monitor_a}/crawl-jobs/{created['id']}")
    assert r.status_code == 404


def test_cancel_crawl_job():
    client, body = auth_client()
    monitor_id = _make_monitor(client)
    created = client.post(f"/monitors/{monitor_id}/crawl-jobs", json={}).json()
    r = client.post(f"/monitors/{monitor_id}/crawl-jobs/{created['id']}/cancel")
    assert r.status_code == 200
    assert r.json()["phase"] == "cancelled"


# ------------------------------- findings: Pro gating + preview -------------------------------

def test_findings_free_plan_gets_a_short_preview_with_locked_count(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, body = auth_client()
    monitor_id = _make_monitor(client)
    db = SessionLocal()
    try:
        job = _make_job(db, body["organization"]["id"], body["user"]["id"], monitor_id)
        job_id = job.id
        for i in range(8):
            _make_finding(db, job, url=f"https://example.com/{i}")
    finally:
        db.close()
    r = client.get(f"/monitors/{monitor_id}/crawl-jobs/{job_id}/findings")
    assert r.status_code == 200
    data = r.json()
    assert data["is_pro"] is False
    assert len(data["findings"]) <= 5
    assert data["locked_count"] == data["total"] - len(data["findings"])


def test_findings_pro_plan_gets_full_paginated_list():
    client, body = auth_client()   # billing_enforced defaults False in tests -> full access
    monitor_id = _make_monitor(client)
    db = SessionLocal()
    try:
        job = _make_job(db, body["organization"]["id"], body["user"]["id"], monitor_id)
        job_id = job.id
        for i in range(8):
            _make_finding(db, job, url=f"https://example.com/{i}")
    finally:
        db.close()
    r = client.get(f"/monitors/{monitor_id}/crawl-jobs/{job_id}/findings")
    data = r.json()
    assert data["is_pro"] is True
    assert data["total"] == 8
    assert data["locked_count"] == 0


def test_findings_are_sorted_by_explainable_priority_score():
    client, body = auth_client()
    monitor_id = _make_monitor(client)
    db = SessionLocal()
    try:
        job = _make_job(db, body["organization"]["id"], body["user"]["id"], monitor_id)
        job_id = job.id
        _make_finding(db, job, url="https://example.com/low", priority="low", confidence="low",
                     page_type="legal_utility")
        _make_finding(db, job, url="https://example.com/high", priority="critical", confidence="high",
                     page_type="product")
    finally:
        db.close()
    data = client.get(f"/monitors/{monitor_id}/crawl-jobs/{job_id}/findings").json()
    urls = [f["url"] for f in data["findings"]]
    assert urls.index("https://example.com/high") < urls.index("https://example.com/low")
    assert all("priority_reason" in f and f["priority_reason"] for f in data["findings"])


# ------------------------------- status updates: Pro-gated mutation -------------------------------

def test_update_finding_status_requires_pro(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, body = auth_client()
    monitor_id = _make_monitor(client)
    db = SessionLocal()
    try:
        job = _make_job(db, body["organization"]["id"], body["user"]["id"], monitor_id)
        finding = _make_finding(db, job)
        job_id, finding_id = job.id, finding.id
    finally:
        db.close()
    r = client.patch(f"/monitors/{monitor_id}/crawl-jobs/{job_id}/findings/{finding_id}",
                     json={"status": "fixed"})
    assert r.status_code == 402


def test_update_finding_status_on_pro_plan():
    client, body = auth_client()
    monitor_id = _make_monitor(client)
    db = SessionLocal()
    try:
        job = _make_job(db, body["organization"]["id"], body["user"]["id"], monitor_id)
        finding = _make_finding(db, job)
        job_id, finding_id = job.id, finding.id
    finally:
        db.close()
    r = client.patch(f"/monitors/{monitor_id}/crawl-jobs/{job_id}/findings/{finding_id}",
                     json={"status": "fixed"})
    assert r.status_code == 200
    assert r.json()["status"] == "fixed"


def test_cannot_update_a_finding_belonging_to_another_orgs_job():
    client_a, body_a = auth_client()
    client_b, body_b = auth_client()
    monitor_a = _make_monitor(client_a)
    db = SessionLocal()
    try:
        job_a = _make_job(db, body_a["organization"]["id"], body_a["user"]["id"], monitor_a)
        finding_a = _make_finding(db, job_a)
        job_a_id, finding_a_id = job_a.id, finding_a.id
    finally:
        db.close()
    monitor_b = _make_monitor(client_b)
    r = client_b.patch(f"/monitors/{monitor_b}/crawl-jobs/{job_a_id}/findings/{finding_a_id}",
                       json={"status": "fixed"})
    assert r.status_code == 404


def test_bulk_status_update():
    client, body = auth_client()
    monitor_id = _make_monitor(client)
    db = SessionLocal()
    try:
        job = _make_job(db, body["organization"]["id"], body["user"]["id"], monitor_id)
        f1 = _make_finding(db, job, url="https://example.com/1")
        f2 = _make_finding(db, job, url="https://example.com/2")
        job_id, f1_id, f2_id = job.id, f1.id, f2.id
    finally:
        db.close()
    r = client.post(f"/monitors/{monitor_id}/crawl-jobs/{job_id}/findings/bulk-status",
                    json={"finding_ids": [f1_id, f2_id], "status": "in_progress"})
    assert r.status_code == 200
    assert r.json()["updated"] == 2
