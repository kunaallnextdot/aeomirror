"""Phase 5 RBAC + invitation tests: the permission matrix (Owner/Admin/Member/
Viewer), the invite→accept→join flow, protected APIs, and cross-org isolation."""
from fastapi.testclient import TestClient

import app.api.routes_org as ro
import app.api.routes_scan as rs
from app.main import app
from app.scanner.models import PageBundle
from tests.authutil import DEFAULT_PASSWORD, auth_client, unique_email
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS


async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"content-encoding": "gzip", "cache-control": "max-age=60"})


def _org_scan(owner_client, url):
    """Owner runs an org-attributed scan; return its id."""
    return owner_client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def _invite_and_accept(owner_client, role, monkeypatch):
    """Owner invites `role`; a brand-new user accepts. Returns an authed client."""
    box: dict = {}
    monkeypatch.setattr(ro, "send_invitation_email",
                        lambda email, token, *a, **k: box.update(token=token) or True)
    email = unique_email(role)
    r = owner_client.post("/org/invitations", json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    acc = c.post("/org/invitations/accept",
                 json={"token": box["token"], "name": role.title(), "password": DEFAULT_PASSWORD})
    assert acc.status_code == 200, acc.text
    c.headers.update({"Authorization": f"Bearer {acc.json()['access_token']}"})
    return c, acc.json()


# ------------------------------- invite flow -------------------------------
def test_invite_accept_joins_org_as_role(monkeypatch):
    owner, _ = auth_client(organization_name="Invite Org")
    member, body = _invite_and_accept(owner, "member", monkeypatch)
    assert body["user"]["role"] == "member"
    assert body["organization"]["name"] == "Invite Org"
    # owner sees the new teammate
    members = owner.get("/org/members").json()
    emails = {m["email"] for m in members}
    assert body["user"]["email"] in emails
    assert any(m["is_owner"] for m in members)


def test_invitation_info_describes_pending_invite(monkeypatch):
    owner, _ = auth_client(organization_name="Info Org")
    box: dict = {}
    monkeypatch.setattr(ro, "send_invitation_email",
                        lambda email, token, *a, **k: box.update(token=token) or True)
    email = unique_email("info")
    owner.post("/org/invitations", json={"email": email, "role": "member"})
    info = TestClient(app).get("/org/invitations/info", params={"token": box["token"]}).json()
    assert info["organization_name"] == "Info Org"
    assert info["role"] == "member"
    assert info["email"] == email
    assert info["requires_signup"] is True   # brand-new email


def test_pending_invitation_listed_then_cancelled(monkeypatch):
    owner, _ = auth_client()
    monkeypatch.setattr(ro, "send_invitation_email", lambda *a, **k: True)
    email = unique_email("pending")
    inv = owner.post("/org/invitations", json={"email": email, "role": "viewer"}).json()
    listed = owner.get("/org/invitations").json()
    assert any(i["id"] == inv["id"] and i["status"] == "pending" for i in listed)
    assert owner.delete(f"/org/invitations/{inv['id']}").status_code == 200
    assert all(i["id"] != inv["id"] for i in owner.get("/org/invitations").json())


# ------------------------------- permission matrix -------------------------------
def test_viewer_is_read_only(monkeypatch):
    owner, _ = auth_client()
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    scan_id = _org_scan(owner, "https://rbac-viewer.example/")
    viewer, _ = _invite_and_accept(owner, "viewer", monkeypatch)
    # can read
    assert viewer.get("/api/dashboard").status_code == 200
    assert viewer.get("/api/scans").status_code == 200
    assert viewer.get(f"/api/scans/{scan_id}").status_code == 200
    # cannot mutate
    assert viewer.post(f"/api/scans/{scan_id}/rerun").status_code == 403
    assert viewer.delete(f"/api/scans/{scan_id}").status_code == 403
    assert viewer.post("/org/invitations", json={"email": unique_email(), "role": "member"}).status_code == 403


def test_member_can_run_but_not_delete_or_invite(monkeypatch):
    owner, _ = auth_client()
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    scan_id = _org_scan(owner, "https://rbac-member.example/")
    member, _ = _invite_and_accept(owner, "member", monkeypatch)
    assert member.post(f"/api/scans/{scan_id}/rerun").status_code == 200
    assert member.delete(f"/api/scans/{scan_id}").status_code == 403
    assert member.post("/org/invitations", json={"email": unique_email(), "role": "member"}).status_code == 403


def test_admin_can_delete_and_invite(monkeypatch):
    owner, _ = auth_client()
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    scan_id = _org_scan(owner, "https://rbac-admin.example/")
    admin, _ = _invite_and_accept(owner, "admin", monkeypatch)
    assert admin.delete(f"/api/scans/{scan_id}").status_code == 200
    monkeypatch.setattr(ro, "send_invitation_email", lambda *a, **k: True)
    assert admin.post("/org/invitations", json={"email": unique_email(), "role": "member"}).status_code == 201
    # but only the Owner can change roles / rename the org
    assert admin.patch("/org", json={"name": "Renamed by admin"}).status_code == 200  # org:update allows admin


def test_owner_can_manage_members(monkeypatch):
    owner, _ = auth_client()
    member, mbody = _invite_and_accept(owner, "member", monkeypatch)
    members = owner.get("/org/members").json()
    mem = next(m for m in members if m["email"] == mbody["user"]["email"])
    # promote to admin
    up = owner.patch(f"/org/members/{mem['id']}", json={"role": "admin"})
    assert up.status_code == 200 and up.json()["role"] == "admin"
    # owner role itself is protected
    owner_row = next(m for m in owner.get("/org/members").json() if m["is_owner"])
    assert owner.patch(f"/org/members/{owner_row['id']}", json={"role": "member"}).status_code == 409
    # remove the member
    assert owner.delete(f"/org/members/{mem['id']}").status_code == 200


def test_member_cannot_manage_members(monkeypatch):
    owner, _ = auth_client()
    member, _ = _invite_and_accept(owner, "member", monkeypatch)
    other, obody = _invite_and_accept(owner, "viewer", monkeypatch)
    row = next(m for m in owner.get("/org/members").json() if m["email"] == obody["user"]["email"])
    assert member.patch(f"/org/members/{row['id']}", json={"role": "admin"}).status_code == 403
    assert member.delete(f"/org/members/{row['id']}").status_code == 403


# ------------------------------- cross-org isolation -------------------------------
def test_scans_isolated_between_orgs(monkeypatch):
    owner_a, _ = auth_client(organization_name="Org A")
    owner_b, _ = auth_client(organization_name="Org B")
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    scan_id = _org_scan(owner_a, "https://org-a-only.example/")
    # B cannot see or read A's scan
    b_ids = [s["id"] for s in owner_b.get("/api/scans").json()]
    assert scan_id not in b_ids
    assert owner_b.get(f"/api/scans/{scan_id}").status_code == 404
    assert owner_b.post(f"/api/scans/{scan_id}/rerun").status_code == 404
    # A can
    assert owner_a.get(f"/api/scans/{scan_id}").status_code == 200


def test_second_org_cannot_invite_existing_member(monkeypatch):
    owner_a, _ = auth_client()
    member, mbody = _invite_and_accept(owner_a, "member", monkeypatch)
    owner_b, _ = auth_client()
    monkeypatch.setattr(ro, "send_invitation_email", lambda *a, **k: True)
    # inviting someone already in another org is rejected (one org per user)
    r = owner_b.post("/org/invitations", json={"email": mbody["user"]["email"], "role": "member"})
    assert r.status_code == 409
