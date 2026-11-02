"""URL-wise optimization: the action lifecycle (a user's "fixed" never verifies),
real verification by page re-check and by the next crawl (fixed / partially fixed /
not fixed / unable to verify, before-and-after evidence kept), regressions and
duplicate-alert prevention, rule-version re-baselining, before/after performance
snapshots (complete, incomplete and missing data), the url_recheck background job
(dedupe, rate limit, failure isolation, cancellation, stalled recovery), next best
actions, change detection and API authorization. `fetch` is monkeypatched: no network."""
import asyncio
from datetime import date, datetime, timedelta

import pytest

from app.config import settings
from app.db.models import (
    JOB_FAILED, JOB_KIND_URL_RECHECK, JOB_PENDING, JOB_RUNNING, Alert, CrawlJob, CrawlUrl, Ga4LandingPageSnapshot,
    GoogleAnalyticsConnection, Monitor, PageValueRun, PageValueScore, ScheduledJob, SearchConsoleConnection,
    UrlAction, UrlActionEvent, UrlPerformanceSnapshot, GscPageSnapshot, CRAWL_COMPLETED,
)
from app.db.session import SessionLocal
from app.monitoring import runner, scheduler
from app.scanner.models import PageBundle
from app.services.url_intelligence import fix_impact, issues
from app.services.url_intelligence import optimization as opt
from app.services.url_intelligence import page_intelligence as pi
from app.services.url_intelligence import page_value as pv
from app.services.url_intelligence import verification as vf
from tests.test_page_opportunities import (   # noqa: F401 — shared fixtures + helpers
    _cleanup_jobs, _ctx, _ga4, _gsc, _keys, _site, _run,
)

PATH = "/services/seo"
GOOD_TITLE = "SEO audits for small businesses in Leeds | Acme"
META = "Independent SEO audits for small businesses: what we check, how long it takes and what it costs you."


@pytest.fixture(autouse=True)
def _quiet_rechecks():
    """The test DB is shared: never leave a due re-check or a queued batch behind."""
    yield
    db = SessionLocal()
    try:
        db.query(UrlAction).update({UrlAction.next_check_at: None, UrlAction.next_snapshot_at: None})
        db.query(ScheduledJob).filter(ScheduledJob.kind == JOB_KIND_URL_RECHECK,
                                      ScheduledJob.status.in_((JOB_PENDING, JOB_RUNNING))).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _pev(host, title="SEO", meta=None, words=400, alts=0, http=200):
    missing = []
    if not title:
        missing.append("missing_title")
    if not meta:
        missing.append("missing_meta_description")
    return {"http_status": http, "title": title, "title_length": len(title or ""), "meta_description": meta,
            "meta_description_length": len(meta or ""), "h1_texts": ["SEO audits"],
            "heading_counts": {"h1": 1, "h2": 2, "h3": 0}, "word_count": words,
            "subheadings": ["What is included", "How much does it cost?"], "question_headings": 1,
            "dates": {"published": None, "modified": None, "source": None},
            "canonical_url": f"https://{host}{PATH}", "noindex": False, "crawl_depth": 1,
            "schema": {"state": "present", "types": ["Service"], "malformed": 0, "blocks": 1,
                       "entity_evidence": {"name": None}},
            "images_missing_alt": alts, "image_count": max(alts, 3), "missing_content_elements": missing}


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


def _setup(host, ev=None, high=True):
    client, org, user = _ctx()
    ev = ev or _pev(host)
    mid, _ = _site(org, user, host=host, pages=[(PATH, ev, issues.page_issues(ev, None), "crawled")])
    rid = _run(org, mid)
    db = SessionLocal()
    try:
        if high:
            db.query(PageValueScore).filter(PageValueScore.run_id == rid).update({PageValueScore.priority: "high"})
            db.commit()
        cu = db.query(PageValueScore).filter(PageValueScore.run_id == rid).one().crawl_url_id
    finally:
        db.close()
    assert _drain_all(rid) == "completed"
    return client, org, user, mid, cu


def _recrawl(org, mid, host, ev, engine="2", high=True):
    """A new completed crawl of the page, then scoring + analysis (which runs the sync)."""
    db = SessionLocal()
    try:
        now = datetime.utcnow() + timedelta(seconds=1)
        m = db.get(Monitor, mid)
        j = CrawlJob(organization_id=org, monitor_id=mid, seed_url=m.url, normalized_seed_url=m.url, max_urls=100,
                     phase=CRAWL_COMPLETED, engine_version=engine, created_at=now, finished_at=now, updated_at=now)
        db.add(j); db.commit()
        codes = issues.page_issues(ev, None)
        db.add(CrawlUrl(crawl_job_id=j.id, organization_id=org, url=f"https://{host}{PATH}",
                        normalized_key=f"{host}{PATH}", discovery_source="sitemap", status="crawled",
                        http_status=ev.get("http_status"), page_title=ev.get("title"), evidence=ev,
                        content_fingerprint=f"fp-{ev.get('title')}-{ev.get('meta_description')}-{ev.get('images_missing_alt')}",
                        issue_codes=codes, issue_count=len(codes), issue_severity=issues.severity_rank(codes),
                        inbound_link_count=0, crawled_at=now))
        db.commit()
        rid = pv.request_run(db, org_id=org, monitor_id=mid, trigger="crawl_completed").id
    finally:
        db.close()
    from tests.test_page_opportunities import _drain
    _drain(rid)
    if high:
        db = SessionLocal()
        try:
            db.query(PageValueScore).filter(PageValueScore.run_id == rid).update({PageValueScore.priority: "high"})
            db.commit()
        finally:
            db.close()
    assert _drain_all(rid) == "completed"
    db = SessionLocal()
    try:
        return rid, db.query(PageValueScore).filter(PageValueScore.run_id == rid).one().crawl_url_id
    finally:
        db.close()


def _html(host, title=GOOD_TITLE, meta=None, alts=0):
    head = (f"<title>{title}</title>" if title else "") + (f'<meta name="description" content="{meta}">' if meta else "")
    head += (f'<link rel="canonical" href="https://{host}{PATH}">'
             '<script type="application/ld+json">{"@type": "Service", "name": "SEO audits"}</script>')
    imgs = "".join('<img src="/a.png">' for _ in range(alts)) + '<img src="/b.png" alt="Team">'
    body = ("<h1>SEO audits</h1><h2>What is included</h2><h2>How much does it cost?</h2>"
            f"{imgs}<p>{'word ' * 400}</p>")
    return f"<html><head>{head}</head><body>{body}</body></html>"


def _fetch(html, status=200, final_url=""):
    async def f(url, **kw):
        return PageBundle(url=url, html=html, status_code=status, final_url=final_url or url)
    return f


def _action(mid, code):
    db = SessionLocal()
    try:
        return db.query(UrlAction).filter(UrlAction.monitor_id == mid, UrlAction.code == code).one()
    finally:
        db.close()


def _events(action_id, kind=None):
    db = SessionLocal()
    try:
        q = db.query(UrlActionEvent).filter(UrlActionEvent.action_id == action_id)
        if kind:
            q = q.filter(UrlActionEvent.kind == kind)
        return q.order_by(UrlActionEvent.created_at).all()
    finally:
        db.close()


def _run_checks(mid):
    db = SessionLocal()
    try:
        return asyncio.run(opt.run_due_checks(db, db.get(Monitor, mid)))
    finally:
        db.close()


def _act(client, mid, cu, code, op, **body):
    return client.post(f"/monitors/{mid}/optimization/urls/{cu}/actions/{code}", json={"op": op, **body})


# ------------------------------------------------------------------ unified record + lifecycle

def test_the_unified_record_links_value_analysis_and_derived_statuses():
    client, org, user, mid, cu = _setup("rec.example.com")
    r = client.get(f"/monitors/{mid}/optimization/urls/{cu}")
    assert r.status_code == 200
    d = r.json()
    assert d["value"]["priority"] == "high" and d["analysis"]["rules_version"] == pi.RULES_VERSION
    by = {a["code"]: a for a in d["actions"]}
    assert {"title_too_short", "missing_meta_description", "orphan_page"} <= set(by)
    assert by["title_too_short"]["status"] == "new" and by["title_too_short"]["tracked"] is False
    assert by["orphan_page"]["verification_method"] == "site_crawl"
    assert any(e["kind"] == "detected" for e in d["timeline"])
    db = SessionLocal()
    try:   # nothing is materialized until someone acts
        assert db.query(UrlAction).filter(UrlAction.monitor_id == mid).count() == 0
    finally:
        db.close()


def test_full_lifecycle_and_marking_fixed_never_verifies():
    client, org, user, mid, cu = _setup("life.example.com")
    assert _act(client, mid, cu, "title_too_short", "start").json()
    a = _action(mid, "title_too_short")
    assert a.status == "in_progress" and a.work_started_at and a.baseline_evidence["title"] == "SEO"
    assert _act(client, mid, cu, "title_too_short", "note", note="Asked the copywriter").status_code == 200
    d = _act(client, mid, cu, "title_too_short", "ready").json()
    a = _action(mid, "title_too_short")
    assert a.status == "ready_for_verification" and a.verified_at is None and a.next_check_at is not None
    assert next(x for x in d["actions"] if x["code"] == "title_too_short")["status"] == "ready_for_verification"
    db = SessionLocal()
    try:   # a check is queued in the background, not run inline
        assert scheduler.has_active_url_recheck_job(db, mid) is not None
    finally:
        db.close()
    assert _act(client, mid, cu, "title_too_short", "reopen").status_code == 409   # not verified yet
    assert _act(client, mid, cu, "missing_meta_description", "ignore").status_code == 422   # no reason
    assert _act(client, mid, cu, "missing_meta_description", "ignore", reason="Generated by our CMS").status_code == 200
    ig = _action(mid, "missing_meta_description")
    assert ig.status == "ignored" and ig.ignored_reason == "Generated by our CMS"
    assert _act(client, mid, cu, "missing_meta_description", "recheck").status_code == 409
    assert _act(client, mid, cu, "missing_meta_description", "reopen").json()
    assert _action(mid, "missing_meta_description").status == "reopened"
    assert _act(client, mid, cu, "orphan_page", "recheck").status_code == 409   # needs a full crawl
    assert _act(client, mid, cu, "no_such_code", "start").status_code == 404
    assert _act(client, mid, cu, "title_too_short", "fly").status_code == 422
    kinds = [e["kind"] for e in client.get(f"/monitors/{mid}/optimization/urls/{cu}").json()["timeline"]]
    assert {"status", "note", "detected"} <= set(kinds)


def test_performance_patterns_cannot_be_rechecked():
    db = SessionLocal()
    try:
        a = UrlAction(organization_id="o", monitor_id="m-perf", normalized_key="k", url="https://x/y",
                      code="low_search_ctr", issue="x", status="new")
        db.add(a); db.commit()
        with pytest.raises(opt.ActionError) as e:
            opt.apply_op(db, a, "recheck", user_id=None)
        assert e.value.status == 409 and "performance" in e.value.message
        opt.apply_op(db, a, "ready", user_id=None)
        assert a.status == "ready_for_verification" and a.next_check_at is None
    finally:
        db.close()


# ------------------------------------------------------------------ verification by page re-check

def test_recheck_verifies_a_fixed_issue_and_keeps_before_and_after(monkeypatch):
    host = "fix.example.com"
    client, org, user, mid, cu = _setup(host)
    _act(client, mid, cu, "title_too_short", "ready")
    _act(client, mid, cu, "missing_meta_description", "ready")
    monkeypatch.setattr(vf, "fetch", _fetch(_html(host)))   # title fixed, meta description still missing
    res = _run_checks(mid)
    assert res["pages"] == 1 and res["checked"] == 2
    t, m = _action(mid, "title_too_short"), _action(mid, "missing_meta_description")
    assert (t.status, t.last_result, t.last_check_method) == ("verified", "fixed", "recrawl") and t.verified_at
    assert (m.status, m.last_result) == ("not_fixed", "not_fixed")
    ev = _events(t.id, "verification")[-1].detail
    assert ev["before"]["title"] == "SEO" and ev["after"]["title"] == GOOD_TITLE
    assert ev["before_observed"].startswith("Observed on this page")


def test_partial_fix_unreachable_redirected_and_error_pages(monkeypatch):
    host = "part.example.com"
    client, org, user, mid, cu = _setup(host, ev=_pev(host, title=GOOD_TITLE, meta=META, alts=3))
    _act(client, mid, cu, "images_missing_alt", "ready")
    monkeypatch.setattr(vf, "fetch", _fetch(_html(host, meta=META, alts=1)))
    _run_checks(mid)
    a = _action(mid, "images_missing_alt")
    assert (a.status, a.last_result) == ("partially_fixed", "partially_fixed")

    from app.core.fetch import FetchError

    async def down(url, **kw):
        raise FetchError("timeout")
    _act(client, mid, cu, "images_missing_alt", "recheck")
    monkeypatch.setattr(vf, "fetch", down)
    _run_checks(mid)
    a = _action(mid, "images_missing_alt")
    assert a.last_result == "unable_to_verify" and a.status == "partially_fixed"   # never read as fixed
    assert "couldn't be fetched" in a.check_error and a.unable_count == 1
    assert a.next_check_at is not None    # retried with backoff

    _act(client, mid, cu, "images_missing_alt", "recheck")
    monkeypatch.setattr(vf, "fetch", _fetch(_html(host, meta=META), final_url=f"https://{host}/"))
    _run_checks(mid)
    assert "redirects" in _action(mid, "images_missing_alt").check_error

    _act(client, mid, cu, "images_missing_alt", "recheck")
    monkeypatch.setattr(vf, "fetch", _fetch("<html><body>Not found</body></html>", status=404))
    _run_checks(mid)
    a = _action(mid, "images_missing_alt")
    assert a.last_result == "unable_to_verify" and "HTTP 404" in a.check_error


def test_a_score_increase_alone_never_verifies():
    host = "score.example.com"
    client, org, user, mid, cu = _setup(host)
    _act(client, mid, cu, "title_too_short", "ready")
    db = SessionLocal()
    try:
        db.query(PageValueScore).filter(PageValueScore.crawl_url_id == cu).update({PageValueScore.score: 99.0})
        db.commit()
        run = pv.latest_run(db, mid, "completed")
        opt.sync_after_analysis(db, run)     # same crawl, higher score: no new evidence
    finally:
        db.close()
    a = _action(mid, "title_too_short")
    assert a.status == "ready_for_verification" and a.last_result is None


# ------------------------------------------------------------------ verification by crawl, regressions

def test_next_crawl_verifies_then_a_regression_reopens_with_one_alert():
    host = "crawl.example.com"
    client, org, user, mid, cu = _setup(host)
    _act(client, mid, cu, "title_too_short", "ready")
    _recrawl(org, mid, host, _pev(host, title=GOOD_TITLE))
    a = _action(mid, "title_too_short")
    assert (a.status, a.last_check_method) == ("verified", "crawl")
    _recrawl(org, mid, host, _pev(host, title="SEO"))     # the short title is back
    a = _action(mid, "title_too_short")
    assert a.status == "reopened"
    _recrawl(org, mid, host, _pev(host, title="SEO!"))    # still back on another crawl
    db = SessionLocal()
    try:
        alerts = db.query(Alert).filter(Alert.monitor_id == mid, Alert.type == "url_fix_regressed").all()
        assert len(alerts) == 1 and alerts[0].detail["code"] == "title_too_short"
    finally:
        db.close()
    rec = client.get(f"/monitors/{mid}/optimization/urls/{_recrawl(org, mid, host, _pev(host, title='SEO!'))[1]}").json()
    assert rec["change"]["classification"] in ("no_change", "unrelated_change")


def test_rules_version_changes_rebaseline_without_claims(monkeypatch):
    host = "rules.example.com"
    client, org, user, mid, cu = _setup(host)
    _act(client, mid, cu, "title_too_short", "ready")
    db = SessionLocal()
    try:
        db.query(UrlAction).filter(UrlAction.monitor_id == mid).update({UrlAction.rules_version: "0"})
        db.commit()
    finally:
        db.close()
    _recrawl(org, mid, host, _pev(host, title=GOOD_TITLE))
    a = _action(mid, "title_too_short")
    assert a.status == "ready_for_verification" and a.rules_version == pi.RULES_VERSION
    assert [e.kind for e in _events(a.id)][-1] == "rules_changed"
    _, cu2 = _recrawl(org, mid, host, _pev(host, title=GOOD_TITLE, words=900), engine="3")   # engine change
    rec = client.get(f"/monitors/{mid}/optimization/urls/{cu2}").json()
    assert rec["change"]["classification"] == "cannot_evaluate"


def test_change_detection_classifies_and_new_serious_issues_alert_once():
    host = "chg.example.com"
    client, org, user, mid, cu = _setup(host, ev=_pev(host, title=GOOD_TITLE, meta=META))
    _, cu2 = _recrawl(org, mid, host, _pev(host, title=None, meta=META))     # title removed: high, confirmed
    ch = client.get(f"/monitors/{mid}/optimization/urls/{cu2}").json()["change"]
    assert ch["classification"] == "regression" and ch["new_issues"][0]["code"] == "missing_title"
    assert any(c["field"] == "title" for c in ch["changes"])
    _recrawl(org, mid, host, _pev(host, title=None, meta=META, words=402))
    db = SessionLocal()
    try:
        assert db.query(Alert).filter(Alert.monitor_id == mid, Alert.type == "url_new_issue").count() == 1
    finally:
        db.close()
    _, cu3 = _recrawl(org, mid, host, _pev(host, title=GOOD_TITLE, meta=META))
    assert client.get(f"/monitors/{mid}/optimization/urls/{cu3}").json()["change"]["classification"] == "improvement"
    _, cu4 = _recrawl(org, mid, host, _pev(host, title=GOOD_TITLE, meta=META, words=900))
    assert client.get(f"/monitors/{mid}/optimization/urls/{cu4}").json()["change"]["classification"] == "unrelated_change"


def test_evidence_changes_ignore_small_fluctuations():
    a = {"title": "A", "word_count": 400}
    assert opt.evidence_changes(a, {"title": "A", "word_count": 420}, 5, 6) == []
    ch = opt.evidence_changes(a, {"title": "B", "word_count": 900}, 5, 0)
    assert {c["field"] for c in ch} == {"title", "word_count", "inbound_links"}


# ------------------------------------------------------------------ performance snapshots

def _snap_action(org, mid, url, started, verified):
    db = SessionLocal()
    try:
        a = UrlAction(organization_id=org, monitor_id=mid, normalized_key=url.split("//")[1], url=url,
                      code="title_too_short", issue="Page title is very short", status="verified",
                      work_started_at=started, verified_at=verified, created_at=started)
        db.add(a); db.commit()
        return a.id
    finally:
        db.close()


def test_ga4_before_after_uses_equal_complete_windows_and_never_claims_cause():
    client, org, user = _ctx()
    host = "perf.example.com"
    mid, _ = _site(org, user, host=host, n_pages=0)
    conn = _ga4(org, user, mid)
    db = SessionLocal()
    try:
        c = db.get(GoogleAnalyticsConnection, conn)
        c.landing_pages_synced_at = datetime(2026, 11, 1)
        for d, cls, s, k in [("20260801", "organic_search", 1, 0), ("20260810", "chatgpt", 10, 1),
                             ("20260810", "organic_search", 100, 2), ("20260915", "chatgpt", 20, 3),
                             ("20260915", "organic_search", 150, 4), ("20260905", "chatgpt", 999, 9)]:
            db.add(Ga4LandingPageSnapshot(organization_id=org, connection_id=conn, date=d, landing_page=PATH,
                                          traffic_class=cls, sessions=s, key_events=k))
        db.commit()
    finally:
        db.close()
    aid = _snap_action(org, mid, f"https://{host}{PATH}", datetime(2026, 9, 1), datetime(2026, 9, 10))
    db = SessionLocal()
    try:
        a = db.get(UrlAction, aid)
        fix_impact.capture(db, a, today=date(2026, 10, 5))    # after window (09-11..10-08) not over
        snaps = {(s.period, s.source): s for s in db.query(UrlPerformanceSnapshot).filter_by(action_id=aid)}
        assert snaps[("before", "ga4")].status == "complete"
        assert (snaps[("before", "ga4")].window_start, snaps[("before", "ga4")].window_end) == ("2026-08-04", "2026-08-31")
        assert snaps[("after", "ga4")].status == "pending" and a.next_snapshot_at is not None
        fix_impact.capture(db, a, today=date(2026, 11, 2))
        cmp = fix_impact.comparison(db, a)
    finally:
        db.close()
    ga4 = next(s for s in cmp["sources"] if s["source"] == "ga4")
    assert ga4["before"]["metrics"]["ai_sessions"] == 10 and ga4["after"]["metrics"]["ai_sessions"] == 20
    assert ga4["after"]["window"] == ["2026-09-11", "2026-10-08"]   # equal 28-day windows
    assert ga4["changes"]["organic_sessions"] == {"before": 100, "after": 150, "delta": 50, "pct": 50.0}
    assert cmp["excluded_period"] == ["2026-09-01", "2026-09-10"]   # the 999 sessions on 09-05 count nowhere
    assert "not proof" in cmp["disclaimer"]
    gsc = next(s for s in cmp["sources"] if s["source"] == "gsc")
    assert gsc["before"]["status"] == "unavailable" and "isn't connected" in gsc["before"]["reason"]
    fp = next(s for s in cmp["sources"] if s["source"] == "first_party")
    assert fp["before"]["status"] == "unavailable" and fp["changes"] == {}


def test_missing_ga4_and_search_console_windows_are_labelled_never_zero():
    client, org, user = _ctx()
    host = "gscw.example.com"
    mid, _ = _site(org, user, host=host, n_pages=0)
    gid = _gsc(org, user, rows=[(f"https://{host}{PATH}", 10, 400, 4.0)])
    aid = _snap_action(org, mid, f"https://{host}{PATH}", datetime(2026, 11, 1), datetime(2026, 11, 1))
    db = SessionLocal()
    try:
        a = db.get(UrlAction, aid)
        fix_impact.capture(db, a, today=date(2026, 11, 2))
        snaps = {(s.period, s.source): s for s in db.query(UrlPerformanceSnapshot).filter_by(action_id=aid)}
        assert snaps[("before", "gsc")].status == "complete" and snaps[("before", "gsc")].metrics["ctr_pct"] == 2.5
        assert snaps[("after", "gsc")].status == "pending"      # the stored window predates the fix
        assert snaps[("before", "ga4")].status == "unavailable" and snaps[("before", "ga4")].metrics is None
        assert snaps[("before", "answer_tracking")].status == "unavailable"
        c = db.get(SearchConsoleConnection, gid)
        c.window_start, c.window_end = "2026-11-05", "2026-12-02"
        db.query(GscPageSnapshot).filter_by(connection_id=gid).update({GscPageSnapshot.clicks: 30})
        db.commit()
        fix_impact.capture(db, a, today=date(2026, 12, 6))
        cmp = fix_impact.comparison(db, a)
    finally:
        db.close()
    gsc = next(s for s in cmp["sources"] if s["source"] == "gsc")
    assert gsc["after"]["window"] == ["2026-11-05", "2026-12-02"] and gsc["changes"]["clicks"]["delta"] == 20
    assert "AI Overview" in gsc["after"]["reason"]


# ------------------------------------------------------------------ background job

def test_due_rechecks_are_queued_once_per_website_and_processed_by_the_worker(monkeypatch):
    host = "job.example.com"
    client, org, user, mid, cu = _setup(host)
    _act(client, mid, cu, "title_too_short", "ready")
    db = SessionLocal()
    try:
        scheduler.enqueue_due_url_rechecks(db)
        assert db.query(ScheduledJob).filter(ScheduledJob.recheck_monitor_id == mid,
                                             ScheduledJob.status == JOB_PENDING).count() == 1
        monkeypatch.setattr(vf, "fetch", _fetch(_html(host)))
        job = db.query(ScheduledJob).filter(ScheduledJob.recheck_monitor_id == mid).one()
        job.status = JOB_RUNNING
        db.commit()
        asyncio.run(runner.process_job(db, job))
        db.refresh(job)
        assert job.status == "completed"
    finally:
        db.close()
    assert _action(mid, "title_too_short").status == "verified"


def test_one_failing_page_never_stops_the_batch_and_is_retried(monkeypatch):
    host = "iso.example.com"
    client, org, user, mid, cu = _setup(host)
    _act(client, mid, cu, "title_too_short", "ready")
    db = SessionLocal()
    try:   # a second page's due action
        other = UrlAction(organization_id=org, monitor_id=mid, normalized_key=f"{host}/other", url=f"https://{host}/other",
                          code="missing_title", issue="Missing page title", status="ready_for_verification",
                          page_type="service", next_check_at=datetime.utcnow() - timedelta(minutes=1), check_priority=1e9)
        db.add(other); db.commit()
    finally:
        db.close()
    real = opt.check_page

    async def flaky(db, run, acts, now=None):
        if acts[0].normalized_key.endswith("/other"):
            raise RuntimeError("boom")
        return await real(db, run, acts, now)
    monkeypatch.setattr(opt, "check_page", flaky)
    monkeypatch.setattr(vf, "fetch", _fetch(_html(host)))
    res = _run_checks(mid)
    assert res["failed_pages"] == 1 and res["checked"] == 1
    o = _action(mid, "missing_title")
    assert o.unable_count == 1 and o.next_check_at is not None and "retried" in o.check_error
    assert _action(mid, "title_too_short").status == "verified"


def test_cancellation_and_daily_rate_limit(monkeypatch):
    host = "cancel.example.com"
    client, org, user, mid, cu = _setup(host)
    _act(client, mid, cu, "title_too_short", "ready")
    assert _act(client, mid, cu, "title_too_short", "cancel_recheck").status_code == 200
    assert _action(mid, "title_too_short").next_check_at is None
    _act(client, mid, cu, "title_too_short", "recheck")
    assert client.post(f"/monitors/{mid}/optimization/rechecks/cancel").json()["cancelled"] == 1
    db = SessionLocal()
    try:
        assert scheduler.has_active_url_recheck_job(db, mid) is None
    finally:
        db.close()
    _act(client, mid, cu, "title_too_short", "recheck")
    monkeypatch.setattr(opt, "RECHECK_DAILY_LIMIT", 0)
    res = _run_checks(mid)
    a = _action(mid, "title_too_short")
    assert res["limited"] and a.last_result is None and a.next_check_at > datetime.utcnow()


def test_stalled_recheck_batches_are_requeued():
    db = SessionLocal()
    try:
        job = ScheduledJob(recheck_monitor_id="m-stalled", organization_id="o", kind=JOB_KIND_URL_RECHECK,
                           status=JOB_RUNNING, attempts=1, max_attempts=3,
                           started_at=datetime.utcnow() - timedelta(hours=2))
        db.add(job); db.commit()
        scheduler.requeue_stalled_crawl_chunks(db)
        db.refresh(job)
        assert job.status == JOB_PENDING
        job.status, job.attempts, job.started_at = JOB_RUNNING, 3, datetime.utcnow() - timedelta(hours=2)
        db.commit()
        scheduler.requeue_stalled_crawl_chunks(db)
        db.refresh(job)
        assert job.status == JOB_FAILED
    finally:
        db.close()


# ------------------------------------------------------------------ next actions

def test_next_actions_put_blocking_issues_first_and_hold_back_repeated_failures(monkeypatch):
    host = "next.example.com"
    client, org, user, mid, cu = _setup(host, ev={**_pev(host), "noindex": True, "robots_meta": "noindex"})
    ov = client.get(f"/monitors/{mid}/optimization").json()
    assert ov["available"] and ov["counts"]["important_urls"] == 1
    assert [n["code"] for n in ov["next_actions"]] == ["noindex"]
    assert any("depend on it" in r for r in ov["next_actions"][0]["reasons"])
    _act(client, mid, cu, "noindex", "ignore", reason="Staging copy")
    codes = [n["code"] for n in client.get(f"/monitors/{mid}/optimization").json()["next_actions"]]
    assert "title_too_short" in codes and "noindex" not in codes
    # Not fixed twice on an unchanged page: held back instead of repeated.
    db = SessionLocal()
    try:
        fp = db.get(CrawlUrl, cu).content_fingerprint or "fp-same"
        db.query(CrawlUrl).filter(CrawlUrl.id == cu).update({CrawlUrl.content_fingerprint: fp})
        db.commit()
    finally:
        db.close()
    _act(client, mid, cu, "title_too_short", "ready")
    db = SessionLocal()
    try:
        a = db.query(UrlAction).filter(UrlAction.monitor_id == mid, UrlAction.code == "title_too_short").one()
        a.status, a.not_fixed_count, a.not_fixed_fingerprint = "not_fixed", 2, fp
        db.commit()
    finally:
        db.close()
    ov = client.get(f"/monitors/{mid}/optimization").json()
    assert "title_too_short" not in [n["code"] for n in ov["next_actions"]]
    assert ov["held_back"][0]["code"] == "title_too_short" and "Review the approach" in ov["held_back"][0]["reason"]
    assert ov["counts"]["not_fixed"] == 1 and ov["counts"]["ignored"] == 1


def test_url_list_filters_and_overview_without_scores():
    host = "list.example.com"
    client, org, user, mid, cu = _setup(host)
    _act(client, mid, cu, "title_too_short", "start")
    r = client.get(f"/monitors/{mid}/optimization/urls").json()
    assert r["total"] == 1
    u = r["urls"][0]
    assert u["main_issue"]["code"] and u["open_issues"] >= 2 and u["score"] is not None
    assert client.get(f"/monitors/{mid}/optimization/urls?status=in_progress").json()["total"] == 1
    assert client.get(f"/monitors/{mid}/optimization/urls?status=verified").json()["total"] == 0
    assert client.get(f"/monitors/{mid}/optimization/urls?q=nothing-like-this").json()["total"] == 0
    c2, org2, user2 = _ctx()
    mid2, _ = _site(org2, user2, host="empty.example.com", n_pages=0)
    ov = c2.get(f"/monitors/{mid2}/optimization").json()
    assert ov["available"] is False and "scored" in ov["reason"]


# ------------------------------------------------------------------ authorization

def test_other_organizations_viewers_and_free_plans(monkeypatch):
    from tests.test_rbac import _invite_and_accept
    host = "authz-opt.example.com"
    owner, org, user, mid, cu = _setup(host)
    other, _, _ = _ctx()
    assert other.get(f"/monitors/{mid}/optimization").status_code == 404
    assert other.get(f"/monitors/{mid}/optimization/urls/{cu}").status_code == 404
    assert _act(other, mid, cu, "title_too_short", "start").status_code == 404
    assert other.post(f"/monitors/{mid}/optimization/rechecks/cancel").status_code == 404
    viewer, _ = _invite_and_accept(owner, "viewer", monkeypatch)
    assert viewer.get(f"/monitors/{mid}/optimization/urls/{cu}").status_code == 200
    assert _act(viewer, mid, cu, "title_too_short", "start").status_code == 403
    monkeypatch.setattr(settings, "billing_enforced", True)
    assert owner.get(f"/monitors/{mid}/optimization").status_code == 402
