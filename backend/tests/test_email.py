"""Email tests: provider success/failure/missing-config, HTML escaping, and the
guarantee that lead storage succeeds even when the email send fails. No real
emails are ever sent — httpx.post is always mocked."""
import app.services.leads as leads
from app.config import settings
from app.db.session import SessionLocal
from app.db.models import Lead


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


def _enable_email(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "test_key")
    monkeypatch.setattr(settings, "email_from", "AEOMirror <hi@aeomirror.com>")


def test_send_success(monkeypatch):
    _enable_email(monkeypatch)
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["to"] = json["to"]
        sent["auth"] = headers["Authorization"]
        return _Resp(200)

    monkeypatch.setattr(leads.httpx, "post", fake_post)
    assert leads.send_welcome_email("user@example.com", domain="example.com", ars=50) is True
    assert sent["to"] == ["user@example.com"]
    assert sent["auth"].startswith("Bearer ")


def test_send_provider_failure_returns_false(monkeypatch):
    _enable_email(monkeypatch)
    monkeypatch.setattr(leads.httpx, "post", lambda *a, **k: _Resp(500))
    assert leads.send_welcome_email("user@example.com", domain="x.com", ars=10) is False


def test_send_raises_are_swallowed(monkeypatch):
    _enable_email(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(leads.httpx, "post", boom)
    assert leads.send_welcome_email("user@example.com") is False  # no exception


def test_missing_config_skips_send(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", None)
    monkeypatch.setattr(settings, "email_from", None)
    called = {"n": 0}
    monkeypatch.setattr(leads.httpx, "post", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert leads.send_welcome_email("user@example.com") is False
    assert called["n"] == 0  # provider never called when unconfigured


def test_html_values_are_escaped():
    subject, text, html = leads._render_welcome("<script>evil</script>", 42,
                                                [{"label": "<b>Issue</b>", "fix_hint": "do & fix"}])
    assert "<script>evil</script>" not in html   # raw injection must not appear
    assert "&lt;script&gt;" in html
    assert "&amp;" in html                        # "do & fix" escaped


def test_lead_stored_even_if_email_fails(monkeypatch):
    _enable_email(monkeypatch)
    monkeypatch.setattr(leads.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    db = SessionLocal()
    try:
        email = "resilient-lead@example.com"
        db.query(Lead).filter(Lead.email == email).delete()
        db.commit()
        lead = leads.capture_lead(db, email, "https://example.com")
        assert lead.id is not None
        assert db.query(Lead).filter(Lead.email == email).first() is not None
    finally:
        db.close()
