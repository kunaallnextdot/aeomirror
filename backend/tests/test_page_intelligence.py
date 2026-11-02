"""Page Intelligence: per-page analysis from stored evidence, page-type rules, missing
data and minimum samples, peer comparisons (successful vs weak by outcome, not
traffic), evidence-backed recommendations linked to Action Center findings (never
duplicated), the background analysis stage (priority order, reuse, failure
isolation, history), AI suggestions (grounding, labelling, caching, limits), and API
authorization. No network and no real AI call."""
import asyncio
import json
from datetime import date, datetime

import pytest

from app.config import settings
from app.core import ai
from app.db.models import (
    JOB_PENDING, CrawlUrl, PageAnalysis, PageSuggestion, PageValueRun, PageValueScore, ScheduledJob, UrlFinding,
)
from app.db.session import SessionLocal
from app.monitoring import runner
from app.services.url_intelligence import page_intelligence as pi
from app.services.url_intelligence import page_suggestions as ps
from app.services.url_intelligence import page_value as pv
from tests.test_page_opportunities import (   # noqa: F401 — shared fixtures + helpers
    _cleanup_jobs, _ctx, _ga4, _gsc, _keys, _site, _run, _drain,
)

TODAY = date(2026, 11, 2)


def _ev(**over):
    base = {"http_status": 200, "title": "Acme Widget Pro — durable steel widget for workshops",
            "title_length": 52, "meta_description": "x" * 150, "meta_description_length": 150,
            "h1_texts": ["Acme Widget Pro"], "heading_counts": {"h1": 1, "h2": 3, "h3": 0}, "word_count": 700,
            "subheadings": ["Specs", "How do I install it?", "Shipping"], "question_headings": 1,
            "dates": {"published": None, "modified": None, "source": None},
            "canonical_url": "https://shop.example.com/products/a", "noindex": False, "crawl_depth": 1,
            "schema": {"state": "present", "types": ["Product", "Offer"], "malformed": 0, "blocks": 1,
                       "entity_evidence": {"name": None}},
            "product": {"price": "49", "price_currency": "USD", "availability": "InStock"},
            "missing_content_elements": []}
    base.update(over)
    return base


def _analyze(page_type="product", ev=None, codes=(), links=5, perf=None, ctx=None, status="confirmed",
             url="https://shop.example.com/products/a"):
    return pi.analyze_page(url=url, page_type=page_type, classification_status=status,
                           evidence=ev if ev is not None else _ev(), issue_codes=list(codes), inbound_links=links,
                           perf=perf or {}, ctx=ctx or {}, today=TODAY)


def _codes(result):
    return [r["code"] for r in result["recommendations"]]


# ------------------------------------------------------------------ analysis from stored evidence

def test_a_healthy_product_page_has_nothing_invented():
    r = _analyze()
    assert r["recommendations"] == [] and r["summary"]["top_issue"] is None
    assert any(c["code"] == "relevant_schema" and c["status"] == "pass" for c in r["checks"])
    assert r["comparison"]["available"] is False


def test_product_page_issues_come_with_the_observed_evidence():
    ev = _ev(title="Widget", title_length=6, meta_description=None, meta_description_length=0,
             heading_counts={"h1": 1, "h2": 0, "h3": 0}, subheadings=[], question_headings=0,
             product={"price": None}, schema={"state": "present", "types": ["Product"], "malformed": 0, "blocks": 1,
                                              "entity_evidence": {}})
    r = _analyze(ev=ev, codes=["missing_meta_description"])
    codes = _codes(r)
    assert {"title_too_short", "missing_meta_description", "no_subheadings", "product_no_price",
            "no_question_sections"} <= set(codes)
    rec = next(x for x in r["recommendations"] if x["code"] == "title_too_short")
    assert rec["evidence"] == ['Observed on this page: "Widget" (6 characters)']
    for rec in r["recommendations"]:     # every field the spec asks for is present
        for key in ("issue", "url", "evidence", "why_it_matters", "recommended_change", "priority", "confidence",
                    "effort", "benefit_category", "dependencies", "verification"):
            assert rec[key], (rec["code"], key)
    schema_rec = next(x for x in r["recommendations"] if x["code"] == "product_no_price")
    assert '"@type": "Product"' in schema_rec["example"]["code"] and "<price>" in schema_rec["example"]["code"]


def test_schema_is_only_recommended_where_it_is_relevant_to_the_page_type():
    legal = _analyze(page_type="legal", ev=_ev(schema={"state": "present", "types": ["WebPage"], "blocks": 1,
                                                          "malformed": 0, "entity_evidence": {}}, product=None))
    assert not {"missing_relevant_schema", "no_structured_data", "product_no_price"} & set(_codes(legal))
    service = _analyze(page_type="service", ev=_ev(schema={"state": "present", "types": ["WebPage"], "blocks": 1,
                                                              "malformed": 0, "entity_evidence": {}}, product=None))
    rec = next(x for x in service["recommendations"] if x["code"] == "missing_relevant_schema")
    assert "expected Service" in rec["evidence"][0]


def test_an_error_page_gets_only_its_http_issue():
    r = _analyze(ev=_ev(http_status=404), codes=["http_4xx"])
    assert _codes(r) == ["http_4xx"]


def test_unknowable_checks_are_not_assessable_never_pass_or_fail():
    ev = _ev()
    for k in ("subheadings", "question_headings", "dates"):
        ev.pop(k)
    r = _analyze(page_type="blog", ev=ev, links=None)
    by = {c["code"]: c for c in r["checks"]}
    assert by["questions"]["status"] == "not_assessable"
    assert by["freshness"]["status"] == "not_assessable"
    assert by["internal_links"]["status"] == "not_assessable"
    assert by["readability"]["status"] == "not_assessable"
    assert by["citations"]["status"] == "not_assessable"
    assert "no_question_sections" not in _codes(r)


def test_stale_articles_and_duplicates_and_orphans_are_flagged_from_evidence():
    ev = _ev(dates={"published": "2024-01-10", "modified": None, "source": "structured data"},
             canonical_url="https://shop.example.com/blog/x",
             schema={"state": "present", "types": ["BlogPosting"], "malformed": 0, "blocks": 1, "entity_evidence": {}})
    r = _analyze(page_type="blog", ev=ev, links=0, url="https://shop.example.com/blog/x",
                 ctx={"duplicate_titles": 2, "duplicate_content": 1})
    assert {"stale_content", "orphan_page", "duplicate_title", "duplicate_content"} <= set(_codes(r))


def test_readiness_reports_observed_citations_without_predicting_them():
    r = _analyze(ctx={"ai_citations": {"count": 2, "answers": 40, "run_at": "2026-10-30T10:00:00"}})
    cit = next(c for c in r["checks"] if c["code"] == "citations")
    assert cit["observed"] == "2 of 40 tracked answers in the run of 2026-10-30"
    assert "not a prediction" in cit["detail"] and "guarantee" in r["readiness_note"]


# ------------------------------------------------------------------ performance, samples, missing data

def test_missing_analytics_produces_no_performance_claims():
    r = _analyze(perf={"score": 70, "total_sessions": None, "conversions": None, "gsc_impressions": None})
    assert r["patterns"] == [] and r["comparison"]["available"] is False


def test_performance_patterns_need_a_minimum_sample():
    few = _analyze(perf={"gsc_impressions": 150, "gsc_clicks": 0, "total_sessions": 80, "conversions": 0},
                   ctx={"site_ctr": 0.05})
    assert few["patterns"] == []
    enough = _analyze(perf={"gsc_impressions": 1200, "gsc_clicks": 6, "total_sessions": 300, "conversions": 0},
                      ctx={"site_ctr": 0.05})
    assert {p["code"] for p in enough["patterns"]} == {"low_search_ctr", "traffic_without_conversions"}
    rec = next(x for x in enough["recommendations"] if x["code"] == "low_search_ctr")
    assert "1,200 Google impressions" in rec["evidence"][0] and "0.5% CTR" in rec["evidence"][0]
    assert "200 new impressions" in rec["verification"] and rec["confidence"] == "medium"


def test_ai_visitors_not_converting_needs_ai_sessions_and_other_conversions():
    split = {"ai_sessions": 40, "ai_conversions": 0, "other_sessions": 500, "other_conversions": 12}
    assert "ai_visitors_not_converting" in _codes(_analyze(perf={"ai_split": split}))
    assert "ai_visitors_not_converting" not in _codes(_analyze(perf={"ai_split": {**split, "ai_sessions": 10}}))


def test_utility_pages_stay_low_and_valuable_commercial_pages_rise():
    legal = _analyze(page_type="legal", ev=_ev(product=None), codes=["missing_meta_description"],
                     perf={"score": 80})
    assert all(r["priority"] == "low" for r in legal["recommendations"])
    product = _analyze(codes=["missing_canonical"], perf={"score": 75})
    assert next(r for r in product["recommendations"] if r["code"] == "missing_canonical")["priority"] == "medium"
    low_value = _analyze(codes=["missing_canonical"], perf={"score": 20})
    assert next(r for r in low_value["recommendations"] if r["code"] == "missing_canonical")["priority"] == "low"


def test_type_dependent_recommendations_lose_confidence_when_classification_is_uncertain():
    ev = _ev(product={"price": None})
    assert next(r for r in _analyze(ev=ev)["recommendations"] if r["code"] == "product_no_price")["confidence"] == "high"
    assert next(r for r in _analyze(ev=ev, status="uncertain")["recommendations"]
                if r["code"] == "product_no_price")["confidence"] == "low"


# ------------------------------------------------------------------ action center links, no duplicates

def test_a_crawl_issue_links_to_its_action_center_card_and_appears_once():
    finding = {"id": "f1", "title": "Missing meta description — 4 product pages", "status": "open"}
    r = _analyze(codes=["missing_meta_description", "missing_meta_description"],
                 ev=_ev(meta_description=None, meta_description_length=0),
                 ctx={"findings": {"missing_meta_description": finding}})
    recs = [x for x in r["recommendations"] if x["code"] == "missing_meta_description"]
    assert len(recs) == 1 and recs[0]["action_center_finding"] == finding


# ------------------------------------------------------------------ peer comparison

def _service_site(org, user, n_strong=5, n_weak=5, n_mid=2):
    pages = []
    for i in range(n_strong):
        pages.append((f"/services/strong-{i}", _ev(word_count=900, schema={"state": "present", "types": ["Service"],
                      "malformed": 0, "blocks": 1, "entity_evidence": {}}, product=None), [], "crawled"))
    for i in range(n_weak):
        pages.append((f"/services/weak-{i}", _ev(word_count=180, heading_counts={"h1": 1, "h2": 0, "h3": 0},
                      subheadings=[], question_headings=0, product=None,
                      schema={"state": "present", "types": ["WebPage"], "malformed": 0, "blocks": 1,
                              "entity_evidence": {}}), [], "crawled"))
    for i in range(n_mid):
        pages.append((f"/services/mid-{i}", _ev(word_count=500, product=None, schema={"state": "present",
                      "types": ["Service"], "malformed": 0, "blocks": 1, "entity_evidence": {}}), [], "crawled"))
    mid, jid = _site(org, user, host="svc.example.com", pages=pages)
    landing = []
    for i in range(n_strong):
        landing.append((f"/services/strong-{i}", "organic_search", 50, 6, None))
    for i in range(n_weak):   # the busiest pages, but they don't convert
        landing.append((f"/services/weak-{i}", "organic_search", 400, 0, None))
    for i in range(n_mid):
        landing.append((f"/services/mid-{i}", "organic_search", 100, 3, None))
    _ga4(org, user, mid, landing=landing)
    return mid, jid


def test_peer_comparison_uses_conversion_rate_not_traffic_and_explains_its_sample():
    _, org, user = _ctx()
    mid, _ = _service_site(org, user)
    rid = _run(org, mid)
    db = SessionLocal()
    try:
        run = db.get(PageValueRun, rid)
        groups = pi.build_peer_groups(db, run)
        g = groups["service"]
        assert g["available"] and g["basis"] == "conversion_rate" and g["qualifying"] == 12
        assert g["successful"]["n"] == 4 and g["weak"]["n"] == 4
        weak = next(s for s in db.query(PageValueScore).filter(PageValueScore.run_id == rid)
                    if s.url.endswith("/weak-0"))
        cu = db.get(CrawlUrl, weak.crawl_url_id)
        feats = pi.features(cu.evidence, "service", weak.inbound_links, weak.issue_count)
        cmp_ = pi.compare("service", feats, 0.0, groups)
        assert cmp_["position"] == "weak"     # 400 sessions, the most traffic — still weak
        texts = " ".join(d["text"] for d in cmp_["differences"])
        assert "median of 900 words of text" in texts and "This page has 180" in texts
        assert "structured data for its page type" in texts
        assert "not proof of cause" in cmp_["disclaimer"]
    finally:
        db.close()


def test_no_comparison_without_enough_pages_of_the_type():
    _, org, user = _ctx()
    mid, _ = _service_site(org, user, n_strong=3, n_weak=3, n_mid=0)
    rid = _run(org, mid)
    db = SessionLocal()
    try:
        g = pi.build_peer_groups(db, db.get(PageValueRun, rid))["service"]
        assert g["available"] is False and "Only 6 page(s)" in g["reason"] and "at least 9" in g["reason"]
    finally:
        db.close()


# ------------------------------------------------------------------ background stage

def _drain_all(rid, limit=200):
    db = SessionLocal()
    try:
        for _ in range(limit):
            run = db.get(PageValueRun, rid)
            if not pv.needs_work(run):
                return run.analysis_status
            pv.run_chunk(db, run)
        raise AssertionError("did not finish")
    finally:
        db.close()


def test_the_analysis_stage_runs_after_scoring_highest_value_first(monkeypatch):
    _, org, user = _ctx()
    mid, _ = _service_site(org, user)
    monkeypatch.setattr(pi, "ANALYZE_LIMIT", 3)
    rid = _run(org, mid)                      # scoring only
    assert _drain_all(rid) == "completed"
    db = SessionLocal()
    try:
        rows = db.query(PageAnalysis).filter(PageAnalysis.run_id == rid).all()
        top3 = [s.crawl_url_id for s in db.query(PageValueScore).filter(PageValueScore.run_id == rid)
                .order_by(PageValueScore.score.desc(), PageValueScore.crawl_url_id).limit(3)]
        assert sorted(a.crawl_url_id for a in rows) == sorted(top3)
        run = db.get(PageValueRun, rid)
        assert (run.analysis_total, run.analyzed_urls) == (3, 3)
    finally:
        db.close()


def test_unchanged_pages_are_not_reanalysed_and_changes_are_tracked_in_history(monkeypatch):
    _, org, user = _ctx()
    mid, jid = _site(org, user, host="hist.example.com", pages=[
        ("/products/a", _ev(meta_description=None, meta_description_length=0), ["missing_meta_description"], "crawled")])
    first = _run(org, mid); _drain_all(first)
    second = _run(org, mid); _drain_all(second)
    db = SessionLocal()
    try:
        assert db.query(PageAnalysis).filter(PageAnalysis.monitor_id == mid).count() == 1   # reused
        # The page is fixed in a new crawl.
        from app.db.models import CrawlJob
        old = db.get(CrawlJob, jid)
        new = CrawlJob(organization_id=org, monitor_id=mid, seed_url=old.seed_url, normalized_seed_url=old.seed_url,
                       max_urls=10, phase="completed", engine_version="2", created_at=datetime.utcnow(),
                       finished_at=datetime.utcnow(), updated_at=datetime.utcnow(), discovered_count=1)
        db.add(new); db.commit()
        db.add(CrawlUrl(crawl_job_id=new.id, organization_id=org, url="https://hist.example.com/products/a",
                        normalized_key="hist.example.com/products/a", discovery_source="sitemap", status="crawled",
                        http_status=200, evidence=_ev(), issue_codes=[], issue_count=0, issue_severity=0,
                        inbound_link_count=0, crawled_at=datetime.utcnow()))
        db.commit()
    finally:
        db.close()
    third = _run(org, mid); _drain_all(third)
    db = SessionLocal()
    try:
        assert db.query(PageAnalysis).filter(PageAnalysis.monitor_id == mid).count() == 2
        h = pi.history(db, mid, "hist.example.com/products/a")
        assert h[0]["resolved_since_previous"] == ["missing_meta_description"]
    finally:
        db.close()


def test_a_failing_page_keeps_its_previous_analysis_and_does_not_stop_the_others(monkeypatch):
    _, org, user = _ctx()
    mid, _ = _site(org, user, host="fail.example.com", pages=[
        ("/products/a", _ev(), [], "crawled"), ("/products/b", _ev(), [], "crawled")])
    first = _run(org, mid); _drain_all(first)
    real = pi.analyze_page

    def flaky(**kw):
        if kw["url"].endswith("/a"):
            raise ValueError("boom")
        return real(**kw)
    monkeypatch.setattr(pi, "analyze_page", flaky)
    monkeypatch.setattr(pi, "RULES_VERSION", "test-2")      # force every page to be re-analysed
    second = _run(org, mid); _drain_all(second)
    db = SessionLocal()
    try:
        a = pi.latest_analysis(db, mid, "fail.example.com/products/a")
        b = pi.latest_analysis(db, mid, "fail.example.com/products/b")
        assert a.run_id == first and b.run_id == second
    finally:
        db.close()


def test_a_job_that_fails_for_good_marks_only_the_analysis_stage_failed(monkeypatch):
    _, org, user = _ctx()
    mid, _ = _site(org, user, host="retry.example.com", n_pages=2)
    rid = _run(org, mid)

    def broken(*a, **k):
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(pi, "run_analysis_chunk", broken)
    db = SessionLocal()
    try:
        job = db.query(ScheduledJob).filter(ScheduledJob.page_value_run_id == rid,
                                            ScheduledJob.status == JOB_PENDING).first()
        if job is None:
            from app.monitoring import scheduler
            job = scheduler.enqueue_page_value_chunk(db, rid, org)
        for _ in range(job.max_attempts):
            job.status, job.attempts = "running", job.attempts + 1
            db.commit()
            asyncio.run(runner.process_job(db, job))
            db.refresh(job)
        run = db.get(PageValueRun, rid)
        assert job.status == "failed" and run.status == "completed" and run.analysis_status == "failed"
        assert db.query(PageValueScore).filter(PageValueScore.run_id == rid).count() == 2   # scores kept
    finally:
        db.close()


# ------------------------------------------------------------------ AI suggestions

AI_OUT = {
    "title": {"suggested": "Acme Widget Pro – Steel Workshop Widget | Acme", "rationale": "Names the product."},
    "meta_description": {"suggested": "A durable steel widget for workshops. [add: warranty length].", "rationale": "r"},
    "h1": {"suggested": "Acme Widget Pro steel workshop widget", "rationale": "r"},
    "heading_outline": ["Specifications", "Installation", "Shipping"],
    "missing_sections": [{"heading": "Warranty", "why": "Not mentioned on the page."}],
    "faq": [{"question": "How do I install it?", "answer_outline": "[add: installation steps]"}],
    "answer_passage": {"suggested": "The Acme Widget Pro is a steel widget for workshops.", "rationale": "r"},
    "schema": {"type": "Product", "notes": "Add availability."},
    "internal_links": [{"url": "https://evil.example.org/x", "anchor_text": "x", "reason": "x"},
                       {"url": "https://ai.example.com/products/b", "anchor_text": "Widget B", "reason": "Related"}],
    "limitations": "Only the page start was available.",
}


def _ai_site(org, user):
    mid, _ = _site(org, user, host="ai.example.com", pages=[
        ("/products/a", _ev(), [], "crawled"), ("/products/b", _ev(title="Widget B"), [], "crawled")])
    _run(org, mid)
    db = SessionLocal()
    try:
        row = db.query(CrawlUrl).filter(CrawlUrl.url == "https://ai.example.com/products/a",
                                        CrawlUrl.organization_id == org).one()
        row.content_fingerprint = "fp-a"
        db.commit()
        return mid, row.id
    finally:
        db.close()


def test_ai_suggestions_are_grounded_labelled_validated_and_cached(monkeypatch):
    client, org, user = _ctx()
    mid, cu = _ai_site(org, user)
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    calls = []

    def fake(system, user_content, **kw):
        calls.append(user_content)
        return json.dumps(AI_OUT), {"input": 900, "output": 400}
    monkeypatch.setattr(ai, "complete_with_usage", fake)
    r = client.post(f"/monitors/{mid}/page-intelligence/{cu}/suggestions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai_generated"] is True and "nothing is changed on your website" in body["notice"]
    title = next(p for p in body["pairs"] if p["field"] == "title")
    assert title["original"] == "Acme Widget Pro — durable steel widget for workshops"   # from evidence, not the model
    assert [l["url"] for l in body["internal_links"]] == ["https://ai.example.com/products/b"]   # invented URL dropped
    assert "untrusted" in ps._SYSTEM and "Never invent" in ps._SYSTEM
    assert "Acme Widget Pro" in calls[0] and "https://ai.example.com/products/b" in calls[0]
    again = client.post(f"/monitors/{mid}/page-intelligence/{cu}/suggestions").json()
    assert again["cached"] is True and len(calls) == 1
    got = client.get(f"/monitors/{mid}/page-intelligence/{cu}").json()
    assert got["suggestions"]["pairs"][0]["suggested"] == title["suggested"]


def test_ai_suggestions_fail_clearly_when_unavailable_limited_or_invalid(monkeypatch):
    client, org, user = _ctx()
    mid, cu = _ai_site(org, user)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    assert client.post(f"/monitors/{mid}/page-intelligence/{cu}/suggestions").status_code == 503
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(ai, "complete_with_usage", lambda *a, **k: ("not json", {"input": 1, "output": 1}))
    assert client.post(f"/monitors/{mid}/page-intelligence/{cu}/suggestions").status_code == 502
    monkeypatch.setattr(ps, "ORG_DAILY_LIMIT", 1)     # the failed call above counted
    assert client.post(f"/monitors/{mid}/page-intelligence/{cu}/suggestions").status_code == 429
    db = SessionLocal()
    try:
        assert db.query(PageSuggestion).filter(PageSuggestion.monitor_id == mid).count() == 0
    finally:
        db.close()


# ------------------------------------------------------------------ API

def test_api_detail_analyse_now_and_action_center_link():
    client, org, user = _ctx()
    mid, jid = _site(org, user, host="api-pi.example.com", pages=[
        ("/products/a", _ev(meta_description=None, meta_description_length=0), ["missing_meta_description"], "crawled")])
    _run(org, mid)
    db = SessionLocal()
    try:
        cu = db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == jid).one()
        db.add(UrlFinding(crawl_job_id=jid, crawl_url_id=cu.id, organization_id=org, url=cu.url,
                          finding_title="Missing meta description — 1 page", category="metadata", priority="medium",
                          confidence="high", evidence_basis="deterministic",
                          issue_key="det:missing_meta_description:unclassified", evidence={"kind": "confirmed"}))
        db.commit()
        cu_id = cu.id
    finally:
        db.close()
    before = client.get(f"/monitors/{mid}/page-intelligence/{cu_id}").json()
    assert before["status"] in ("not_analyzed", "queued") and before["analysis"] is None
    assert before["performance"]["ai_sessions"] is None          # GA4 not linked: not measured
    r = client.post(f"/monitors/{mid}/page-intelligence/{cu_id}/analyze")
    assert r.status_code == 200 and r.json()["result"] == "analyzed" and r.json()["status"] == "current"
    rec = next(x for x in r.json()["analysis"]["recommendations"] if x["code"] == "missing_meta_description")
    assert rec["action_center_finding"]["title"] == "Missing meta description — 1 page"
    assert r.json()["verification"][0]["title"] == "Missing meta description — 1 page"
    assert client.post(f"/monitors/{mid}/page-intelligence/{cu_id}/analyze").json()["result"] == "reused"


def test_api_isolation_plan_and_roles(monkeypatch):
    from tests.test_rbac import _invite_and_accept
    owner, org, user = _ctx()
    mid, jid = _site(org, user, host="iso-pi.example.com", n_pages=1)
    _run(org, mid)
    db = SessionLocal()
    try:
        cu = db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == jid).one().id
    finally:
        db.close()
    other, _, _ = _ctx()
    for path in ("", "/analyze", "/suggestions"):
        fn = other.get if not path else other.post
        assert fn(f"/monitors/{mid}/page-intelligence/{cu}{path}").status_code == 404
    assert owner.get(f"/monitors/{mid}/page-intelligence/not-a-page").status_code == 404
    viewer, _ = _invite_and_accept(owner, "viewer", monkeypatch)
    assert viewer.get(f"/monitors/{mid}/page-intelligence/{cu}").status_code == 200
    assert viewer.post(f"/monitors/{mid}/page-intelligence/{cu}/analyze").status_code == 403
    assert viewer.post(f"/monitors/{mid}/page-intelligence/{cu}/suggestions").status_code == 403
    monkeypatch.setattr(settings, "billing_enforced", True)
    assert owner.get(f"/monitors/{mid}/page-intelligence/{cu}").status_code == 402
