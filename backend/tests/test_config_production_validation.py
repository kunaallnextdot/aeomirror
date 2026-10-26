"""Settings._validate_production's fail-fast checks (app/config.py).

Covers two independent checks:
- API_BASE_URL: a forgotten override here previously shipped
  "http://localhost:8000" straight into every production AI Business Impact
  tracking snippet (see app/api/routes_tracking.py's _snippet).
- AUTH_COOKIE_SAMESITE vs. a cross-domain frontend/API split: SameSite=strict/lax
  never sends the refresh cookie on a cross-site fetch, which silently logged
  every user out on a page refresh in production (see routes_auth.py's
  _set_refresh_cookie and auth/client.js's refreshSession).
"""
import pytest

from app.config import Settings

_BASE_OK = dict(
    environment="production", database_url="postgresql://u:p@host/db",
    redis_url="redis://host:6379/0", jwt_secret="x" * 32,
    # Same registrable domain as api_base_url below by default, so the API_BASE_URL
    # tests exercise only their own concern, not the SameSite/cross-domain check.
    app_base_url="https://aeomirror.com", auth_cookie_samesite="none",
)


def test_production_rejects_the_localhost_api_base_url_default():
    with pytest.raises(ValueError, match="API_BASE_URL"):
        Settings(**_BASE_OK, api_base_url="http://localhost:8000")


def test_production_rejects_a_non_https_api_base_url():
    with pytest.raises(ValueError, match="API_BASE_URL"):
        Settings(**_BASE_OK, api_base_url="http://api.aeomirror.com")


def test_production_accepts_a_real_https_api_base_url():
    s = Settings(**_BASE_OK, api_base_url="https://api.aeomirror.com")
    assert s.api_base_url == "https://api.aeomirror.com"


def test_development_is_unaffected_by_the_localhost_default():
    s = Settings(environment="development", api_base_url="http://localhost:8000")
    assert s.api_base_url == "http://localhost:8000"


# ------------------- AUTH_COOKIE_SAMESITE vs. cross-domain frontend/API -------------------

_CROSS_DOMAIN = dict(_BASE_OK, api_base_url="https://aeomirror.onrender.com")


def test_production_rejects_strict_samesite_when_frontend_and_api_are_cross_domain():
    with pytest.raises(ValueError, match="AUTH_COOKIE_SAMESITE"):
        Settings(**{**_CROSS_DOMAIN, "auth_cookie_samesite": "strict"})


def test_production_rejects_lax_samesite_when_frontend_and_api_are_cross_domain():
    with pytest.raises(ValueError, match="AUTH_COOKIE_SAMESITE"):
        Settings(**{**_CROSS_DOMAIN, "auth_cookie_samesite": "lax"})


def test_production_accepts_samesite_none_when_frontend_and_api_are_cross_domain():
    s = Settings(**{**_CROSS_DOMAIN, "auth_cookie_samesite": "none"})
    assert s.auth_cookie_samesite == "none"


def test_production_allows_strict_samesite_when_frontend_and_api_share_a_domain():
    # Same registrable domain (api.aeomirror.com is a subdomain of aeomirror.com) —
    # genuinely same-site, so strict/lax is fine and more defensive than 'none'.
    s = Settings(**{**_BASE_OK, "auth_cookie_samesite": "strict", "api_base_url": "https://api.aeomirror.com"})
    assert s.auth_cookie_samesite == "strict"


def test_development_is_unaffected_by_the_samesite_cross_domain_check():
    s = Settings(environment="development", auth_cookie_samesite="strict",
                app_base_url="https://aeomirror.com", api_base_url="https://aeomirror.onrender.com",
                database_url="sqlite:///:memory:")
    assert s.auth_cookie_samesite == "strict"
