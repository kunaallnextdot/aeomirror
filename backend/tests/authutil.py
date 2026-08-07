"""Shared helpers for auth-aware tests (Phase 5).

`auth_client()` registers a fresh account and returns a TestClient that already
carries the Bearer access token (and refresh cookie), so protected endpoints and
org-scoped scans work out of the box.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app

DEFAULT_PASSWORD = "Password123"


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def register(client: TestClient, *, email: str | None = None,
             password: str = DEFAULT_PASSWORD, name: str = "Test User",
             organization_name: str | None = None):
    email = email or unique_email()
    return client.post("/auth/register", json={
        "name": name, "email": email, "password": password,
        "organization_name": organization_name,
    })


def authenticate(client: TestClient, **kwargs) -> dict:
    """Register a fresh account and attach its Bearer token to an EXISTING client.
    For module-level clients, call this from a fixture (not at import time) so the
    test schema exists first. Returns the register body."""
    resp = register(client, **kwargs)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    client.headers.update({"Authorization": f"Bearer {body['access_token']}"})
    return body


def auth_client(**kwargs) -> tuple[TestClient, dict]:
    """Return (client_with_bearer, register_body)."""
    client = TestClient(app)
    return client, authenticate(client, **kwargs)
