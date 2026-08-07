"""Rubric versioning tests: DB-sourced active rubric, code fallback, engine uses
the provided rubric, and scans record rubric_version_id. Scores stay identical to
the code rubric because the seeded weights are identical."""
import pytest
from fastapi.testclient import TestClient

import app.api.routes_scan as rs
from app.db.models import RubricVersion, Scan
from app.db.session import SessionLocal
from app.main import app
from app.scanner import rubric_provider
from app.scanner.engine import score
from app.scanner.rubric import CHECK_WEIGHTS, FAMILY_WEIGHTS, Rubric, default_rubric
from app.scanner.models import PageBundle
from tests.authutil import authenticate
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS, good_page

# Scanning requires an account; billing off in tests so scans never gate on quota.
client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def _auth_client():
    authenticate(client)


@pytest.fixture(autouse=True)
def _clean_rubric():
    # Ensure no active rubric bleeds into other tests, and the cache is cleared.
    yield
    db = SessionLocal()
    db.query(RubricVersion).delete()
    db.commit()
    db.close()
    rubric_provider.clear_cache()


async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True)


def _seed(version, *, active, family=None, check=None):
    db = SessionLocal()
    db.add(RubricVersion(version=version, family_weights=family or FAMILY_WEIGHTS,
                         check_weights=check or CHECK_WEIGHTS, is_active=active))
    db.commit()
    db.close()
    rubric_provider.clear_cache()


def test_default_rubric_matches_code_constants():
    r = default_rubric()
    assert r.version == "2026.07.1"
    assert r.family_weights == FAMILY_WEIGHTS
    assert r.check_weights == CHECK_WEIGHTS


def test_active_rubric_falls_back_to_code_when_db_empty():
    db = SessionLocal()
    try:
        r = rubric_provider.get_active_rubric(db)
        assert r.version == "2026.07.1"
        assert r.family_weights == FAMILY_WEIGHTS
    finally:
        db.close()


def test_active_rubric_loaded_from_db():
    _seed("test-9.9", active=True)
    db = SessionLocal()
    try:
        r = rubric_provider.get_active_rubric(db)
        assert r.version == "test-9.9"
    finally:
        db.close()


def test_engine_uses_provided_rubric():
    page = good_page()
    default_report = score(page)                 # code rubric
    zero = Rubric(version="zero",
                  family_weights={k: 0 for k in FAMILY_WEIGHTS},
                  check_weights={k: 0 for k in CHECK_WEIGHTS})
    zero_report = score(page, zero)
    assert default_report.ars >= 90              # default scoring unchanged
    assert zero_report.ars == 0                  # provided weights are honored
    assert zero_report.rubric_version == "zero"


def test_scan_stores_rubric_version_id(monkeypatch):
    _seed("2026.07.1", active=True)
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    resp = client.post("/v1/scan", json={"url": "https://rubric-id-test.example/"}).json()
    db = SessionLocal()
    try:
        row = db.query(Scan).filter(Scan.id == resp["scan_id"]).first()
        assert row is not None
        assert row.rubric_version_id == "2026.07.1"
        assert row.rubric_version == "2026.07.1"
    finally:
        db.close()
