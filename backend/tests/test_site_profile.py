"""Site-level understanding (app/services/url_intelligence/site_profile.py) — built
entirely from already-extracted per-page crawl evidence, no new LLM call. Every
inference must carry confidence/basis and degrade honestly when evidence is thin."""
from app.db.models import CrawlJob, CrawlUrl
from app.db.session import SessionLocal
from app.services.url_intelligence.site_profile import build_site_profile
from tests.authutil import auth_client


def _job(db, org_id, user_id):
    j = CrawlJob(organization_id=org_id, user_id=user_id, monitor_id="m1",
                seed_url="https://example.com/", normalized_seed_url="https://example.com/",
                max_urls=100)
    db.add(j); db.commit(); db.refresh(j)
    return j


def _url(db, job, *, url, page_type, entity_name=None, entity_logo=None):
    evidence = {"schema": {"entity_evidence": {"name": entity_name, "logo": entity_logo, "url": None, "same_as": []}}}
    row = CrawlUrl(crawl_job_id=job.id, organization_id=job.organization_id, url=url,
                   normalized_key=url, discovery_source="seed", status="crawled",
                   page_type=page_type, evidence=evidence)
    db.add(row); db.commit()
    return row


def test_no_crawled_pages_is_honest_not_fabricated():
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        profile = build_site_profile(db, job)
    finally:
        db.close()
    assert profile["available"] is False
    assert profile["reason"] == "no_crawled_pages"


def test_ecommerce_pattern_detected_from_real_page_type_distribution():
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        for i in range(4):
            _url(db, job, url=f"https://example.com/products/{i}", page_type="product")
        _url(db, job, url="https://example.com/about", page_type="other")
        profile = build_site_profile(db, job)
    finally:
        db.close()
    assert profile["available"] is True
    assert profile["pages_analyzed"] == 5
    assert "E-commerce" in profile["business_type"]["label"]
    assert profile["business_type"]["confidence"] == "medium"
    assert profile["business_type"]["basis"]   # never an unexplained label


def test_no_dominant_pattern_is_honestly_insufficient_evidence():
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        # A spread across types none of which crosses its rule's threshold.
        for i, pt in enumerate(["other", "comparison", "pricing", "other"]):
            _url(db, job, url=f"https://example.com/{i}", page_type=pt)
        profile = build_site_profile(db, job)
    finally:
        db.close()
    assert profile["business_type"]["confidence"] == "insufficient_evidence"


def test_consistent_entity_across_pages():
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        for i in range(3):
            _url(db, job, url=f"https://example.com/{i}", page_type="other",
                entity_name="Acme Inc", entity_logo="https://example.com/logo.png")
        profile = build_site_profile(db, job)
    finally:
        db.close()
    assert profile["entity"]["consistent"] is True
    assert profile["entity"]["detected_name"] == "Acme Inc"
    assert profile["entity"]["confidence"] == "low"   # only 3 pages, below the 5-page "high" bar


def test_inconsistent_entity_name_across_pages_is_flagged_not_hidden():
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        _url(db, job, url="https://example.com/a", page_type="other", entity_name="Acme Inc")
        _url(db, job, url="https://example.com/b", page_type="other", entity_name="Acme LLC")
        profile = build_site_profile(db, job)
    finally:
        db.close()
    assert profile["entity"]["consistent"] is False
    assert set(profile["entity"]["distinct_names_found"]) == {"Acme Inc", "Acme LLC"}


def test_entity_consistency_not_applicable_with_fewer_than_two_data_points():
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        _url(db, job, url="https://example.com/a", page_type="other")   # no entity data at all
        profile = build_site_profile(db, job)
    finally:
        db.close()
    assert profile["entity"]["consistent"] is None   # not a fabricated True/False
    assert profile["entity"]["confidence"] == "insufficient_evidence"


def test_site_profile_endpoint_requires_org_ownership():
    client_a, body_a = auth_client()
    client_b, body_b = auth_client()
    r_a = client_a.post("/monitors", json={"url": "https://example.com/", "frequency": "manual"})
    monitor_a = r_a.json()["id"]
    db = SessionLocal()
    try:
        job = _job(db, body_a["organization"]["id"], body_a["user"]["id"])
        job.monitor_id = monitor_a
        db.commit()
        job_id = job.id
    finally:
        db.close()
    r = client_b.get(f"/monitors/{monitor_a}/crawl-jobs/{job_id}/site-profile")
    assert r.status_code == 404
    r2 = client_a.get(f"/monitors/{monitor_a}/crawl-jobs/{job_id}/site-profile")
    assert r2.status_code == 200
