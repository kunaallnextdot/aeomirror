"""P0-2 regression: an ADMIN_EMAILS address grants platform admin only to an account
that has verified it. Registering with, or changing PATCH /me to, an unclaimed
allowlisted address must not grant admin — including by replaying a verification
link that was issued for the account's previous address."""
import pytest
from fastapi.testclient import TestClient

from app.api import routes_auth
from app.config import settings
from app.db.models import User
from app.db.session import SessionLocal
from app.main import app
from tests.authutil import register, unique_email


@pytest.fixture
def mail(monkeypatch):
    """Capture verification links instead of sending them: {email: [token, ...]}."""
    box: dict[str, list[str]] = {}
    monkeypatch.setattr(routes_auth, "send_verification_email",
                        lambda email, token, *a, **k: box.setdefault(email, []).append(token) or True)
    return box


@pytest.fixture
def admin_email(monkeypatch):
    email = unique_email("platform-admin")
    monkeypatch.setattr(settings, "admin_emails", f"someone-else@example.com, {email.upper()}")
    return email


def _client(email):
    c = TestClient(app)
    r = register(c, email=email)
    assert r.status_code == 201, r.text
    c.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return c


def _is_admin(c):
    return c.get("/me").json()["user"]["is_platform_admin"], c.get("/admin/users").status_code


def test_registering_with_an_unverified_allowlisted_email_is_not_admin(mail, admin_email):
    c = _client(admin_email)
    assert _is_admin(c) == (False, 403)


def test_the_owner_of_the_allowlisted_address_becomes_admin_once_verified(mail, admin_email):
    c = _client(admin_email)
    assert c.post("/auth/verify-email", json={"token": mail[admin_email][-1]}).status_code == 200
    assert _is_admin(c) == (True, 200)


def test_changing_email_to_an_allowlisted_address_does_not_grant_admin(mail, admin_email):
    c = _client(unique_email("attacker"))
    assert _is_admin(c) == (False, 403)
    r = c.patch("/me", json={"email": admin_email})
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is False
    assert _is_admin(c) == (False, 403)


def test_a_link_issued_for_the_previous_address_cannot_verify_the_new_one(mail, admin_email):
    own = unique_email("attacker")
    c = _client(own)
    held_back = mail[own][-1]                      # registration link, deliberately not used yet
    c.patch("/me", json={"email": admin_email})
    r = c.post("/auth/verify-email", json={"token": held_back})
    assert r.status_code == 400
    assert _is_admin(c) == (False, 403)
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.email == admin_email).one().email_verified is False
    finally:
        db.close()


def test_the_link_sent_to_the_new_address_still_verifies_it(mail):
    c = _client(unique_email("mover"))
    new = unique_email("moved")
    c.patch("/me", json={"email": new})
    assert c.post("/auth/verify-email", json={"token": mail[new][-1]}).status_code == 200
    assert c.get("/me").json()["user"]["email_verified"] is True


def test_a_client_supplied_email_verified_flag_is_ignored(mail, admin_email):
    c = _client(admin_email)
    c.patch("/me", json={"email_verified": True, "is_platform_admin": True})
    assert c.get("/me").json()["user"]["email_verified"] is False
    assert _is_admin(c) == (False, 403)


def test_db_flag_admins_keep_access_and_anonymous_callers_are_refused(mail):
    c = _client(unique_email("flagged"))
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == c.get("/me").json()["user"]["id"]).one()
        u.is_platform_admin = True
        db.commit()
    finally:
        db.close()
    assert _is_admin(c) == (True, 200)
    assert TestClient(app).get("/admin/users").status_code == 401
