"""Phase 5 authentication tests: register, login, logout, refresh rotation,
password reset, email verification, profile updates, and session management."""
from fastapi.testclient import TestClient

import app.api.routes_auth as ra
from app.config import settings
from app.main import app
from tests.authutil import DEFAULT_PASSWORD, auth_client, register, unique_email

COOKIE = settings.auth_cookie_name


def _capture(monkeypatch, attr: str) -> dict:
    """Patch an auth-email sender in routes_auth to capture the raw token."""
    box: dict = {}

    def fake(email, token, *args, **kwargs):
        box["email"], box["token"] = email, token
        return True

    monkeypatch.setattr(ra, attr, fake)
    return box


# ------------------------------- registration -------------------------------
def test_register_creates_owner_and_org():
    client, body = auth_client(name="Ada", organization_name="Acme")
    assert body["access_token"] and body["token_type"] == "bearer"
    assert body["user"]["role"] == "owner"
    assert body["user"]["email_verified"] is False
    assert body["organization"]["name"] == "Acme"
    me = client.get("/me").json()
    assert me["role"] == "owner"
    assert me["organization"]["slug"]
    # refresh cookie set, httpOnly
    assert COOKIE in client.cookies


def test_register_duplicate_email_conflicts():
    email = unique_email()
    c = TestClient(app)
    assert register(c, email=email).status_code == 201
    assert register(c, email=email).status_code == 409


def test_register_rejects_weak_password():
    c = TestClient(app)
    r = c.post("/auth/register", json={"name": "X", "email": unique_email(), "password": "short"})
    assert r.status_code == 422


# ------------------------------- login -------------------------------
def test_login_success_and_wrong_password():
    email = unique_email()
    c = TestClient(app)
    register(c, email=email)
    ok = c.post("/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    assert ok.status_code == 200 and ok.json()["access_token"]
    bad = c.post("/auth/login", json={"email": email, "password": "WrongPass123"})
    assert bad.status_code == 401
    # unknown email is the same 401 (no user enumeration)
    assert c.post("/auth/login", json={"email": unique_email(), "password": "whatever12"}).status_code == 401


def test_login_rate_limited_after_repeated_failures():
    email = unique_email()
    c = TestClient(app)
    register(c, email=email)
    statuses = [c.post("/auth/login", json={"email": email, "password": "WrongPass123"}).status_code
                for _ in range(settings.login_max_attempts + 3)]
    assert 429 in statuses


# ------------------------------- refresh rotation -------------------------------
def test_refresh_rotates_and_invalidates_old_token():
    email = unique_email()
    c = TestClient(app)
    register(c, email=email)
    old_refresh = c.cookies.get(COOKIE)
    assert old_refresh
    r = c.post("/auth/refresh")
    assert r.status_code == 200 and r.json()["access_token"]
    # the rotated-out token must no longer work (rotation)
    fresh = TestClient(app)
    reuse = fresh.post("/auth/refresh", cookies={COOKIE: old_refresh})
    assert reuse.status_code == 401


def test_refresh_without_cookie_401():
    assert TestClient(app).post("/auth/refresh").status_code == 401


# ------------------------------- logout -------------------------------
def test_logout_revokes_session():
    email = unique_email()
    c = TestClient(app)
    register(c, email=email)
    old_refresh = c.cookies.get(COOKIE)
    assert c.post("/auth/logout").status_code == 200
    reuse = TestClient(app).post("/auth/refresh", cookies={COOKIE: old_refresh})
    assert reuse.status_code == 401


def test_logout_all_revokes_every_session():
    email = unique_email()
    c1 = TestClient(app)
    register(c1, email=email)
    r1 = c1.cookies.get(COOKIE)
    c2 = TestClient(app)
    c2.post("/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    token2 = c2.post("/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}).json()["access_token"]
    # log out everywhere from c2
    assert c2.post("/auth/logout-all", headers={"Authorization": f"Bearer {token2}"}).status_code == 200
    # c1's older session is now dead too
    assert TestClient(app).post("/auth/refresh", cookies={COOKIE: r1}).status_code == 401


# ------------------------------- password reset -------------------------------
def test_forgot_password_does_not_leak_existence(monkeypatch):
    box = _capture(monkeypatch, "send_password_reset_email")
    c = TestClient(app)
    r = c.post("/auth/forgot-password", json={"email": unique_email()})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "token" not in box  # no email/token generated for unknown address


def test_reset_password_flow(monkeypatch):
    box = _capture(monkeypatch, "send_password_reset_email")
    email = unique_email()
    c = TestClient(app)
    register(c, email=email)
    c.post("/auth/forgot-password", json={"email": email})
    assert box.get("token")
    new_pw = "BrandNew123"
    r = c.post("/auth/reset-password", json={"token": box["token"], "password": new_pw})
    assert r.status_code == 200
    assert c.post("/auth/login", json={"email": email, "password": new_pw}).status_code == 200
    assert c.post("/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}).status_code == 401


def test_reset_password_invalid_token_400():
    assert TestClient(app).post("/auth/reset-password",
        json={"token": "nope", "password": "Whatever123"}).status_code == 400


# ------------------------------- email verification -------------------------------
def test_verify_email_flow():
    client, body = auth_client()
    token = body["dev_verification_token"]
    assert token
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
    assert client.get("/me").json()["user"]["email_verified"] is True


def test_verify_email_invalid_token_400():
    assert TestClient(app).post("/auth/verify-email", json={"token": "bad"}).status_code == 400


# ------------------------------- profile -------------------------------
def test_update_profile_name_and_prefs():
    client, _ = auth_client()
    r = client.patch("/me", json={"name": "Renamed", "notification_prefs": {"scan_reports": False}})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["name"] == "Renamed"
    assert body["user"]["notification_prefs"]["scan_reports"] is False


def test_change_password_requires_current_password():
    client, _ = auth_client()
    bad = client.patch("/me", json={"new_password": "AnotherPass123", "current_password": "wrong"})
    assert bad.status_code == 403
    ok = client.patch("/me", json={"new_password": "AnotherPass123", "current_password": DEFAULT_PASSWORD})
    assert ok.status_code == 200


def test_change_email_resets_verification():
    client, body = auth_client()
    client.post("/auth/verify-email", json={"token": body["dev_verification_token"]})
    r = client.patch("/me", json={"email": unique_email("changed")})
    assert r.status_code == 200
    assert r.json()["user"]["email_verified"] is False


# ------------------------------- sessions -------------------------------
def test_sessions_list_and_revoke():
    email = unique_email()
    c = TestClient(app)
    reg = register(c, email=email).json()
    c.headers.update({"Authorization": f"Bearer {reg['access_token']}"})
    # second login => second session
    c.post("/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    sessions = c.get("/me/sessions").json()
    assert len(sessions) >= 2
    assert any(s["current"] for s in sessions)
    target = next(s for s in sessions if not s["current"])
    assert c.delete(f"/me/sessions/{target['id']}").status_code == 200
    assert target["id"] not in [s["id"] for s in c.get("/me/sessions").json()]


def test_protected_endpoints_require_auth():
    anon = TestClient(app)
    assert anon.get("/me").status_code == 401
    assert anon.get("/api/scans").status_code == 401
    assert anon.get("/api/dashboard").status_code == 401
    assert anon.get("/org").status_code == 401
    # a malformed bearer is also rejected
    assert anon.get("/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401
