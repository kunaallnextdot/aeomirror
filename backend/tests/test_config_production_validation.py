"""Settings._validate_production's fail-fast checks (app/config.py). Covers the
API_BASE_URL check specifically: a forgotten override here previously shipped
"http://localhost:8000" straight into every production AI Business Impact
tracking snippet (see app/api/routes_tracking.py's _snippet)."""
import pytest

from app.config import Settings

_BASE_OK = dict(
    environment="production", database_url="postgresql://u:p@host/db",
    redis_url="redis://host:6379/0", jwt_secret="x" * 32,
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
