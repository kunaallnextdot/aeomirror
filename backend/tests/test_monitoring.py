"""Phase 7 monitoring tests: monitors CRUD, scheduler (enqueue/dedupe/claim/retry),
recurring scans, change detection, alert generation, history/trends, notifications,
and org scoping."""
import asyncio
from datetime import datetime, timedelta

import app.api.routes_scan as rs
import app.monitoring.notifications as notifications
from app.db.models import (
    JOB_FAILED, JOB_PENDING, Alert, Monitor, MonitorHistory, NotificationLog,
    ScheduledJob,
)
from app.db.session import SessionLocal
from app.main import app
from app.monitoring import scheduler, worker
from app.scanner.models import PageBundle
from tests.authutil import auth_client

# ------------------------------- fetch fixtures -------------------------------
GOOD_HTML = """<!doctype html><html lang=en><head>
<title>Acme Robotics — Industrial Automation Systems</title>
<meta name=description content="Acme builds industrial automation and robotics for factories.">
<link rel=canonical href="http://acme.example/">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"Acme"}</script>
</head><body><main><h1>Acme Robotics</h1>
<p>Acme Robotics builds industrial automation systems for factories worldwide, improving throughput and safety across production lines.</p>
<h2>Products</h2><p>Robotic arms, conveyors and control software for modern manufacturing.</p>
<img src=a.png alt="A robotic arm on a production line">
<a href="/products">Products</a> <a href="/about">About</a>
<time datetime="2026-07-01">Updated July 2026</time></main></body></html>"""

GOOD_ROBOTS = "User-agent: *\nAllow: /\nSitemap: http://acme.example/sitemap.xml\n"
GOOD_SITEMAP = "<?xml version='1.0'?><urlset><url><loc>http://acme.example/</loc></url></urlset>"

# Degraded: GPTBot blocked, no schema, no sitemap, no meta description/canonical.
BAD_HTML = """<!doctype html><html><head><title>Acme</title></head>
<body><div>Acme</div></body></html>"""
BAD_ROBOTS = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"


def _bundle(url, html, robots, sitemap_present, sitemap_xml, headers):
    return PageBundle(url=url, html=html, robots_txt=robots,
                      llms_txt_present=False, sitemap_present=sitemap_present,
                      sitemap_xml=sitemap_xml, headers=headers)


def good_bundle(url):
    return _bundle(url, GOOD_HTML, GOOD_ROBOTS, True, GOOD_SITEMAP,
                   {"content-encoding": "gzip", "cache-control": "max-age=600"})


def bad_bundle(url):
    return _bundle(url, BAD_HTML, BAD_ROBOTS, False, "", {})


def set_fetch(monkeypatch, bundle_fn):
    async def _f(url):
        return bundle_fn(url)
    monkeypatch.setattr(rs, "fetch", _f)


# ------------------------------- monitors CRUD -------------------------------
def test_create_list_update_delete_monitor(monkeypatch):
    client, _ = auth_client(organization_name="Mon Org")
    r = client.post("/monitors", json={"url": "http://acme.example/", "frequency": "weekly", "name": "Acme"})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert r.json()["status"] == "active" and r.json()["frequency"] == "weekly"

    listed = client.get("/monitors").json()
    assert listed["counts"]["total"] == 1 and listed["counts"]["active"] == 1
    assert any(m["id"] == mid for m in listed["monitors"])

    up = client.patch(f"/monitors/{mid}", json={"status": "paused"})
    assert up.status_code == 200 and up.json()["status"] == "paused"
    assert up.json()["next_scan_at"] is None

    d = client.delete(f"/monitors/{mid}")
    assert d.status_code == 200
    assert client.get(f"/monitors/{mid}").status_code == 404


def test_invalid_frequency_rejected():
    client, _ = auth_client()
    assert client.post("/monitors", json={"url": "http://x.example/", "frequency": "hourly"}).status_code == 422


def test_free_monitor_quota_enforced(monkeypatch):
    """Free plan includes 1 monitor; the 2nd is blocked with 402. Pro allows 10.
    (Enforcement only applies when billing is enforced — the default test config
    bypasses it, so the other monitoring tests can create freely.)"""
    from app.config import settings

    monkeypatch.setattr(settings, "billing_enforced", True)
    client, _ = auth_client()

    assert client.post("/monitors", json={"url": "http://m1.example/", "frequency": "manual"}).status_code == 201
    second = client.post("/monitors", json={"url": "http://m2.example/", "frequency": "manual"})
    assert second.status_code == 402
    assert "1 monitor" in second.json()["detail"]

    # Upgrade to Pro -> 10 monitors (the 2nd now succeeds).
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})
    assert client.post("/monitors", json={"url": "http://m2.example/", "frequency": "manual"}).status_code == 201


def test_monitor_scoped_to_org(monkeypatch):
    a, _ = auth_client()
    b, _ = auth_client()
    mid = a.post("/monitors", json={"url": "http://a-only.example/", "frequency": "manual"}).json()["id"]
    assert b.get(f"/monitors/{mid}").status_code == 404
    assert b.post(f"/monitors/{mid}/run").status_code == 404
    assert b.delete(f"/monitors/{mid}").status_code == 404


# ------------------------------- manual run + history -------------------------------
def test_manual_run_records_history_and_score(monkeypatch):
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()
    mid = client.post("/monitors", json={"url": "http://run.example/", "frequency": "manual"}).json()["id"]
    run = client.post(f"/monitors/{mid}/run")
    assert run.status_code == 200
    body = run.json()
    assert body["job_status"] == "completed"
    assert body["monitor"]["latest_score"] is not None
    assert body["latest"]["overall_score"] is not None
    hist = client.get(f"/history/{mid}").json()
    assert len(hist["history"]) == 1
    assert hist["history"][0]["changes"]["first_scan"] is True


# ------------------------------- scheduler -------------------------------
def test_enqueue_due_and_dedupe(monkeypatch):
    client, _ = auth_client()
    # weekly monitor is due immediately on creation (next_scan_at = now)
    client.post("/monitors", json={"url": "http://due.example/", "frequency": "daily"})
    db = SessionLocal()
    try:
        created = scheduler.enqueue_due(db)
        assert created >= 1
        # calling again does not duplicate (active job exists + next_scan advanced)
        assert scheduler.enqueue_due(db) == 0
        pend = db.query(ScheduledJob).filter(ScheduledJob.status == JOB_PENDING).count()
        assert pend >= 1
    finally:
        db.close()


def test_paused_monitor_not_enqueued(monkeypatch):
    client, _ = auth_client()
    mid = client.post("/monitors", json={"url": "http://paused.example/", "frequency": "daily"}).json()["id"]
    client.patch(f"/monitors/{mid}", json={"status": "paused"})
    db = SessionLocal()
    try:
        m = db.get(Monitor, mid)
        # a paused monitor has no next_scan_at and is skipped
        assert m.next_scan_at is None
    finally:
        db.close()


def test_worker_tick_runs_due_monitor(monkeypatch):
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()
    mid = client.post("/monitors", json={"url": "http://tick.example/", "frequency": "daily"}).json()["id"]
    res = asyncio.run(worker.tick())
    assert res["processed"] >= 1
    m = client.get(f"/monitors/{mid}").json()["monitor"]
    assert m["latest_score"] is not None
    assert m["history_count"] >= 1


# ------------------------------- change detection + alerts -------------------------------
def test_change_detection_and_alerts(monkeypatch):
    client, _ = auth_client(organization_name="Alert Org")
    mid = client.post("/monitors", json={"url": "http://change.example/", "frequency": "manual"}).json()["id"]

    set_fetch(monkeypatch, good_bundle)
    client.post(f"/monitors/{mid}/run")     # baseline (healthy)
    good_score = client.get(f"/monitors/{mid}").json()["monitor"]["latest_score"]

    set_fetch(monkeypatch, bad_bundle)
    client.post(f"/monitors/{mid}/run")     # degraded -> changes + alerts
    bad_score = client.get(f"/monitors/{mid}").json()["monitor"]["latest_score"]
    assert bad_score < good_score

    detail = client.get(f"/monitors/{mid}").json()
    assert detail["latest_changes"]["first_scan"] is False
    assert detail["latest_changes"]["overall"]["delta"] < 0
    assert detail["trends"]["regressions"]

    alerts = client.get("/alerts").json()["alerts"]
    types = {a["type"] for a in alerts}
    # dropping schema, sitemap, and blocking GPTBot must raise these
    assert "robots_blocked" in types
    assert "schema_removed" in types
    assert "sitemap_removed" in types
    assert any(a["severity"] == "critical" for a in alerts)


def test_acknowledge_alert(monkeypatch):
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()
    mid = client.post("/monitors", json={"url": "http://ack.example/", "frequency": "manual"}).json()["id"]
    client.post(f"/monitors/{mid}/run")
    set_fetch(monkeypatch, bad_bundle)
    client.post(f"/monitors/{mid}/run")
    alerts = client.get("/alerts", params={"status": "open"}).json()["alerts"]
    assert alerts
    aid = alerts[0]["id"]
    assert client.post(f"/alerts/{aid}/acknowledge").status_code == 200
    still_open = [a["id"] for a in client.get("/alerts", params={"status": "open"}).json()["alerts"]]
    assert aid not in still_open


# ------------------------------- history + trends -------------------------------
def test_history_and_trends(monkeypatch):
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()
    mid = client.post("/monitors", json={"url": "http://trend.example/", "frequency": "manual"}).json()["id"]
    client.post(f"/monitors/{mid}/run")
    set_fetch(monkeypatch, bad_bundle)
    client.post(f"/monitors/{mid}/run")

    h = client.get(f"/history/{mid}").json()
    assert len(h["history"]) == 2
    tr = h["trends"]
    assert len(tr["score_series"]) == 2
    assert len(tr["issue_series"]) == 2
    assert tr["category_series"]           # per-signal series present
    assert tr["regressions"]               # degradation captured
    # trend indicator on the monitor should read "down"
    assert client.get(f"/monitors/{mid}").json()["monitor"]["trend"] == "down"


# ------------------------------- retries -------------------------------
def test_fail_job_retries_then_fails():
    db = SessionLocal()
    try:
        job = ScheduledJob(monitor_id="m", organization_id="o", status="running",
                           attempts=1, max_attempts=3)
        db.add(job); db.commit(); db.refresh(job)
        scheduler.fail_job(db, job, "boom")
        assert job.status == JOB_PENDING and job.run_after is not None   # retry scheduled
        job.attempts = 3
        db.commit()
        scheduler.fail_job(db, job, "boom again")
        assert job.status == JOB_FAILED                                  # exhausted
    finally:
        db.close()


def test_process_job_failure_is_retried(monkeypatch):
    async def _boom(url):
        raise RuntimeError("fetch down")
    monkeypatch.setattr(rs, "fetch", _boom)
    client, _ = auth_client()
    mid = client.post("/monitors", json={"url": "http://fail.example/", "frequency": "manual"}).json()["id"]
    db = SessionLocal()
    try:
        m = db.get(Monitor, mid)
        scheduler.enqueue_manual(db, m)
        claimed = scheduler.claim_job(db)
        from app.monitoring import runner
        asyncio.run(runner.process_job(db, claimed))
        db.refresh(claimed)
        assert claimed.attempts == 1
        assert claimed.status == JOB_PENDING       # scheduled for retry
        assert claimed.error
    finally:
        db.close()


# ------------------------------- notifications -------------------------------
def test_critical_alert_notification_logged(monkeypatch):
    # email not configured in tests -> status "skipped", but still logged
    set_fetch(monkeypatch, good_bundle)
    client, body = auth_client()
    mid = client.post("/monitors", json={"url": "http://notify.example/", "frequency": "manual"}).json()["id"]
    client.post(f"/monitors/{mid}/run")
    set_fetch(monkeypatch, bad_bundle)
    client.post(f"/monitors/{mid}/run")
    db = SessionLocal()
    try:
        org_id = body["organization"]["id"]
        logs = (db.query(NotificationLog)
                .filter(NotificationLog.organization_id == org_id,
                        NotificationLog.kind == "critical_alert").all())
        assert logs                                   # a critical-alert email was attempted
        assert all(l.status in ("sent", "skipped", "failed") for l in logs)
    finally:
        db.close()


def test_weekly_summary_builds_and_logs(monkeypatch):
    set_fetch(monkeypatch, good_bundle)
    client, body = auth_client()
    mid = client.post("/monitors", json={"url": "http://weekly.example/", "frequency": "manual"}).json()["id"]
    client.post(f"/monitors/{mid}/run")
    db = SessionLocal()
    try:
        org_id = body["organization"]["id"]
        assert notifications.send_weekly_summary(db, org_id) is True
        log = (db.query(NotificationLog)
               .filter(NotificationLog.organization_id == org_id,
                       NotificationLog.kind == "weekly_summary").first())
        assert log is not None
    finally:
        db.close()


def test_maybe_send_summaries_baseline_then_send(monkeypatch):
    set_fetch(monkeypatch, good_bundle)
    client, body = auth_client()
    client.post("/monitors", json={"url": "http://baseline.example/", "frequency": "manual"})
    db = SessionLocal()
    try:
        org_id = body["organization"]["id"]
        # C1: the legacy weekly summary applies only to orgs WITHOUT a digest-enabled
        # monitor, so this org opts its monitor out of the digest to exercise that path.
        from app.db.models import Monitor
        db.query(Monitor).filter(Monitor.organization_id == org_id).update(
            {Monitor.digest_enabled: False})
        db.commit()
        # first pass only records a baseline (no immediate summary)
        c1 = notifications.maybe_send_summaries(db)
        assert c1["weekly"] == 0
        base = (db.query(NotificationLog)
                .filter(NotificationLog.organization_id == org_id,
                        NotificationLog.kind == "weekly_summary").all())
        assert base and base[0].meta.get("baseline") is True
        # backdate the baseline > 7 days and re-run -> a weekly summary sends
        base[0].created_at = datetime.utcnow() - timedelta(days=8)
        db.commit()
        c2 = notifications.maybe_send_summaries(db)
        assert c2["weekly"] == 1
    finally:
        db.close()
