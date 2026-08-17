"""Weekly digest (Part B): content assembly, skip-when-no-data, console backend,
send-failure isolation, unsubscribe (GET safe / POST opts out / invalid 404), per-org
batching isolation, and the C1 weekly-summary suppression. No real network/email."""
from datetime import datetime, timedelta

import app.monitoring.notifications as notif
from app.db.models import Monitor, MonitorHistory, NotificationLog, Organization, Scan, User
from app.db.session import SessionLocal
from app.main import app
from app.services import digest as dg
from app.services import email_backend
from fastapi.testclient import TestClient
from tests.authutil import auth_client

anon = TestClient(app)
MONDAY_1PM = datetime(2026, 1, 5, 13, 0, 0)     # weekday()==0 (Monday), hour 13 == send window


# ------------------------------- fixtures -------------------------------
def _scan(db, org, result):
    s = Scan(url="https://d.test/", normalized_url="d.test", ars=50, rubric_version="t",
             result=result, organization_id=org)
    db.add(s); db.commit(); db.refresh(s)
    return s


def _monitor(db, org, *, latest_scan_id=None, latest_score=None, digest_enabled=True):
    m = Monitor(organization_id=org, url="https://d.test/", normalized_url="d.test",
                status="active", frequency="weekly", digest_enabled=digest_enabled,
                latest_scan_id=latest_scan_id, latest_score=latest_score)
    db.add(m); db.commit(); db.refresh(m)
    return m


def _history(db, m, scan_id, *, score, when):
    h = MonitorHistory(monitor_id=m.id, organization_id=m.organization_id, scan_id=scan_id,
                       overall_score=score, status="pass", issue_count=1, scores={}, changes={},
                       created_at=when)
    db.add(h); db.commit()
    return h


# ------------------------------- content assembly -------------------------------
def test_digest_content_assembly_mixed_severities():
    _, obody = auth_client()
    org = obody["organization"]["id"]
    now = MONDAY_1PM
    db = SessionLocal()
    try:
        result = {
            "overall_score": 74,
            "crawler_access": {"findings": [
                {"status": "blocked_by_robots", "severity": "CRITICAL", "bot_name": "GPTBot",
                 "cause": "Disallowed.", "remediation": "Edit robots.txt for GPTBot."},
                {"status": "blocked_by_server", "severity": "WARNING", "bot_name": "CCBot",
                 "cause": "HTTP 403.", "remediation": "Allowlist CCBot in the WAF."},
            ]},
            "sections": [
                {"id": "schema", "label": "Structured Data", "status": "fail",
                 "recommendations": ["Add JSON-LD."]},
                {"id": "meta", "label": "Metadata", "status": "warn",
                 "recommendations": ["Add a meta description."]},
                {"id": "robots", "label": "robots.txt", "status": "pass", "recommendations": []},
            ],
            "change_set": [{"severity": "CRITICAL"}, {"severity": "WARNING"}, {"severity": "INFO"}],
        }
        s = _scan(db, org, result)
        m = _monitor(db, org, latest_scan_id=s.id, latest_score=74)
        _history(db, m, s.id, score=74, when=now - timedelta(days=1))            # this week
        old = _scan(db, org, {"overall_score": 80})
        _history(db, m, old.id, score=80, when=now - timedelta(days=8))          # baseline last week
        d = dg.build_digest(db, org, now=now)
    finally:
        db.close()

    assert d is not None
    b = d["monitors"][0]
    assert b["score"] == 74 and b["delta"] == -6 and b["direction"] == "down"
    assert b["change_summary"] == {"CRITICAL": 1, "WARNING": 1, "INFO": 1}
    assert [c["name"] for c in b["blocked_crawlers"]] == ["GPTBot", "CCBot"]     # critical first
    assert b["top_fixes"][0]["severity"] == "CRITICAL" and len(b["top_fixes"]) == 3

    subject, text, html = dg.render_digest(d, unsubscribe_url="http://x/unsub")
    assert "BLOCKED AI CRAWLERS" in text and "GPTBot" in text                   # called out prominently
    assert "unsub" in html


def test_digest_skipped_when_no_data():
    _, obody = auth_client()
    org = obody["organization"]["id"]
    db = SessionLocal()
    try:
        _monitor(db, org, latest_scan_id=None)          # monitor, but never scanned
        assert dg.build_digest(db, org) is None
        assert dg.send_digest_for_org(db, org) is False
        n = (db.query(NotificationLog)
             .filter(NotificationLog.organization_id == org,
                     NotificationLog.kind == "weekly_digest").count())
        assert n == 0                                    # empty digest never sent/logged
    finally:
        db.close()


# ------------------------------- email backend -------------------------------
def test_console_backend_renders_without_sending(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the transport must not be called from the console backend")
    monkeypatch.setattr("app.services.email_backend.email_transport.send_email", boom)
    res = email_backend.send(to="x@y.z", subject="s", text="t", html="<p>t</p>", backend="console")
    assert res["backend"] == "console" and res["status"] == "logged"


def test_smtp_backend_reports_transport_status(monkeypatch):
    # email_backend delegates to the shared SMTP transport and returns its status verbatim,
    # forwarding the List-Unsubscribe headers. The transport itself never raises.
    seen = {}

    def fake_send(to, subject, text, html_body, *, kind, log_prefix, headers=None):
        seen["headers"] = headers
        seen["log_prefix"] = log_prefix
        return "failed"
    monkeypatch.setattr("app.services.email_backend.email_transport.send_email", fake_send)
    res = email_backend.send(to="x@y.z", subject="s", text="t", html="h",
                             headers={"List-Unsubscribe": "<u>"}, backend="smtp")
    assert res == {"backend": "smtp", "status": "failed"}
    assert seen["headers"] == {"List-Unsubscribe": "<u>"}   # unsubscribe header preserved
    assert seen["log_prefix"] == "digest"


def test_send_digest_never_raises_on_backend_error(monkeypatch):
    _, obody = auth_client()
    org = obody["organization"]["id"]
    db = SessionLocal()
    try:
        s = _scan(db, org, {"overall_score": 80, "sections": [], "change_set": []})
        m = _monitor(db, org, latest_scan_id=s.id, latest_score=80)
        _history(db, m, s.id, score=80, when=datetime.utcnow() - timedelta(days=1))

        def explode(**k):
            raise RuntimeError("provider exploded")
        monkeypatch.setattr("app.services.digest.email_backend.send", explode)
        assert dg.send_digest_for_org(db, org) is False    # swallowed, no exception
    finally:
        db.close()


# ------------------------------- unsubscribe -------------------------------
def _owner(db, org_id) -> User:
    return db.get(User, db.get(Organization, org_id).owner_id)


def test_unsubscribe_get_is_safe_post_opts_out():
    _, obody = auth_client()
    org = obody["organization"]["id"]
    db = SessionLocal()
    try:
        token = dg.unsubscribe_token(db, _owner(db, org))
    finally:
        db.close()

    r = anon.get(f"/digest/unsubscribe/{token}")           # GET renders, must NOT mutate
    assert r.status_code == 200 and "unsubscribe" in r.text.lower()
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.digest_unsubscribe_token == token).first().digest_opt_out is False
    finally:
        db.close()

    assert anon.post(f"/digest/unsubscribe/{token}").status_code == 200   # POST opts out
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.digest_unsubscribe_token == token).first().digest_opt_out is True
    finally:
        db.close()


def test_unsubscribe_invalid_token_404_no_leak():
    assert anon.get("/digest/unsubscribe/definitely-not-real").status_code == 404
    assert anon.post("/digest/unsubscribe/definitely-not-real").status_code == 404


def test_optout_recipient_excluded_from_send():
    _, obody = auth_client()
    org = obody["organization"]["id"]
    db = SessionLocal()
    try:
        s = _scan(db, org, {"overall_score": 80, "sections": [], "change_set": []})
        m = _monitor(db, org, latest_scan_id=s.id, latest_score=80)
        _history(db, m, s.id, score=80, when=datetime.utcnow() - timedelta(days=1))
        owner = _owner(db, org)
        owner.digest_opt_out = True
        db.commit()
        assert dg.send_digest_for_org(db, org) is False    # has data, but the only recipient opted out
    finally:
        db.close()


# ------------------------------- scheduling / batching / C1 -------------------------------
def test_outside_send_window_no_digests():
    tuesday = datetime(2026, 1, 6, 13, 0, 0)               # weekday()==1
    assert dg.maybe_send_digests(SessionLocal(), now=tuesday) == {"digests": 0}


def test_batching_isolates_per_org_failures(monkeypatch):
    _, abody = auth_client()
    _, bbody = auth_client()
    orga, orgb = abody["organization"]["id"], bbody["organization"]["id"]
    db = SessionLocal()
    try:
        _monitor(db, orga); _monitor(db, orgb)             # both digest-enabled
    finally:
        db.close()

    ok = {"n": 0}

    def fake_send(db, org_id, *, now=None):
        if org_id == orga:
            raise RuntimeError("org A blows up")           # one org fails hard
        ok["n"] += 1
        return True
    monkeypatch.setattr("app.services.digest.send_digest_for_org", fake_send)
    monkeypatch.setattr("app.services.digest._sent_this_week", lambda db, o, now: False)

    dg.maybe_send_digests(SessionLocal(), now=MONDAY_1PM)  # must not raise despite org A
    assert ok["n"] >= 1                                    # org B (and others) still processed


def test_c1_weekly_summary_suppressed_for_digest_orgs(monkeypatch):
    _, abody = auth_client()
    _, bbody = auth_client()
    orga, orgb = abody["organization"]["id"], bbody["organization"]["id"]
    db = SessionLocal()
    try:
        _monitor(db, orga, digest_enabled=True)            # org A: digest active
        _monitor(db, orgb, digest_enabled=False)           # org B: no digest monitor
        # seed an old weekly_summary baseline so both orgs are "due" for the weekly summary
        for o in (orga, orgb):
            db.add(NotificationLog(organization_id=o, kind="weekly_summary", channel="email",
                                   status="skipped", created_at=datetime.utcnow() - timedelta(days=8)))
        db.commit()
    finally:
        db.close()

    sent = []
    monkeypatch.setattr(notif, "send_weekly_summary", lambda db, org_id: sent.append(org_id) or True)
    monkeypatch.setattr(notif, "send_monthly_summary", lambda db, org_id: True)
    notif.maybe_send_summaries(SessionLocal())

    assert orgb in sent            # org B (no digest) still gets the legacy weekly summary
    assert orga not in sent        # org A (digest active) is suppressed — never both
