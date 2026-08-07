"""Public report share links (shareable /r/<token>).

Security-focused: valid token serves the report with no auth; invalid/expired/revoked
all 404 indistinguishably; the public payload is a whitelist (no scan_id, internal
versions, recommendation evidence, AI _meta, or bulk page URLs); the public path never
regenerates the report; the active-share cap gates via 402; rate limiting fires. The
Anthropic API is never hit (ANTHROPIC_API_KEY is empty in tests)."""
import asyncio
import json
from datetime import timedelta

import app.api.routes_scan as rs
import app.billing.plans as plans_mod
from app.config import settings
from app.core.security import now_utc
from app.db.models import ReportShare
from app.db.session import SessionLocal
from app.main import app
from app.scanner.models import PageBundle
from fastapi.testclient import TestClient
from tests.authutil import auth_client
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS

anon = TestClient(app)   # NO auth — proves the public path needs none


async def _fake_fetch(url, *, transport=None, retries=None):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True)


def _scan_with_report(client, monkeypatch, url="https://share.example/"):
    """Create a scan and persist its report row (GET /reports/{id} upserts it)."""
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    sid = client.post("/v1/scan", json={"url": url}).json()["scan_id"]
    assert client.get(f"/reports/{sid}").status_code == 200
    return sid


def _create_share(client, sid) -> dict:
    r = client.post(f"/reports/{sid}/share")
    assert r.status_code == 200, r.text
    return r.json()


def test_valid_token_returns_report_without_auth(monkeypatch):
    client, _ = auth_client()
    sid = _scan_with_report(client, monkeypatch)
    token = _create_share(client, sid)["token"]

    r = anon.get(f"/public/reports/{token}")          # unauthenticated
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["domain"] and "scorecard" in body and "recommendations" in body
    assert r.headers.get("x-robots-tag") == "noindex"   # never index a shared report


def test_expired_token_returns_404(monkeypatch):
    client, _ = auth_client()
    sid = _scan_with_report(client, monkeypatch)
    token = _create_share(client, sid)["token"]
    db = SessionLocal()
    try:
        row = db.query(ReportShare).filter(ReportShare.token == token).first()
        row.expires_at = now_utc() - timedelta(days=1)
        db.commit()
    finally:
        db.close()
    assert anon.get(f"/public/reports/{token}").status_code == 404


def test_revoked_token_returns_404(monkeypatch):
    client, _ = auth_client()
    sid = _scan_with_report(client, monkeypatch)
    share = _create_share(client, sid)
    assert client.delete(f"/reports/shares/{share['id']}").status_code == 200
    assert anon.get(f"/public/reports/{share['token']}").status_code == 404


def test_random_token_returns_404():
    assert anon.get("/public/reports/not-a-real-token-at-all").status_code == 404


def test_public_payload_is_a_whitelist(monkeypatch):
    client, _ = auth_client()
    sid = _scan_with_report(client, monkeypatch)
    token = _create_share(client, sid)["token"]
    body = anon.get(f"/public/reports/{token}").json()
    blob = json.dumps(body)

    # Internal ids/versions are absent.
    for k in ("scan_id", "report_version", "scanner_version", "generated_at"):
        assert k not in body
    assert sid not in blob                                   # the internal scan id never leaks
    # Recommendations carry no scraped evidence (page <title>, robots Disallow paths).
    assert body["recommendations"], "expected recommendations for GOOD_HTML"
    for rec in body["recommendations"]:
        assert "evidence" not in rec
    # If an AI narrative is present, its internal _meta (model id, tier) is stripped.
    if body.get("ai"):
        assert "_meta" not in body["ai"]


def test_public_path_never_regenerates(monkeypatch):
    """The public read serves the PERSISTED report only — it must not call
    generate_report or the AI model (which would cost tokens / mutate state)."""
    client, _ = auth_client()
    sid = _scan_with_report(client, monkeypatch)          # report persisted here (auth path)
    token = _create_share(client, sid)["token"]

    import app.core.ai as ai_mod
    import app.reports.service as svc

    def _boom(*a, **k):
        raise AssertionError("public path must not regenerate the report / call AI")
    monkeypatch.setattr(svc, "generate_report", _boom)
    monkeypatch.setattr(ai_mod, "complete", _boom)

    assert anon.get(f"/public/reports/{token}").status_code == 200


def test_public_read_rate_limit_fires(monkeypatch):
    from app.core.cache import public_report_limiter
    client, _ = auth_client()
    sid = _scan_with_report(client, monkeypatch)
    token = _create_share(client, sid)["token"]

    monkeypatch.setattr(public_report_limiter, "limit", 1)
    public_report_limiter._hits.clear()
    assert anon.get(f"/public/reports/{token}").status_code == 200
    r2 = anon.get(f"/public/reports/{token}")
    assert r2.status_code == 429
    assert r2.headers.get("retry-after")
    public_report_limiter._hits.clear()


def test_create_share_requires_auth():
    assert anon.post("/reports/whatever/share").status_code == 401


def test_active_share_cap_returns_402(monkeypatch):
    client, _ = auth_client()
    sid = _scan_with_report(client, monkeypatch)          # billing off (default) for scan+report
    # Now enforce billing with a Free cap of 2 active shares.
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "share_limit", 2)

    assert client.post(f"/reports/{sid}/share").status_code == 200
    assert client.post(f"/reports/{sid}/share").status_code == 200
    r3 = client.post(f"/reports/{sid}/share")
    assert r3.status_code == 402
    assert r3.json()["unlock"]["kind"] == "share"


def test_share_url_is_retrievable_by_owner(monkeypatch):
    """No show-once: the owner can list an active share and get its token/URL back."""
    client, _ = auth_client()
    sid = _scan_with_report(client, monkeypatch)
    created = _create_share(client, sid)
    listed = client.get(f"/reports/{sid}/shares").json()["shares"]
    assert len(listed) == 1
    assert listed[0]["token"] == created["token"]        # URL retrievable, not shown-once
    assert listed[0]["path"] == f"/r/{created['token']}"


def test_record_view_failure_still_serves_report(monkeypatch):
    """A failing view-count write (metrics) must not break the content read."""
    client, _ = auth_client()
    sid = _scan_with_report(client, monkeypatch)
    token = _create_share(client, sid)["token"]

    # Make every commit raise — the only commit during the public GET is record_view's;
    # resolve + report read are queries. record_view swallows it and still serves.
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Session, "commit", lambda self: (_ for _ in ()).throw(RuntimeError("db down")))
    r = anon.get(f"/public/reports/{token}")
    assert r.status_code == 200 and r.json()["domain"]


def test_create_share_for_other_orgs_scan_is_404(monkeypatch):
    owner, _ = auth_client()
    other, _ = auth_client()
    sid = _scan_with_report(owner, monkeypatch, "https://xorg-create.example/")
    assert other.post(f"/reports/{sid}/share").status_code == 404


def test_list_other_orgs_scan_shares_is_404(monkeypatch):
    owner, _ = auth_client()
    other, _ = auth_client()
    sid = _scan_with_report(owner, monkeypatch, "https://xorg-list.example/")
    _create_share(owner, sid)
    assert other.get(f"/reports/{sid}/shares").status_code == 404


def test_revoke_other_orgs_share_is_404(monkeypatch):
    owner, _ = auth_client()
    other, _ = auth_client()
    sid = _scan_with_report(owner, monkeypatch, "https://xorg-revoke.example/")
    share = _create_share(owner, sid)
    # 404 (not 403) — never confirm the share exists to a non-owner.
    assert other.delete(f"/reports/shares/{share['id']}").status_code == 404
    # ...and the owner's link still works (revoke didn't happen).
    assert anon.get(f"/public/reports/{share['token']}").status_code == 200


def test_bulk_share_exposes_no_other_page_urls(monkeypatch):
    """A bulk scan's public payload must not leak the scanned page-URL list (only the
    primary URL appears; best/worst/page URLs live in result['bulk'], never read here)."""
    import app.scanner.bulk as bulk_mod
    from app.monitoring import worker
    from tests.test_bulk_scan import BULK_URLS, _fake_fetch_factory

    monkeypatch.setattr(bulk_mod, "fetch", _fake_fetch_factory())
    client, _ = auth_client(organization_name="Bulk Share Org")
    sid = client.post("/v1/scan/bulk", json={"urls": BULK_URLS}).json()["scan_id"]
    asyncio.run(worker.tick())
    assert client.get(f"/reports/{sid}").status_code == 200
    token = _create_share(client, sid)["token"]

    blob = json.dumps(anon.get(f"/public/reports/{token}").json())
    for other in BULK_URLS[1:]:          # every non-primary page URL is absent
        assert other not in blob
