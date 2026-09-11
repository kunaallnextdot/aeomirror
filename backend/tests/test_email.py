"""Email tests: transport success/failure/missing-config, HTML escaping, and the
guarantee that lead storage succeeds even when the email send fails. No real emails
are ever sent — the shared SMTP transport (email_transport.send_email) is patched."""
import app.services.email_transport as email_transport
import app.services.leads as leads
from app.config import settings
from app.db.session import SessionLocal
from app.db.models import Lead


def _enable_email(monkeypatch):
    """Make settings.email_enabled True (Gmail creds present) so send_welcome_email
    reaches the transport, which we patch — no real SMTP is opened."""
    monkeypatch.setattr(settings, "gmail_user", "aeo@example.com")
    monkeypatch.setattr(settings, "gmail_app_password", "app-password")


def test_send_success(monkeypatch):
    _enable_email(monkeypatch)
    sent = {}

    def fake_send(to, subject, text, html_body, *, kind, log_prefix, headers=None):
        sent["to"] = to
        sent["log_prefix"] = log_prefix
        return "sent"

    monkeypatch.setattr(email_transport, "send_email", fake_send)
    assert leads.send_welcome_email("user@example.com", domain="example.com", ars=50) is True
    assert sent["to"] == "user@example.com"
    assert sent["log_prefix"] == "leads"


def test_send_provider_failure_returns_false(monkeypatch):
    _enable_email(monkeypatch)
    monkeypatch.setattr(email_transport, "send_email", lambda *a, **k: "failed")
    assert leads.send_welcome_email("user@example.com", domain="x.com", ars=10) is False


def test_send_raises_are_swallowed(monkeypatch):
    _enable_email(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("network down")

    # capture_lead swallows unexpected errors; send_welcome_email delegates to the transport,
    # which itself never raises — but even if a caller misbehaves the lead write is protected.
    monkeypatch.setattr(email_transport, "send_email", boom)
    db = SessionLocal()
    try:
        db.query(Lead).filter(Lead.email == "swallow@example.com").delete()
        db.commit()
        lead = leads.capture_lead(db, "swallow@example.com", "https://x.com")
        assert lead.id is not None      # no exception surfaced
    finally:
        db.close()


def test_missing_config_skips_send(monkeypatch):
    # Email is disabled only when BOTH transports are unconfigured (Resend + Gmail SMTP).
    monkeypatch.setattr(settings, "resend_api_key", None)
    monkeypatch.setattr(settings, "gmail_user", None)
    monkeypatch.setattr(settings, "gmail_app_password", None)
    called = {"n": 0}
    monkeypatch.setattr(email_transport, "send_email",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "sent")
    assert leads.send_welcome_email("user@example.com") is False
    assert called["n"] == 0  # transport never called when unconfigured


def test_html_values_are_escaped():
    subject, text, html = leads._render_welcome("<script>evil</script>", 42,
                                                [{"label": "<b>Issue</b>", "fix_hint": "do & fix"}])
    assert "<script>evil</script>" not in html   # raw injection must not appear
    assert "&lt;script&gt;" in html
    assert "&amp;" in html                        # "do & fix" escaped


def test_lead_stored_even_if_email_fails(monkeypatch):
    _enable_email(monkeypatch)
    monkeypatch.setattr(email_transport, "send_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
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
