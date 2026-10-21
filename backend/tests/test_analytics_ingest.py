"""Session upsert, pageview idempotency, and conversion dedup/verification-upgrade
(app/services/analytics/ingest.py) — the core correctness guarantees: first-touch
attribution is never overwritten, duplicate events are never double-counted, and a
server-confirmed conversion upgrades (never duplicates) a client-reported one."""
from datetime import datetime, timedelta

from app.db.models import AnalyticsConversion, AnalyticsPageview, AnalyticsSession, Monitor
from app.db.session import SessionLocal
from app.services.analytics import ingest
from tests.authutil import auth_client


def _make_monitor(org_id: str, user_id: str) -> str:
    db = SessionLocal()
    try:
        m = Monitor(organization_id=org_id, user_id=user_id, url="https://example.com/",
                    normalized_url="example.com", frequency="manual", status="active")
        db.add(m)
        db.commit()
        db.refresh(m)
        return m.id
    finally:
        db.close()


def test_session_created_once_and_first_touch_is_never_overwritten():
    client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    db = SessionLocal()
    try:
        monitor = db.get(Monitor, monitor_id)
        s1 = ingest.get_or_create_session(
            db, monitor=monitor, session_id="sess-1", visitor_id="visitor-1",
            landing_url="https://example.com/pricing", referrer="https://chat.openai.com/c/x",
            utm_source=None, utm_medium=None, utm_campaign=None,
        )
        assert s1.channel == "chatgpt"
        first_id = s1.id

        # Second pageview in the SAME session, now with NO referrer (typical for
        # internal navigation) — must NOT overwrite the session's first-touch channel.
        s2 = ingest.get_or_create_session(
            db, monitor=monitor, session_id="sess-1", visitor_id="visitor-1",
            landing_url="https://example.com/about", referrer=None,
            utm_source=None, utm_medium=None, utm_campaign=None,
        )
        assert s2.id == first_id
        assert s2.channel == "chatgpt"   # unchanged
        assert s2.landing_url == "https://example.com/pricing"   # unchanged (first landing page)

        db.commit()
        rows = db.query(AnalyticsSession).filter_by(monitor_id=monitor_id, session_id="sess-1").all()
        assert len(rows) == 1   # never a duplicate session row
    finally:
        db.close()


def test_returning_visitor_detected_on_a_new_session():
    client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    db = SessionLocal()
    try:
        monitor = db.get(Monitor, monitor_id)
        s1 = ingest.get_or_create_session(
            db, monitor=monitor, session_id="sess-a", visitor_id="visitor-x",
            landing_url="https://example.com/", referrer=None,
            utm_source=None, utm_medium=None, utm_campaign=None,
        )
        assert s1.is_returning is False
        db.commit()

        s2 = ingest.get_or_create_session(
            db, monitor=monitor, session_id="sess-b", visitor_id="visitor-x",
            landing_url="https://example.com/", referrer=None,
            utm_source=None, utm_medium=None, utm_campaign=None,
        )
        assert s2.is_returning is True
    finally:
        db.close()


def test_pageview_idempotent_on_event_id():
    client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    db = SessionLocal()
    try:
        monitor = db.get(Monitor, monitor_id)
        session = ingest.get_or_create_session(
            db, monitor=monitor, session_id="sess-pv", visitor_id="v1",
            landing_url="https://example.com/", referrer=None,
            utm_source=None, utm_medium=None, utm_campaign=None,
        )
        db.commit()

        first = ingest.record_pageview(db, monitor=monitor, session=session,
                                       event_id="evt-1", url="https://example.com/")
        db.commit()
        # A retried beacon with the SAME event_id must not be recorded twice.
        second = ingest.record_pageview(db, monitor=monitor, session=session,
                                        event_id="evt-1", url="https://example.com/")
        db.commit()
        assert first is True
        assert second is False
        rows = db.query(AnalyticsPageview).filter_by(monitor_id=monitor_id, event_id="evt-1").all()
        assert len(rows) == 1
    finally:
        db.close()


def test_conversion_deduplicated_on_external_id_and_upgraded_to_verified():
    client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    db = SessionLocal()
    try:
        monitor = db.get(Monitor, monitor_id)
        # Client-reported first (unverified).
        c1 = ingest.record_conversion(
            db, monitor=monitor, session=None, event_type="purchase", event_name=None,
            external_id="order-123", value_cents=None, currency=None, verified=False,
        )
        db.commit()
        assert c1.verified is False

        # The real server-to-server confirmation for the SAME order id.
        c2 = ingest.record_conversion(
            db, monitor=monitor, session=None, event_type="purchase", event_name=None,
            external_id="order-123", value_cents=4900, currency="USD", verified=True,
        )
        db.commit()
        assert c2.id == c1.id   # same row, upgraded — never a duplicate
        assert c2.verified is True
        assert c2.value_cents == 4900

        rows = db.query(AnalyticsConversion).filter_by(monitor_id=monitor_id,
                                                        event_type="purchase",
                                                        external_id="order-123").all()
        assert len(rows) == 1
    finally:
        db.close()


def test_an_already_verified_conversion_is_not_downgraded_by_a_later_client_report():
    client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    db = SessionLocal()
    try:
        monitor = db.get(Monitor, monitor_id)
        ingest.record_conversion(db, monitor=monitor, session=None, event_type="purchase",
                                 event_name=None, external_id="order-9", value_cents=1000,
                                 currency="USD", verified=True)
        db.commit()
        c2 = ingest.record_conversion(db, monitor=monitor, session=None, event_type="purchase",
                                      event_name=None, external_id="order-9", value_cents=None,
                                      currency=None, verified=False)
        db.commit()
        assert c2.verified is True   # still verified
        assert c2.value_cents == 1000   # not wiped by the later unverified call
    finally:
        db.close()
