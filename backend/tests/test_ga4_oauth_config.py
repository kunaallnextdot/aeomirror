"""Google OAuth authorize-URL construction + config hygiene — regression coverage for
a real production incident: Connect Google Analytics failed in production with
Google's `403 access_denied` ("app not verified") followed by a `400` at Google's
internal `accounts.google.co.in/accounts/SetSID` endpoint. Investigation (see the
session's diagnosis) found the generated authorize URL itself was correctly formed —
no malformed/double-encoded parameter, no missing field — which rules out a URL-
construction bug as the cause of either symptom; access_denied is a Google Cloud
Console consent-screen/publishing-status fact, and SetSID is Google's own account-
session continuation step, both outside this codebase's control.

What WAS a genuine, fixable gap: GOOGLE_OAUTH_CLIENT_ID/_SECRET/_REDIRECT_URI had no
whitespace normalization, so a one-character paste artifact (a trailing newline/space
from copying a value out of Cloud Console or a dashboard env-var field) would silently
corrupt the exact-match `redirect_uri` Google requires, surfacing as a confusing
downstream failure rather than an obvious mismatch error. This file pins that fix, the
authorize URL's own correctness, and that the new diagnostic logging never leaks a
secret.
"""
import logging
from urllib.parse import parse_qsl, urlparse

from app.config import Settings, settings
from app.db.models import OAuthState
from app.db.session import SessionLocal
from app.services.integrations import google_analytics as ga4
from tests.authutil import auth_client

FAKE_REDIRECT = "https://aeomirror.onrender.com/integrations/google/callback"


def _enable_ga4(monkeypatch, *, redirect_uri=FAKE_REDIRECT):
    monkeypatch.setattr(settings, "google_oauth_client_id",
                        "123456789012-abcdefghijklmnopqrstuvwxyz123456.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "GOCSPX-not-a-real-secret")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", redirect_uri)
    monkeypatch.setattr(settings, "token_encryption_key",
                        "zxhjMoSGw8Hu9dZ0qVgN6W7AUHHho9mz5igY2ZvQ4oE=")


# ------------------------------- config hygiene -------------------------------

def test_oauth_client_id_secret_redirect_uri_are_stripped_of_stray_whitespace():
    """A trailing newline/space pasted into a Render env var field must not silently
    corrupt the value — this is a real, common copy-paste artifact, and Google's
    redirect_uri check is an exact byte-for-byte string match."""
    s = Settings(
        google_oauth_client_id=" abc123.apps.googleusercontent.com \n",
        google_oauth_client_secret="\tGOCSPX-fake\n",
        google_oauth_redirect_uri=f" {FAKE_REDIRECT} \n",
    )
    assert s.google_oauth_client_id == "abc123.apps.googleusercontent.com"
    assert s.google_oauth_client_secret == "GOCSPX-fake"
    assert s.google_oauth_redirect_uri == FAKE_REDIRECT


def test_empty_oauth_values_still_disable_the_integration():
    """Stripping must not turn a genuinely-unset (whitespace-only) value into a
    truthy one — ga4_integration_enabled must still correctly read it as unset."""
    s = Settings(google_oauth_client_id="   ", google_oauth_client_secret="",
                google_oauth_redirect_uri="  \n")
    assert s.ga4_integration_enabled is False


# ------------------------------- authorize URL correctness -------------------------------

def test_authorize_url_is_well_formed_with_every_required_parameter(monkeypatch):
    """Direct repro of the production incident: generate the REAL authorize URL and
    parse every parameter back out, the same way Google's server would, to rule out
    a malformed/double-encoded/missing parameter as the cause of the reported
    access_denied / SetSID failures."""
    _enable_ga4(monkeypatch)
    verifier, challenge = ga4._generate_pkce_pair()
    url = ga4.build_authorize_url("a-real-looking-state-value", challenge)

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    assert parsed.path == "/o/oauth2/v2/auth"

    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    # Exact round-trip: what we parse back out must equal what we configured,
    # proving no corruption happened in the encode step.
    assert params["client_id"] == settings.google_oauth_client_id
    assert params["redirect_uri"] == settings.google_oauth_redirect_uri
    assert params["response_type"] == "code"
    assert params["scope"] == "https://www.googleapis.com/auth/analytics.readonly"
    assert params["access_type"] == "offline"
    # Both "consent" (always issue a refresh_token) AND "select_account" (always show
    # the account chooser, never silently reuse the browser's last Google session —
    # the most common real cause of "connected but 0 accessible properties").
    assert params["prompt"] == "consent select_account"
    assert params["include_granted_scopes"] == "true"
    assert params["state"] == "a-real-looking-state-value"
    assert params["code_challenge"] == challenge
    assert params["code_challenge_method"] == "S256"
    # No stray/duplicate parameters from a construction bug.
    assert len(params) == 10


def test_redirect_uri_in_authorize_url_matches_production_callback_route_exactly(monkeypatch):
    """Pins the exact value the ticket states is registered in Google Cloud Console —
    a regression here would silently break every future connect attempt."""
    _enable_ga4(monkeypatch, redirect_uri="https://aeomirror.onrender.com/integrations/google/callback")
    verifier, challenge = ga4._generate_pkce_pair()
    url = ga4.build_authorize_url("s", challenge)
    params = dict(parse_qsl(urlparse(url).query))
    assert params["redirect_uri"] == "https://aeomirror.onrender.com/integrations/google/callback"
    # No accidental /api prefix, no trailing slash.
    assert "/api/" not in params["redirect_uri"]
    assert not params["redirect_uri"].endswith("/callback/")


# ------------------------------- safe logging -------------------------------

def test_connect_logs_redirect_uri_but_never_the_client_secret(monkeypatch, caplog):
    _enable_ga4(monkeypatch)
    client, _ = auth_client()
    caplog.set_level(logging.INFO, logger="app.integrations.google_analytics.routes")
    r = client.get("/integrations/google/connect")
    assert r.status_code == 200
    assert settings.google_oauth_redirect_uri in caplog.text
    assert settings.google_oauth_client_secret not in caplog.text
    # Only a short prefix of the client_id is logged, never the full value.
    assert settings.google_oauth_client_id not in caplog.text

    # Clean up the OAuthState row this created — the shared test-session DB persists
    # across files, and another test (test_integrations_oauth.py's
    # test_connect_returns_authorize_url_bound_to_caller) asserts an exact global
    # row count for this table.
    db = SessionLocal()
    try:
        db.query(OAuthState).delete()
        db.commit()
    finally:
        db.close()


def test_callback_failure_logs_only_the_safe_error_code_never_raw_params(monkeypatch, caplog):
    _enable_ga4(monkeypatch)
    client, _ = auth_client()
    caplog.set_level(logging.WARNING, logger="app.integrations.google_analytics.routes")
    r = client.get("/integrations/google/callback",
                   params={"code": "raw-auth-code-must-not-be-logged", "state": "not-a-real-state"},
                   follow_redirects=False)
    assert r.status_code == 302
    assert "invalid_state" in caplog.text
    assert "raw-auth-code-must-not-be-logged" not in caplog.text
