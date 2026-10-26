"""Static guard against the exact regression that shipped a localhost tracker URL
to production: a missing/wrong API_BASE_URL in render.yaml's web service. Catches
the mistake at commit time, before it can ever reach a deploy, by parsing the
checked-in render.yaml itself (not the running app's Settings — see
test_config_production_validation.py for that complementary, runtime-level check).
"""
from pathlib import Path

import pytest
import yaml

_RENDER_YAML = Path(__file__).resolve().parents[2] / "render.yaml"


def _web_service_env(name: str) -> dict:
    doc = yaml.safe_load(_RENDER_YAML.read_text())
    for svc in doc["services"]:
        if svc.get("name") == name:
            return {e["key"]: e.get("value") for e in svc.get("envVars", []) if "value" in e}
    pytest.fail(f"no service named {name!r} in render.yaml")


def test_render_yaml_exists_and_parses():
    assert _RENDER_YAML.is_file()


def test_api_service_declares_api_base_url():
    env = _web_service_env("aeomirror-api")
    assert "API_BASE_URL" in env, (
        "render.yaml's aeomirror-api service has no API_BASE_URL — it will silently "
        "fall back to config.py's http://localhost:8000 dev default in production "
        "(the exact bug this test exists to catch)."
    )


def test_api_base_url_is_https_and_never_localhost():
    url = _web_service_env("aeomirror-api")["API_BASE_URL"]
    assert url.startswith("https://"), f"API_BASE_URL must be HTTPS in production, got {url!r}"
    assert "localhost" not in url and "127.0.0.1" not in url, (
        f"API_BASE_URL is a localhost/loopback address ({url!r}) — this is exactly "
        "what ships a broken AI Business Impact tracking snippet to production."
    )


def _registrable(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def test_auth_cookie_samesite_is_none_given_the_actual_cross_domain_topology():
    # This app's real topology (APP_BASE_URL on Vercel, API_BASE_URL on its own
    # Render host) is genuinely cross-site — see render.yaml's own note on
    # AUTH_COOKIE_SAMESITE. A stricter value here silently logs every user out on
    # every page refresh (the browser never attaches a strict/lax cookie to a
    # cross-site request, no matter what CORS allows).
    env = _web_service_env("aeomirror-api")
    from urllib.parse import urlparse
    app_host = urlparse(env["APP_BASE_URL"]).hostname
    api_host = urlparse(env["API_BASE_URL"]).hostname
    if _registrable(app_host) != _registrable(api_host):
        assert env.get("AUTH_COOKIE_SAMESITE", "").lower() == "none", (
            f"APP_BASE_URL ({app_host}) and API_BASE_URL ({api_host}) are on different "
            "domains, so AUTH_COOKIE_SAMESITE must be 'none' — anything stricter "
            "breaks session refresh on every page load in production."
        )
