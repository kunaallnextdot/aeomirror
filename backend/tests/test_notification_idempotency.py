"""Minimal-safe email fix — critical-alert idempotency, flapping suppression, recipient
dedup, per-org rate limit, and the scheduler's one-active-job-per-monitor invariant.

notify_critical_alerts is driven directly with synthetic Monitor/Alert rows; the transport
is stubbed to 'sent' so the NotificationLog-based idempotency is exercised without email."""
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.security import now_utc
from app.db.models import Alert, Monitor, NotificationLog, ScheduledJob
from app.db.session import SessionLocal
from app.monitoring import notifications, scheduler
from tests.authutil import auth_client


@pytest.fixture()
def sent_calls(monkeypatch):
    """Stub the transport so a 'send' succeeds without email config, and count sends."""
    calls = []
    def fake_send(to, subject, text, html_body, *, kind):
        calls.append({"to": to, "kind": kind})
        return "sent"
    monkeypatch.setattr(notifications, "_send_email", fake_send)
    return calls


def _org_owner():
    _client, body = auth_client()
    return body["organization"]["id"], body["user"]["id"]


def _monitor(db, org_id, user_id, url="http://notify.example/"):
    m = Monitor(organization_id=org_id, user_id=user_id, url=url,
                normalized_url="notify.example", frequency="daily", status="active")
    db.add(m); db.commit(); db.refresh(m)
    return m


def _alert(db, m, atype="robots_blocked"):
    a = Alert(monitor_id=m.id, organization_id=m.organization_id, scan_id=None,
              type=atype, severity="critical", title=f"{atype}", message="msg")
    db.add(a); db.commit(); db.refresh(a)
    return a


def _crit_logs(db, org_id, status=None):
    q = db.query(NotificationLog).filter(
        NotificationLog.organization_id == org_id,
        NotificationLog.kind == notifications.CRITICAL_ALERT_KIND)
    if status:
        q = q.filter(NotificationLog.status == status)
    return q.all()


# A / B / C — first send once; repeats suppressed
def test_new_alert_sends_once_then_suppressed(sent_calls):
    org_id, user_id = _org_owner()
    db = SessionLocal()
    try:
        m = _monitor(db, org_id, user_id)
        a = _alert(db, m, "robots_blocked")
        assert notifications.notify_critical_alerts(db, m, [a]) == 1     # A: first send
        assert notifications.notify_critical_alerts(db, m, [a]) == 0     # B: same type, suppressed
        assert notifications.notify_critical_alerts(db, m, [a]) == 0     # C: still suppressed
        assert len(sent_calls) == 1
        # H: exactly one 'sent' row, the repeats recorded as skipped/duplicate
        assert len(_crit_logs(db, org_id, "sent")) == 1
        dupes = [l for l in _crit_logs(db, org_id, "skipped")
                 if (l.meta or {}).get("reason") == "critical_alert_duplicate"]
        assert len(dupes) == 2
    finally:
        db.close()


# D — resolve + reopen (prior send older than cooldown) → sends again
def test_reopen_after_cooldown_sends_again(sent_calls):
    org_id, user_id = _org_owner()
    db = SessionLocal()
    try:
        m = _monitor(db, org_id, user_id)
        a = _alert(db, m, "robots_blocked")
        assert notifications.notify_critical_alerts(db, m, [a]) == 1
        # backdate the 'sent' log beyond the 24h cooldown → treated as a new episode
        row = _crit_logs(db, org_id, "sent")[0]
        row.created_at = now_utc() - timedelta(seconds=settings.critical_alert_cooldown_seconds + 3600)
        db.commit()
        assert notifications.notify_critical_alerts(db, m, [a]) == 1     # reopened → send
        assert len(sent_calls) == 2
    finally:
        db.close()


# E — owner and monitor creator are the same person → one email
def test_identical_recipient_deduped(sent_calls):
    org_id, user_id = _org_owner()
    db = SessionLocal()
    try:
        m = _monitor(db, org_id, user_id)          # user_id == owner id
        a = _alert(db, m, "critical_issue")
        assert notifications.notify_critical_alerts(db, m, [a]) == 1
        assert len(sent_calls) == 1                 # not two copies
    finally:
        db.close()


# F — different alert types each send once in a single scan
def test_different_alert_types_each_send(sent_calls):
    org_id, user_id = _org_owner()
    db = SessionLocal()
    try:
        m = _monitor(db, org_id, user_id)
        a1 = _alert(db, m, "robots_blocked")
        a2 = _alert(db, m, "score_drop")
        # one combined email covering both fresh types
        assert notifications.notify_critical_alerts(db, m, [a1, a2]) == 1
        sent = _crit_logs(db, org_id, "sent")
        assert {(l.meta or {}).get("alert_type") for l in sent} == {"robots_blocked", "score_drop"}
        # re-firing only robots_blocked is suppressed; score_drop already covered too
        assert notifications.notify_critical_alerts(db, m, [a1, a2]) == 0
    finally:
        db.close()


# G — different monitors are independent
def test_monitors_are_independent(sent_calls):
    org_id, user_id = _org_owner()
    db = SessionLocal()
    try:
        m1 = _monitor(db, org_id, user_id, "http://a.example/")
        m2 = _monitor(db, org_id, user_id, "http://b.example/")
        a1 = _alert(db, m1, "robots_blocked")
        a2 = _alert(db, m2, "robots_blocked")
        assert notifications.notify_critical_alerts(db, m1, [a1]) == 1
        assert notifications.notify_critical_alerts(db, m2, [a2]) == 1   # not suppressed by m1
        assert len(sent_calls) == 2
    finally:
        db.close()


# J / K — per-org rate limit blocks after the cap, but never touches alerts
def test_rate_limit_blocks_but_preserves_alerts(sent_calls, monkeypatch):
    monkeypatch.setattr(settings, "critical_alert_max_per_org_per_day", 5)
    org_id, user_id = _org_owner()
    db = SessionLocal()
    try:
        m = _monitor(db, org_id, user_id)
        # 5 distinct types each send once -> 5 'sent' rows (hits the cap)
        first_five = [_alert(db, m, f"t{i}") for i in range(5)]
        for a in first_five:
            notifications.notify_critical_alerts(db, m, [a])
        assert len(_crit_logs(db, org_id, "sent")) == 5
        # a 6th, genuinely new type is now rate-limited
        a6 = _alert(db, m, "t6")
        assert notifications.notify_critical_alerts(db, m, [a6]) == 0
        limited = [l for l in _crit_logs(db, org_id, "skipped")
                   if (l.meta or {}).get("reason") == "critical_alert_rate_limited"]
        assert limited
        # K: all six Alert rows still exist, none resolved/deleted
        assert db.query(Alert).filter(Alert.monitor_id == m.id).count() == 6
        assert all(a.status == "open" for a in db.query(Alert).filter(Alert.monitor_id == m.id))
    finally:
        db.close()


# L — the DB refuses a second active job for the same monitor
def test_scheduler_unique_active_job():
    org_id, user_id = _org_owner()
    db = SessionLocal()
    try:
        m = _monitor(db, org_id, user_id)
        db.add(ScheduledJob(monitor_id=m.id, organization_id=org_id, kind="scheduled",
                            status="pending", attempts=0, max_attempts=3))
        db.commit()
        # a second ACTIVE job for the same monitor violates the partial unique index
        db.add(ScheduledJob(monitor_id=m.id, organization_id=org_id, kind="manual",
                            status="running", attempts=0, max_attempts=3))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        # a completed job for the same monitor is fine (leaves the partial predicate)
        db.add(ScheduledJob(monitor_id=m.id, organization_id=org_id, kind="scheduled",
                            status="completed", attempts=1, max_attempts=3))
        db.commit()
        active = db.query(ScheduledJob).filter(
            ScheduledJob.monitor_id == m.id,
            ScheduledJob.status.in_(("pending", "running"))).count()
        assert active == 1
    finally:
        db.close()


# L(bis) — enqueue_manual dedupes to a single active job per monitor
def test_enqueue_manual_dedupes_active_job():
    org_id, user_id = _org_owner()
    db = SessionLocal()
    try:
        m = _monitor(db, org_id, user_id)
        j1 = scheduler.enqueue_manual(db, m)
        j2 = scheduler.enqueue_manual(db, m)              # active job exists → same job back
        assert j2 is not None and j2.id == j1.id
        active = db.query(ScheduledJob).filter(
            ScheduledJob.monitor_id == m.id,
            ScheduledJob.status.in_(("pending", "running"))).count()
        assert active == 1
    finally:
        db.close()


# M — a worker re-scan (second notify with the same open alert) does not resend
def test_worker_rescan_does_not_resend(sent_calls):
    org_id, user_id = _org_owner()
    db = SessionLocal()
    try:
        m = _monitor(db, org_id, user_id)
        a = _alert(db, m, "robots_blocked")
        assert notifications.notify_critical_alerts(db, m, [a]) == 1   # first scan
        # simulate a worker restart re-scanning and re-detecting the same open alert
        assert notifications.notify_critical_alerts(db, m, [a]) == 0
        assert len(sent_calls) == 1
    finally:
        db.close()
