"""Contact & Support tests: validation, storage, sanitization, rate limiting,
best-effort email (no real SMTP), and the admin Support Inbox."""
import app.services.contact_service as contact_service
from app.core.cache import contact_limiter
from app.db.models import CONTACT_CLOSED, CONTACT_NEW, Contact, User
from app.db.session import SessionLocal
from app.main import app
from fastapi.testclient import TestClient
from tests.authutil import auth_client

anon = TestClient(app)


def _make_admin(user_id: str) -> None:
    db = SessionLocal()
    try:
        db.get(User, user_id).is_platform_admin = True
        db.commit()
    finally:
        db.close()


def _admin_client():
    c, body = auth_client()
    _make_admin(body["user"]["id"])
    return c


def _reset_limiter():
    contact_limiter._hits.clear()


def _clear_contacts():
    db = SessionLocal()
    try:
        db.query(Contact).delete()
        db.commit()
    finally:
        db.close()


def setup_function():
    _reset_limiter()
    _clear_contacts()


VALID = {
    "name": "Ada Lovelace",
    "email": "ada@example.com",
    "website": "https://example.com",
    "subject": "Question about scans",
    "message": "I have a question about how AI readiness scoring works on my site.",
}


def test_submit_stores_and_returns_success(monkeypatch):
    # Never touch the network: no-op the (best-effort) email delivery.
    monkeypatch.setattr(contact_service, "deliver_emails", lambda c: None)
    r = anon.post("/api/contact", json=VALID)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and "24 hours" in body["message"]

    db = SessionLocal()
    try:
        row = db.query(Contact).filter(Contact.email == "ada@example.com").one()
        assert row.status == CONTACT_NEW and row.subject == "Question about scans"
    finally:
        db.close()


def test_missing_and_bad_fields_are_rejected():
    assert anon.post("/api/contact", json={**VALID, "email": "not-an-email"}).status_code == 422
    assert anon.post("/api/contact", json={**VALID, "message": "too short"}).status_code == 422
    no_name = {k: v for k, v in VALID.items() if k != "name"}
    assert anon.post("/api/contact", json=no_name).status_code == 422


def test_header_injection_is_neutralized(monkeypatch):
    monkeypatch.setattr(contact_service, "deliver_emails", lambda c: None)
    payload = {**VALID, "email": "evil@example.com",
               "name": "Evil\nBcc: victim@x.com", "subject": "Sub\r\nInjected"}
    assert anon.post("/api/contact", json=payload).status_code == 200
    db = SessionLocal()
    try:
        row = db.query(Contact).filter(Contact.email == "evil@example.com").one()
        assert "\n" not in row.name and "\r" not in row.name
        assert "\n" not in row.subject and "\r" not in row.subject
    finally:
        db.close()


def test_rate_limited_after_budget(monkeypatch):
    monkeypatch.setattr(contact_service, "deliver_emails", lambda c: None)
    codes = [anon.post("/api/contact", json={**VALID, "email": f"u{i}@e.com"}).status_code
             for i in range(7)]
    assert codes.count(200) == 5           # contact_max_per_window default
    assert codes[-1] == 429


def test_smtp_disabled_is_best_effort(monkeypatch):
    from app.core import email_smtp
    monkeypatch.setattr(email_smtp.settings, "gmail_user", None)
    monkeypatch.setattr(email_smtp.settings, "gmail_app_password", None)
    assert email_smtp.send_email(to="x@y.com", subject="s", text_body="b") is False


# ------------------------------- admin inbox -------------------------------
def _seed(**kw):
    db = SessionLocal()
    try:
        c = Contact(name=kw.get("name", "Grace Hopper"), email=kw.get("email", "grace@example.com"),
                    subject=kw.get("subject", "Help"), message=kw.get("message", "A message body."),
                    status=kw.get("status", CONTACT_NEW))
        db.add(c); db.commit(); db.refresh(c)
        return c.id
    finally:
        db.close()


def test_admin_inbox_authz_and_listing():
    cid = _seed()
    assert anon.get("/admin/contacts").status_code == 401           # unauthenticated
    user, _ = auth_client()
    assert user.get("/admin/contacts").status_code == 403           # non-admin
    admin = _admin_client()
    data = admin.get("/admin/contacts").json()
    assert data["total"] >= 1
    assert set(data["counts"]) == {"new", "open", "closed"}
    assert any(row["id"] == cid for row in data["items"])


def test_admin_mark_resolved_and_delete():
    admin = _admin_client()
    cid = _seed(email="resolve@example.com")
    r = admin.post(f"/admin/contacts/{cid}/status", json={"status": CONTACT_CLOSED})
    assert r.status_code == 200 and r.json()["status"] == CONTACT_CLOSED

    closed = admin.get("/admin/contacts?status=closed").json()
    assert any(row["id"] == cid for row in closed["items"])

    assert admin.delete(f"/admin/contacts/{cid}").status_code == 200
    assert admin.get(f"/admin/contacts/{cid}").status_code == 404
