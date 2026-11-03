"""Business-Value URL Intelligence (Page Opportunities) and read-only Search Console:
business page classification, transparent scoring, missing-data handling, utility-page
prioritisation, GA4 + Search Console + first-party joins, chunked/resumable runs over
large URL sets, cancellation, API authorization and isolation, and the Search Console
OAuth/sync safety rules. Google is never called — fetchers and httpx are stubbed."""
import asyncio
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from app.config import settings
from app.core import token_crypto
from app.db.models import (
    CRAWL_COMPLETED, GA4_STATUS_ACTIVE, GA4_STATUS_ERROR, GA4_STATUS_PENDING_PROPERTY, JOB_KIND_PAGE_VALUE,
    JOB_PENDING, PV_CANCELLED, PV_COMPLETED, PV_RUNNING, AnalyticsConversion, AnalyticsSession, CrawlJob,
    CrawlUrl, Ga4LandingPageSnapshot, GoogleAnalyticsConnection, GscPageSnapshot, Monitor, OAuthState,
    PageValueRun, PageValueScore, ScheduledJob, SearchConsoleConnection, UrlFinding,
)
from app.db.session import SessionLocal
from app.monitoring import runner, scheduler
from app.services.integrations import google_analytics as ga4
from app.services.integrations import search_console as gsc
from app.services.url_intelligence import discovery, issues
from app.services.url_intelligence import page_value as pv
from app.services.url_intelligence.classification import classify_business_page
from tests.authutil import auth_client

FAKE_KEY = "zxhjMoSGw8Hu9dZ0qVgN6W7AUHHho9mz5igY2ZvQ4oE="
K = lambda d: d.strftime("%Y%m%d")   # noqa: E731


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)
    monkeypatch.setattr(settings, "google_oauth_client_id", "cid")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "cs")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "https://api.example.com/integrations/google/callback")


@pytest.fixture(autouse=True)
def _cleanup_jobs():
    """The test DB is shared across files: never leave a runnable job behind."""
    yield
    db = SessionLocal()
    try:
        db.query(ScheduledJob).filter(ScheduledJob.kind.in_((JOB_KIND_PAGE_VALUE, "gsc_sync")),
                                      ScheduledJob.status == JOB_PENDING).delete(synchronize_session=False)
        db.query(SearchConsoleConnection).update({SearchConsoleConnection.next_sync_at: None})
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ helpers

def _ctx():
    client, body = auth_client()
    return client, body["organization"]["id"], body["user"]["id"]


def _site(org, user, host="shop.example.com", n_pages=None, pages=None):
    """A website with a completed crawl. `pages`: [(path, evidence|None, codes, status)]."""
    db = SessionLocal()
    try:
        m = Monitor(organization_id=org, user_id=user, url=f"https://{host}/", normalized_url=host,
                    frequency="manual", status="active")
        db.add(m); db.commit(); db.refresh(m)
        j = CrawlJob(organization_id=org, monitor_id=m.id, seed_url=m.url, normalized_seed_url=m.url,
                     max_urls=100000, phase=CRAWL_COMPLETED, engine_version="2", created_at=datetime.utcnow(),
                     finished_at=datetime.utcnow(), updated_at=datetime.utcnow(), discovered_count=0)
        db.add(j); db.commit(); db.refresh(j)
        rows = pages or [(f"/products/item-{i}", {"schema": {"types": ["Product"]}, "title": f"Item {i}"}, [], "crawled")
                         for i in range(n_pages or 0)]
        for path, ev, codes, status in rows:
            db.add(CrawlUrl(crawl_job_id=j.id, organization_id=org, url=f"https://{host}{path}",
                            normalized_key=f"{host}{path}".rstrip("/"), discovery_source="sitemap", status=status,
                            skip_reason="off_site" if status == "skipped" else None,
                            http_status=200 if status == "crawled" else None, page_title=(ev or {}).get("title"),
                            evidence=ev if status == "crawled" else None,
                            issue_codes=list(codes) if status == "crawled" else None,
                            issue_count=len(codes) if status == "crawled" else None,
                            issue_severity=issues.severity_rank(list(codes)) if status == "crawled" else None,
                            inbound_link_count=0, crawled_at=datetime.utcnow() if status == "crawled" else None))
        j.discovered_count = len(rows)
        db.commit()
        return m.id, j.id
    finally:
        db.close()


def _ga4(org, user, monitor_id, landing=(), key_events=("purchase",), currency="USD", linked=True):
    """landing: [(path, traffic_class, sessions, key_events, revenue)] on today's date."""
    db = SessionLocal()
    try:
        c = GoogleAnalyticsConnection(
            organization_id=org, connected_by_user_id=user, status=GA4_STATUS_ACTIVE,
            encrypted_refresh_token=token_crypto.encrypt_token("rt"), ga4_property_id="123",
            ga4_property_name="Acme", last_synced_at=datetime.utcnow(), landing_pages_synced_at=datetime.utcnow(),
            linked_monitor_id=monitor_id if linked else None, key_event_names=list(key_events),
            key_events_configured=bool(key_events), currency_code=currency)
        db.add(c); db.commit()
        for path, cls, sessions, kev, rev in landing:
            db.add(Ga4LandingPageSnapshot(organization_id=org, connection_id=c.id, date=K(date.today()),
                                          landing_page=path, traffic_class=cls, sessions=sessions,
                                          key_events=kev, revenue=rev))
        db.commit()
        return c.id
    finally:
        db.close()


def _gsc(org, user, site_url="sc-domain:example.com", rows=(), truncated=False, synced=True):
    db = SessionLocal()
    try:
        c = SearchConsoleConnection(
            organization_id=org, connected_by_user_id=user, status=GA4_STATUS_ACTIVE, site_url=site_url,
            encrypted_refresh_token=token_crypto.encrypt_token("rt-gsc"), truncated=truncated,
            last_synced_at=datetime.utcnow() if synced else None, window_start="2026-10-03", window_end="2026-10-30")
        db.add(c); db.commit()
        from app.services.analytics.aggregation import page_key
        for page, clicks, impressions, pos in rows:
            db.add(GscPageSnapshot(organization_id=org, connection_id=c.id, page=page, page_key=page_key(page)[0],
                                   clicks=clicks, impressions=impressions, position=pos))
        db.commit()
        return c.id
    finally:
        db.close()


def _drain(run_id, chunk_size=pv.CHUNK_SIZE, limit=500):
    """Process a run to a terminal state, one chunk per call (as the worker would)."""
    db = SessionLocal()
    try:
        n = 0
        while n < limit:
            run = db.get(PageValueRun, run_id)
            if pv.is_terminal(run.status):
                return n
            pv.run_chunk(db, run, chunk_size=chunk_size)
            n += 1
        raise AssertionError("run did not finish")
    finally:
        db.close()


def _run(org, monitor_id, **kw):
    db = SessionLocal()
    try:
        run = pv.request_run(db, org_id=org, monitor_id=monitor_id, trigger="manual")
        rid = run.id
    finally:
        db.close()
    _drain(rid, **kw)
    return rid


def _scores(run_id):
    db = SessionLocal()
    try:
        return {r.url.split(".com", 1)[1]: r for r in db.query(PageValueScore).filter(PageValueScore.run_id == run_id)}
    finally:
        db.close()


# ------------------------------------------------------------------ classification

@pytest.mark.parametrize("url,evidence,expected", [
    ("https://x.com/", {}, "homepage"),
    ("https://x.com/products/red-shoe", {"schema": {"types": ["Product"]}, "product": {"price": "20"}}, "product"),
    ("https://x.com/collections/shoes", {"schema": {"types": ["CollectionPage"]}}, "category"),
    ("https://x.com/services/seo-audit", {"schema": {"types": ["Service"]}}, "service"),
    ("https://x.com/pricing", {"title": "Plans and pricing"}, "landing"),
    ("https://x.com/rooms/deluxe-suite", {"schema": {"types": ["HotelRoom"]}}, "hotel_property"),
    ("https://x.com/book/table", {"title": "Book now"}, "booking"),
    ("https://x.com/blog/how-to-tie", {"schema": {"types": ["BlogPosting"]}}, "blog"),
    ("https://x.com/cart", {}, "utility"),
    ("https://x.com/privacy-policy", {"title": "Privacy policy"}, "legal"),
    ("https://x.com/account/login", {"title": "Sign in"}, "login_account"),
    ("https://x.com/about-our-story", {"title": "Our story"}, "unknown"),
])
def test_business_page_types(url, evidence, expected):
    assert classify_business_page(url, evidence)["page_type"] == expected


def test_classification_carries_confidence_evidence_and_status():
    strong = classify_business_page("https://x.com/products/a", {"schema": {"types": ["Product"]},
                                                                 "product": {"price": "9", "price_currency": "EUR"}})
    assert strong["status"] == "confirmed" and strong["confidence"] >= 0.7
    assert any("Product" in e for e in strong["evidence"]) and any("9 EUR" in e for e in strong["evidence"])
    one_signal = classify_business_page("https://x.com/services/x", {})
    assert one_signal["status"] == "likely" and one_signal["confidence"] < 0.7


def test_uncertain_classification_is_never_confirmed():
    # URL says product, schema says article: conflicting evidence lowers confidence.
    c = classify_business_page("https://x.com/products/guide", {"schema": {"types": ["Article"]}})
    assert c["status"] != "confirmed"
    assert any(e.startswith("Conflicting signal") for e in c["evidence"])
    assert classify_business_page("https://x.com/x-y-z", None)["status"] == "uncertain"
    assert "hasn't been crawled" in classify_business_page("https://x.com/x-y-z", None)["evidence"][0]


def test_a_legal_page_mentioning_sign_in_is_still_legal():
    assert classify_business_page("https://x.com/terms", {"title": "Terms — sign in to accept"})["page_type"] == "legal"


# ------------------------------------------------------------------ scoring

def _cls(t, status="confirmed"):
    return {"page_type": t, "status": status, "confidence": 0.9, "evidence": []}


def test_missing_sources_are_left_out_of_the_score_never_counted_as_zero():
    no_data = pv.score_page(_cls("product"), {"inbound_links": None}, {"maxima": {}})
    comps = {c["key"]: c for c in no_data["components"]}
    assert comps["ai_sessions"]["available"] is False and comps["ai_sessions"]["value"] is None
    assert comps["ai_sessions"]["points"] is None and "Not measured" in comps["ai_sessions"]["note"]
    assert no_data["measured_signals"] == 0
    assert no_data["score"] == 100.0      # only purpose was available, and it was maxed
    # The same page WITH GA4 available but no traffic: a measured zero does lower it.
    measured = pv.score_page(_cls("product"), {"ai_sessions": 0, "organic_sessions": 0, "inbound_links": None},
                             {"ga4_available": True, "maxima": {"ai_sessions": 50, "organic_sessions": 80}})
    assert measured["score"] < no_data["score"] and measured["measured_signals"] == 2


def test_score_separates_measured_from_inferred_and_explains_every_component():
    r = pv.score_page(_cls("service"), {"conversions": 4, "ai_sessions": 10, "organic_sessions": 0,
                                        "gsc_clicks": 5, "gsc_impressions": 400, "inbound_links": 3},
                      {"conversion_source": "ga4_key_events", "ga4_available": True, "gsc_available": True,
                       "maxima": {"conversions": 4, "ai_sessions": 10, "organic_sessions": 5, "gsc_clicks": 50,
                                  "gsc_impressions": 1000, "inbound_links": 30}})
    kinds = {c["key"]: c["kind"] for c in r["components"]}
    assert kinds["purpose"] == "inferred" and kinds["conversions"] == "measured"
    assert all(c["note"] for c in r["components"])
    assert next(c for c in r["components"] if c["key"] == "conversions")["points"] == 25.0   # site's top page
    assert 0 < r["score"] <= 100


def test_commercial_pages_outscore_utility_pages_on_equal_evidence():
    src = {"maxima": {}}
    assert pv.score_page(_cls("product"), {}, src)["score"] > pv.score_page(_cls("legal"), {}, src)["score"]
    assert pv.score_page(_cls("booking"), {}, src)["score"] > pv.score_page(_cls("login_account"), {}, src)["score"]


def test_utility_pages_stay_low_priority_unless_outcomes_are_measured():
    cls = _cls("legal")
    assert pv.priority_for(80, cls, ["missing_title"], True, {})[0] == "low"
    prio, reason = pv.priority_for(80, cls, ["missing_title"], True, {"conversions": 3})
    assert prio == "high"
    assert pv.priority_for(80, _cls("product"), ["missing_title"], True, {})[0] == "high"


def test_priority_states_nothing_to_fix_and_not_evaluated_honestly():
    assert pv.priority_for(90, _cls("product"), [], True, {})[0] == "none"
    assert pv.priority_for(90, _cls("product"), [], False, {})[0] == "not_evaluated"


def test_low_search_ctr_signal_needs_volume_and_a_site_baseline():
    src = {"site_ctr": 0.05}
    assert pv.data_issues(_cls("product"), {"gsc_impressions": 1000, "gsc_clicks": 5}, src) == ["low_search_ctr"]
    assert pv.data_issues(_cls("product"), {"gsc_impressions": 100, "gsc_clicks": 0}, src) == []   # too few
    assert pv.data_issues(_cls("product"), {"gsc_impressions": 1000, "gsc_clicks": 5}, {"site_ctr": None}) == []


# ------------------------------------------------------------------ runs: data integration

def test_run_joins_ga4_search_console_and_crawl_data_per_url():
    _, org, user = _ctx()
    mid, _ = _site(org, user, host="shop.example.com", pages=[
        ("/products/a", {"schema": {"types": ["Product"]}, "title": "A"}, ["missing_meta_description"], "crawled"),
        ("/privacy", {"title": "Privacy policy"}, ["missing_meta_description"], "crawled"),
        ("/blog/news", {"schema": {"types": ["BlogPosting"]}}, [], "crawled"),
        ("/elsewhere", None, [], "skipped"),     # off-site: not this website's page
    ])
    _ga4(org, user, mid, landing=[("/products/a", "chatgpt", 12, 3, 150.0), ("/products/a/", "organic_search", 40, 1, 50.0),
                                  ("/privacy", "organic_search", 5, 0, None)])
    _gsc(org, user, rows=[("https://shop.example.com/products/a", 30, 900, 4.2),
                          ("http://shop.example.com/products/a/", 10, 100, 8.0)])
    rid = _run(org, mid)
    s = _scores(rid)
    assert set(s) == {"/products/a", "/privacy", "/blog/news"}
    a = s["/products/a"]
    assert (a.ai_sessions, a.organic_sessions, a.total_sessions) == (12, 40, 52)   # trailing slash merged
    assert (a.conversions, a.conversion_source, a.revenue, a.revenue_currency) == (4, "ga4_key_events", 200.0, "USD")
    assert (a.gsc_clicks, a.gsc_impressions) == (40, 1000)                          # http/https merged by page key
    assert a.gsc_position == pytest.approx((4.2 * 900 + 8.0 * 100) / 1000)
    assert a.business_page_type == "product" and a.is_commercial
    blog = s["/blog/news"]
    assert (blog.ai_sessions, blog.gsc_clicks) == (0, 0)        # source available, page absent → measured zero
    assert a.score > s["/privacy"].score
    assert a.priority == "high" and s["/privacy"].priority == "low" and blog.priority == "none"


def test_sources_that_do_not_apply_to_this_website_stay_not_measured():
    _, org, user = _ctx()
    mid, _ = _site(org, user, host="shop.example.com", n_pages=2)
    _ga4(org, user, mid, landing=[("/products/item-0", "chatgpt", 9, 0, None)], linked=False)
    _gsc(org, user, site_url="sc-domain:another-site.org")
    rid = _run(org, mid)
    row = _scores(rid)["/products/item-0"]
    assert row.ai_sessions is None and row.gsc_clicks is None and row.conversions is None and row.revenue is None
    db = SessionLocal()
    try:
        src = pv.public_sources(db.get(PageValueRun, rid).inputs)
    finally:
        db.close()
    assert src["ga4"]["reason"] == "The GA4 property isn't linked to this website."
    assert src["gsc"]["reason"] == "The Search Console property doesn't cover this website."
    assert src["conversion_source"] is None


def test_a_truncated_search_console_sync_never_turns_an_absent_page_into_zero():
    _, org, user = _ctx()
    mid, _ = _site(org, user, host="shop.example.com", n_pages=2)
    _gsc(org, user, rows=[("https://shop.example.com/products/item-0", 3, 50, 2.0)], truncated=True)
    s = _scores(_run(org, mid))
    assert s["/products/item-0"].gsc_clicks == 3
    assert s["/products/item-1"].gsc_clicks is None


def test_server_verified_conversions_take_precedence_over_ga4_and_are_never_added():
    _, org, user = _ctx()
    mid, _ = _site(org, user, host="shop.example.com", n_pages=1)
    _ga4(org, user, mid, landing=[("/products/item-0", "organic_search", 20, 7, 70.0)])
    db = SessionLocal()
    try:
        sess = AnalyticsSession(organization_id=org, monitor_id=mid, session_id="s1",
                                landing_url="https://shop.example.com/products/item-0?utm_source=x", channel="ai")
        db.add(sess); db.commit()
        for i, verified in enumerate((True, True, False)):
            db.add(AnalyticsConversion(organization_id=org, monitor_id=mid, analytics_session_id=sess.id,
                                       event_type="purchase", external_id=f"o{i}", value_cents=2500,
                                       currency="EUR", verified=verified))
        db.commit()
    finally:
        db.close()
    row = _scores(_run(org, mid))["/products/item-0"]
    assert (row.conversions, row.conversion_source) == (2, "first_party_verified")   # unverified excluded
    assert (row.revenue, row.revenue_currency) == (50.0, "EUR")


def test_no_conversion_tracking_means_not_measured_not_zero():
    _, org, user = _ctx()
    mid, _ = _site(org, user, host="shop.example.com", n_pages=1)
    _ga4(org, user, mid, landing=[("/products/item-0", "chatgpt", 20, 0, None)], key_events=())
    row = _scores(_run(org, mid))["/products/item-0"]
    assert row.ai_sessions == 20 and row.conversions is None and row.revenue is None


# ------------------------------------------------------------------ runs: scale, resumability, cancellation

def test_large_url_sets_are_processed_in_bounded_resumable_chunks():
    _, org, user = _ctx()
    mid, _ = _site(org, user, host="big.example.com", n_pages=2500)
    db = SessionLocal()
    try:
        rid = pv.request_run(db, org_id=org, monitor_id=mid, trigger="manual").id
    finally:
        db.close()
    chunks = _drain(rid, chunk_size=1000)
    assert chunks == 1 + 3          # prepare + ceil(2500/1000)
    db = SessionLocal()
    try:
        run = db.get(PageValueRun, rid)
        assert (run.status, run.total_urls, run.processed_urls) == (PV_COMPLETED, 2500, 2500)
        assert db.query(PageValueScore).filter(PageValueScore.run_id == rid).count() == 2500
    finally:
        db.close()


def test_a_chunk_rerun_after_a_crash_rewrites_its_rows_without_duplicates():
    _, org, user = _ctx()
    mid, _ = _site(org, user, host="crash.example.com", n_pages=5)
    db = SessionLocal()
    try:
        run = pv.request_run(db, org_id=org, monitor_id=mid, trigger="manual")
        pv.run_chunk(db, run, chunk_size=2)          # prepare
        cursor_before = run.cursor
        pv.run_chunk(db, run, chunk_size=2)          # first 2 rows written...
        run.cursor, run.processed_urls = cursor_before, 0   # ...but the cursor was never saved (crash)
        db.commit()
        rid = run.id
    finally:
        db.close()
    _drain(rid, chunk_size=2)
    db = SessionLocal()
    try:
        assert db.query(PageValueScore).filter(PageValueScore.run_id == rid).count() == 5
        assert db.get(PageValueRun, rid).processed_urls == 5
    finally:
        db.close()


def test_runs_are_deduplicated_and_readers_keep_the_previous_run_until_the_new_one_completes():
    client, org, user = _ctx()
    mid, _ = _site(org, user, host="dedupe.example.com", n_pages=3)
    first = _run(org, mid)
    db = SessionLocal()
    try:
        a = pv.request_run(db, org_id=org, monitor_id=mid, trigger="manual")
        b = pv.request_run(db, org_id=org, monitor_id=mid, trigger="ga4_sync")
        assert a.id == b.id                                    # one active run per website
        assert db.query(ScheduledJob).filter(ScheduledJob.page_value_run_id == a.id).count() == 1
        pv.run_chunk(db, a, chunk_size=2); pv.run_chunk(db, a, chunk_size=2)   # mid-run
        second = a.id
    finally:
        db.close()
    assert client.get(f"/monitors/{mid}/page-opportunities/pages").json()["total"] == 3   # still the first run
    _drain(second, chunk_size=2)
    db = SessionLocal()
    try:
        assert db.query(PageValueScore).filter(PageValueScore.run_id == first).count() == 0
        assert db.query(PageValueScore).filter(PageValueScore.run_id == second).count() == 3
    finally:
        db.close()


def test_cancelling_a_run_stops_it_and_discards_partial_rows():
    client, org, user = _ctx()
    mid, _ = _site(org, user, host="cancel.example.com", n_pages=6)
    db = SessionLocal()
    try:
        run = pv.request_run(db, org_id=org, monitor_id=mid, trigger="manual")
        pv.run_chunk(db, run, chunk_size=2); pv.run_chunk(db, run, chunk_size=2)
        rid = run.id
    finally:
        db.close()
    r = client.post(f"/monitors/{mid}/page-opportunities/runs/{rid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == PV_CANCELLED
    db = SessionLocal()
    try:
        run = db.get(PageValueRun, rid)
        pv.run_chunk(db, run, chunk_size=2)       # a late chunk does nothing
        assert run.status == PV_CANCELLED
        assert db.query(PageValueScore).filter(PageValueScore.run_id == rid).count() == 0
    finally:
        db.close()
    assert client.post(f"/monitors/{mid}/page-opportunities/runs/{rid}/cancel").status_code == 409


def test_the_worker_processes_page_value_chunks_through_the_shared_queue(monkeypatch):
    _, org, user = _ctx()
    mid, _ = _site(org, user, host="queue.example.com", n_pages=3)
    monkeypatch.setattr(pv, "CHUNK_SIZE", 2)
    db = SessionLocal()
    try:
        run = pv.request_run(db, org_id=org, monitor_id=mid, trigger="manual")
        for _ in range(10):
            job = (db.query(ScheduledJob).filter(ScheduledJob.page_value_run_id == run.id,
                                                 ScheduledJob.status == JOB_PENDING).first())
            if not job:
                break
            job.status, job.attempts = "running", 1
            db.commit()
            asyncio.run(runner.process_job(db, job))
        db.refresh(run)
        assert run.status == PV_COMPLETED and run.processed_urls == 3
    finally:
        db.close()


def test_a_completed_crawl_queues_scoring_and_a_ga4_sync_rescores():
    _, org, user = _ctx()
    mid, jid = _site(org, user, host="hooks.example.com", n_pages=1)
    _ga4(org, user, mid)
    db = SessionLocal()
    try:
        runner._score_completed_crawl(db, db.get(CrawlJob, jid))
        run = pv.active_run(db, mid)
        assert run is not None and run.trigger == "crawl_completed"
        pv.cancel_run(db, run)
        assert pv.request_runs_after_sync(db, org, "ga4_sync") == 1
        assert pv.active_run(db, mid).trigger == "ga4_sync"
    finally:
        db.close()


def test_inbound_links_are_counted_once_per_linking_page():
    _, org, user = _ctx()
    mid, jid = _site(org, user, host="links.example.com", pages=[
        ("/a", {}, [], "crawled"), ("/b", {}, [], "crawled"), ("/c", {}, [], "crawled")])
    db = SessionLocal()
    try:
        job = db.get(CrawlJob, jid)
        rows = {r.url.rsplit("/", 1)[1]: r for r in db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == jid)}
        base = "https://links.example.com"
        discovery._count_inbound_links(db, job, rows["a"], [f"{base}/b", f"{base}/b#top", f"{base}/c", f"{base}/a"])
        discovery._count_inbound_links(db, job, rows["c"], [f"{base}/b"])
        counts = {r.url.rsplit("/", 1)[1]: r.inbound_link_count
                  for r in db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == jid)}
        assert counts == {"a": 0, "b": 2, "c": 1}
    finally:
        db.close()


# ------------------------------------------------------------------ API

def test_api_overview_list_filters_and_detail():
    client, org, user = _ctx()
    mid, jid = _site(org, user, host="api.example.com", pages=[
        ("/products/a", {"schema": {"types": ["Product"]}}, ["missing_meta_description"], "crawled"),
        ("/terms", {"title": "Terms of service"}, [], "crawled"),
        ("/services/x", {"schema": {"types": ["Service"]}}, [], "crawled"),
    ])
    _ga4(org, user, mid, landing=[("/products/a", "perplexity", 8, 2, None)])
    db = SessionLocal()
    try:
        crawl_url = db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == jid, CrawlUrl.url.like("%/products/a")).one()
        db.add(UrlFinding(crawl_job_id=jid, crawl_url_id=crawl_url.id, organization_id=org, url=crawl_url.url,
                          template_id=None, finding_title="Missing meta description", category="metadata",
                          priority="medium", confidence="high", evidence_basis="deterministic",
                          issue_key="det:missing_meta_description:unclassified", recommended_fix="Add one.",
                          evidence={"kind": "confirmed"}))
        db.commit()
        cu_id = crawl_url.id
    finally:
        db.close()
    _run(org, mid)

    o = client.get(f"/monitors/{mid}/page-opportunities").json()
    c = o["results"]["counts"]
    assert c["scored_urls"] == 3 and c["discovered_urls"] == 3 and c["commercial_pages"] == 2
    assert c["pages_with_ai_traffic"] == 1 and c["pages_with_conversions"] == 1
    assert c["pages_with_search_clicks"] is None          # Search Console not connected: unknown, not 0
    assert "does not guarantee revenue" in o["definitions"]["score"]

    lst = client.get(f"/monitors/{mid}/page-opportunities/pages", params={"sort": "-score"}).json()
    assert lst["pages"][0]["url"].endswith("/products/a")
    assert client.get(f"/monitors/{mid}/page-opportunities/pages",
                      params={"has": "ai"}).json()["total"] == 1
    assert client.get(f"/monitors/{mid}/page-opportunities/pages",
                      params={"page_type": "legal"}).json()["total"] == 1
    assert client.get(f"/monitors/{mid}/page-opportunities/pages",
                      params={"q": "services", "commercial": True}).json()["total"] == 1
    paged = client.get(f"/monitors/{mid}/page-opportunities/pages", params={"page_size": 2, "page": 2}).json()
    assert paged["total"] == 3 and len(paged["pages"]) == 1
    assert client.get(f"/monitors/{mid}/page-opportunities/pages", params={"sort": "nope"}).status_code == 422

    d = client.get(f"/monitors/{mid}/page-opportunities/pages/{cu_id}").json()
    assert d["classification"]["page_type"] == "product" and d["classification"]["evidence"]
    assert d["recommended_action"]["kind"] == "finding"   # links the existing card, never a duplicate
    assert d["recommended_action"]["title"] == "Missing meta description"
    assert any("AI assistants" in w for w in d["why_it_matters"])
    assert {c["key"] for c in d["score_breakdown"]} >= {"purpose", "conversions", "ai_sessions", "search"}


def test_api_authorization_isolation_and_plan_gating(monkeypatch):
    owner, org, user = _ctx()
    mid, _ = _site(org, user, host="authz.example.com", n_pages=1)
    _run(org, mid)
    other, _, _ = _ctx()
    assert other.get(f"/monitors/{mid}/page-opportunities").status_code == 404
    assert other.get(f"/monitors/{mid}/page-opportunities/pages").status_code == 404
    assert other.post(f"/monitors/{mid}/page-opportunities/refresh").status_code == 404
    db = SessionLocal()
    try:
        cu = db.query(PageValueScore).filter(PageValueScore.monitor_id == mid).first().crawl_url_id
    finally:
        db.close()
    assert other.get(f"/monitors/{mid}/page-opportunities/pages/{cu}").status_code == 404

    monkeypatch.setattr(settings, "billing_enforced", True)
    assert owner.get(f"/monitors/{mid}/page-opportunities").status_code == 402
    assert owner.post(f"/monitors/{mid}/page-opportunities/refresh").status_code == 402


def test_viewers_can_read_but_not_start_runs(monkeypatch):
    from tests.test_rbac import _invite_and_accept
    owner, org, user = _ctx()
    mid, _ = _site(org, user, host="viewer.example.com", n_pages=1)
    _run(org, mid)
    viewer, _ = _invite_and_accept(owner, "viewer", monkeypatch)
    assert viewer.get(f"/monitors/{mid}/page-opportunities/pages").status_code == 200
    assert viewer.post(f"/monitors/{mid}/page-opportunities/refresh").status_code == 403


def test_refresh_without_a_completed_crawl_is_a_clear_conflict():
    client, org, user = _ctx()
    db = SessionLocal()
    try:
        m = Monitor(organization_id=org, user_id=user, url="https://new.example.com/", normalized_url="new.example.com",
                    frequency="manual", status="active")
        db.add(m); db.commit()
        mid = m.id
    finally:
        db.close()
    r = client.post(f"/monitors/{mid}/page-opportunities/refresh")
    assert r.status_code == 409
    o = client.get(f"/monitors/{mid}/page-opportunities").json()
    assert o["has_completed_crawl"] is False and o["results"] is None


# ------------------------------------------------------------------ Search Console

def test_search_console_asks_only_for_webmasters_readonly():
    client, _, _ = _ctx()
    url = client.get("/integrations/search-console/connect").json()["authorize_url"]
    qs = parse_qs(urlsplit(url).query)
    assert qs["scope"] == [gsc.GSC_SCOPE]
    assert qs["include_granted_scopes"] == ["false"]
    assert qs["code_challenge_method"] == ["S256"]
    # GA4's own authorize URL is unchanged.
    assert "analytics.readonly" in ga4.build_authorize_url("s", "c") and "webmasters" not in ga4.build_authorize_url("s", "c")


def _gsc_state(client, monkeypatch, scope=gsc.GSC_SCOPE):
    client.get("/integrations/search-console/connect")
    db = SessionLocal()
    try:
        state = db.query(OAuthState).filter(OAuthState.provider == gsc.OAUTH_PROVIDER,
                                            OAuthState.consumed.is_(False)).order_by(OAuthState.created_at.desc()).first().state
    finally:
        db.close()

    async def fake_exchange(code, verifier):
        return {"access_token": "at", "refresh_token": "rt-new", "scope": scope}
    monkeypatch.setattr(ga4, "exchange_code_for_tokens", fake_exchange)
    return state


def test_the_shared_callback_completes_a_search_console_grant_without_touching_ga4(monkeypatch):
    client, org, user = _ctx()
    ga4_id = _ga4(org, user, None, linked=False)
    state = _gsc_state(client, monkeypatch)
    r = client.get("/integrations/google/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.status_code == 302 and "gsc=connected" in r.headers["location"]
    assert "/app/site-intelligence" in r.headers["location"]
    db = SessionLocal()
    try:
        conn = db.query(SearchConsoleConnection).filter(SearchConsoleConnection.organization_id == org).one()
        assert conn.status == GA4_STATUS_PENDING_PROPERTY
        assert token_crypto.decrypt_token(conn.encrypted_refresh_token) == "rt-new"
        assert token_crypto.decrypt_token(db.get(GoogleAnalyticsConnection, ga4_id).encrypted_refresh_token) == "rt"
    finally:
        db.close()


def test_a_grant_without_the_search_console_scope_is_rejected(monkeypatch):
    client, org, _ = _ctx()
    state = _gsc_state(client, monkeypatch, scope="openid")
    r = client.get("/integrations/google/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert "gsc=error&reason=scope_not_granted" in r.headers["location"]
    db = SessionLocal()
    try:
        assert db.query(SearchConsoleConnection).filter(SearchConsoleConnection.organization_id == org).count() == 0
    finally:
        db.close()


def test_a_ga4_state_can_never_complete_a_search_console_grant():
    _, org, user = _ctx()
    db = SessionLocal()
    try:
        row, _ = ga4.create_oauth_state(db, org_id=org, user_id=user)          # GA4 flow
        assert ga4.consume_oauth_state(db, row.state, provider=gsc.OAUTH_PROVIDER) is None
        assert ga4.consume_oauth_state(db, row.state) is not None
    finally:
        db.close()


@pytest.mark.parametrize("site,url,covered", [
    ("sc-domain:example.com", "https://shop.example.com/a", True),
    ("sc-domain:example.com", "http://example.com/", True),
    ("sc-domain:example.com", "https://notexample.com/", False),
    ("https://www.example.com/", "https://www.example.com/blog", True),
    ("https://www.example.com/", "https://example.com/blog", False),
    ("https://www.example.com/shop/", "https://www.example.com/blog", False),
    ("https://www.example.com/", "http://www.example.com/", False),
])
def test_property_coverage_is_an_exact_url_match(site, url, covered):
    assert gsc.site_covers(site, url) is covered


def test_selecting_a_property_accepts_only_verified_ones_and_clears_old_rows(monkeypatch):
    client, org, user = _ctx()
    cid = _gsc(org, user, site_url="sc-domain:old.com", rows=[("https://old.com/a", 1, 10, 1.0)])

    async def fake_refresh(_t):
        return {"access_token": "at"}

    async def fake_sites(_t):
        return [{"site_url": "sc-domain:example.com", "permission_level": "siteOwner"}]
    monkeypatch.setattr(ga4, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(gsc, "list_sites", fake_sites)
    assert client.post("/integrations/search-console/select-site",
                       json={"site_url": "sc-domain:unverified.com"}).status_code == 403
    r = client.post("/integrations/search-console/select-site", json={"site_url": "sc-domain:example.com"})
    assert r.status_code == 200
    db = SessionLocal()
    try:
        assert db.query(GscPageSnapshot).filter(GscPageSnapshot.connection_id == cid).count() == 0
        assert db.get(SearchConsoleConnection, cid).last_synced_at is None
    finally:
        db.close()


def test_list_sites_drops_unverified_properties(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"siteEntry": [{"siteUrl": "sc-domain:a.com", "permissionLevel": "siteOwner"},
                                  {"siteUrl": "https://b.com/", "permissionLevel": "siteUnverifiedUser"}]}

    async def fake_get(self, url, **kw):
        assert url.endswith("/webmasters/v3/sites")
        return Resp()
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert asyncio.run(gsc.list_sites("at")) == [{"site_url": "sc-domain:a.com", "permission_level": "siteOwner"}]


def test_sync_paginates_replaces_rows_and_flags_truncation(monkeypatch):
    _, org, user = _ctx()
    cid = _gsc(org, user, rows=[("https://shop.example.com/stale", 99, 999, 1.0)])
    monkeypatch.setattr(gsc, "ROW_LIMIT", 2)
    monkeypatch.setattr(gsc, "MAX_PAGES_PER_SYNC", 2)
    calls = []

    async def fake_refresh(_t):
        return {"access_token": "at"}

    async def fake_query(token, site, start, end, start_row):
        calls.append(start_row)
        return [{"page": f"https://shop.example.com/p{start_row + i}", "clicks": 1, "impressions": 10,
                 "position": 3.0} for i in range(2)]
    monkeypatch.setattr(ga4, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(gsc, "query_pages", fake_query)
    db = SessionLocal()
    try:
        conn = db.get(SearchConsoleConnection, cid)
        out = asyncio.run(gsc.run_sync(db, conn))
        assert calls == [0, 2] and out["truncated"] is True and conn.truncated is True
        pages = {p for (p,) in db.query(GscPageSnapshot.page).filter(GscPageSnapshot.connection_id == cid)}
        assert pages == {f"https://shop.example.com/p{i}" for i in range(4)}     # stale row replaced
        start, end = gsc.sync_window()
        assert (conn.window_start, conn.window_end) == (start.isoformat(), end.isoformat())
        assert end == date.today() - timedelta(days=gsc.DATA_LAG_DAYS)
    finally:
        db.close()


def test_sync_access_errors_mark_the_connection_and_keep_existing_rows(monkeypatch):
    _, org, user = _ctx()
    cid = _gsc(org, user, rows=[("https://shop.example.com/kept", 5, 50, 2.0)])

    async def fake_refresh(_t):
        return {"access_token": "at"}

    async def denied(*a, **k):
        raise ga4.GoogleApiError("denied", status_code=403)
    monkeypatch.setattr(ga4, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(gsc, "query_pages", denied)
    db = SessionLocal()
    try:
        conn = db.get(SearchConsoleConnection, cid)
        from app.services.integrations.google_errors import SyncStopped
        with pytest.raises(SyncStopped):
            asyncio.run(gsc.run_sync(db, conn))
        db.refresh(conn)
        assert conn.status == GA4_STATUS_ERROR and conn.last_sync_error_code == "property_inaccessible"
        assert db.query(GscPageSnapshot).filter(GscPageSnapshot.connection_id == cid).count() == 1
    finally:
        db.close()


def test_search_console_status_reports_coverage_and_is_org_scoped():
    client, org, user = _ctx()
    mid, _ = _site(org, user, host="shop.example.com", n_pages=0)
    _gsc(org, user)
    s = client.get("/integrations/search-console/status").json()
    assert s["status"] == GA4_STATUS_ACTIVE and mid in s["covered_monitor_ids"]
    other, _, _ = _ctx()
    assert other.get("/integrations/search-console/status").json() == {"status": "not_connected"}
    assert other.delete("/integrations/search-console").status_code == 404


def test_business_impact_shows_a_page_opportunities_summary_not_a_duplicate_card():
    client, org, user = _ctx()
    mid, _ = _site(org, user, host="bi.example.com", pages=[
        ("/products/a", {"schema": {"types": ["Product"]}}, ["missing_meta_description"], "crawled")])
    before = client.get("/business-impact/command-center", params={"monitor_id": mid}).json()
    assert before["indicators"]["page_opportunities"]["status"] == "not_configured"
    _ga4(org, user, mid, landing=[("/products/a", "chatgpt", 50, 5, None)])
    _run(org, mid)
    cc = client.get("/business-impact/command-center", params={"monitor_id": mid}).json()
    ind = cc["indicators"]["page_opportunities"]
    assert ind["status"] == "available" and ind["high_priority"] == 1 and ind["commercial_pages"] == 1
    assert ind["link"] == f"/app/site-intelligence/{mid}?view=opportunities"
    assert not [f for f in cc["findings"] if "opportunit" in f["title"].lower()]
