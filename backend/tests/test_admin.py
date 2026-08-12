"""Phase 8 admin platform tests: authorization, dashboard, user/org/scan/monitor
management, analytics, system health, audit logs, feature flags, and settings."""
import app.api.routes_scan as rs
from app.db.models import AuditLog, User
from app.db.session import SessionLocal
from app.main import app
from app.scanner.models import PageBundle
from fastapi.testclient import TestClient
from tests.authutil import auth_client
from tests.test_monitoring import bad_bundle, good_bundle, set_fetch


def make_admin(user_id: str) -> None:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        u.is_platform_admin = True
        db.commit()
    finally:
        db.close()


def admin_client(**kw):
    c, body = auth_client(**kw)
    make_admin(body["user"]["id"])
    return c, body


# ------------------------------- authorization -------------------------------
def test_admin_endpoints_require_admin():
    anon = TestClient(app)
    assert anon.get("/admin/dashboard").status_code == 401
    user, _ = auth_client()                    # a normal (org-owner, non-platform) user
    assert user.get("/admin/dashboard").status_code == 403
    assert user.get("/admin/users").status_code == 403
    admin, _ = admin_client()
    assert admin.get("/admin/dashboard").status_code == 200


def test_me_exposes_platform_admin_flag():
    admin, body = admin_client()
    assert admin.get("/me").json()["user"]["is_platform_admin"] is True
    user, _ = auth_client()
    assert user.get("/me").json()["user"]["is_platform_admin"] is False


# ------------------------------- dashboard -------------------------------
def test_admin_dashboard_shape():
    admin, _ = admin_client()
    d = admin.get("/admin/dashboard").json()
    for k in ("total_users", "active_users", "organizations", "total_websites",
              "total_scans", "todays_scans", "running_monitors", "failed_jobs",
              "average_ai_score", "api_requests", "system_health"):
        assert k in d
    assert d["total_users"] >= 1
    assert d["system_health"] in ("healthy", "degraded")


# ------------------------------- user management -------------------------------
def test_admin_user_search_and_actions():
    admin, _ = admin_client()
    target, tbody = auth_client(name="Target Person")
    uid = tbody["user"]["id"]

    found = admin.get("/admin/users", params={"q": tbody["user"]["email"]}).json()
    assert found["total"] >= 1 and any(u["id"] == uid for u in found["items"])
    assert "pages" in found and found["page"] == 1

    assert admin.post(f"/admin/users/{uid}/suspend").json()["status"] == "suspended"
    assert admin.post(f"/admin/users/{uid}/activate").json()["status"] == "active"
    assert admin.post(f"/admin/users/{uid}/verify-email").status_code == 200
    rp = admin.post(f"/admin/users/{uid}/reset-password").json()
    assert rp["ok"] and rp.get("dev_reset_token")
    act = admin.get(f"/admin/users/{uid}/activity").json()
    assert "scan_count" in act and act["user"]["id"] == uid
    assert admin.delete(f"/admin/users/{uid}").json()["status"] == "deleted"
    assert admin.get(f"/admin/users/{uid}/activity").status_code == 404


def test_admin_cannot_delete_self():
    admin, body = admin_client()
    assert admin.delete(f"/admin/users/{body['user']['id']}").status_code == 409


# ------------------------------- org management -------------------------------
def test_admin_org_list_detail_delete():
    admin, _ = admin_client()
    _, obody = auth_client(organization_name="Deletable Org")
    oid = obody["organization"]["id"]
    listed = admin.get("/admin/organizations").json()
    assert any(o["id"] == oid for o in listed["items"])
    detail = admin.get(f"/admin/organizations/{oid}").json()
    assert detail["organization"]["members"] >= 1 and detail["members"]
    assert admin.delete(f"/admin/organizations/{oid}").json()["status"] == "deleted"
    assert admin.get(f"/admin/organizations/{oid}").status_code == 404


def test_admin_org_list_reports_real_plan():
    """The admin org list must reflect the org's ACTUAL plan, not a hardcoded 'free'."""
    admin, _ = admin_client()

    # A Pro org (upgraded via the checkout dev-complete flow) reports plan "pro".
    pro_client, pbody = auth_client(organization_name="Zeta Pro Practice")
    ref = pro_client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    pro_client.post("/billing/checkout/complete", json={"reference": ref})
    pro_id = pbody["organization"]["id"]
    pro_list = admin.get("/admin/organizations", params={"q": "Zeta Pro Practice"}).json()["items"]
    assert next(o for o in pro_list if o["id"] == pro_id)["plan"] == "pro"

    # A plain org still reports "free".
    _, fbody = auth_client(organization_name="Zeta Free Practice")
    free_id = fbody["organization"]["id"]
    free_list = admin.get("/admin/organizations", params={"q": "Zeta Free Practice"}).json()["items"]
    assert next(o for o in free_list if o["id"] == free_id)["plan"] == "free"


def test_admin_delete_org_purges_disposable_retains_financial():
    """Deleting an org purges disposable org-scoped rows but RETAINS financial records."""
    from datetime import datetime, timedelta

    from app.db.models import (
        AiContentInsight, Invitation, Invoice, NotificationLog, Payment, ReportShare,
        Scan, ScanSnapshot, Subscription, UsageEvent,
    )
    from app.db.session import SessionLocal
    admin, _ = admin_client()
    _, obody = auth_client(organization_name="Purge Org")
    oid = obody["organization"]["id"]

    db = SessionLocal()
    try:
        db.add_all([
            Scan(url="https://purge.example/", normalized_url="purge.example", ars=50,
                 rubric_version="t", result={}, organization_id=oid),
            UsageEvent(organization_id=oid, kind="compare"),
            AiContentInsight(scan_id="s-purge", page_url="https://purge.example/",
                             organization_id=oid, data={}),
            Invitation(organization_id=oid, email="inv@purge.example",
                       token_hash=f"purge-{oid}", expires_at=datetime.utcnow() + timedelta(days=1)),
            NotificationLog(organization_id=oid, kind="weekly_summary", status="sent"),
            ReportShare(token=f"purge-share-{oid}", scan_id="s-purge", organization_id=oid,
                        expires_at=datetime.utcnow() + timedelta(days=1)),
            ScanSnapshot(scan_id="s-purge", monitor_id=None, organization_id=oid,
                         schema_version=1, payload={"schema_version": 1}),
            Subscription(organization_id=oid, plan_code="pro"),
            Payment(organization_id=oid, kind="subscription"),
            Invoice(organization_id=oid, number=f"INV-PURGE-{oid}"),
        ])
        db.commit()
    finally:
        db.close()

    assert admin.delete(f"/admin/organizations/{oid}").json()["status"] == "deleted"

    db = SessionLocal()
    try:
        def n(model):
            return db.query(model).filter(model.organization_id == oid).count()
        # disposable → purged
        assert n(Scan) == 0 and n(UsageEvent) == 0 and n(AiContentInsight) == 0
        assert n(Invitation) == 0 and n(NotificationLog) == 0 and n(ReportShare) == 0
        assert n(ScanSnapshot) == 0                       # FIX 1: snapshots purged with the org
        # financial → retained
        assert n(Subscription) == 1 and n(Payment) == 1 and n(Invoice) == 1
    finally:
        db.close()


def test_admin_delete_user_purges_auth_tokens():
    """Deleting a user removes their dangling reset/verification tokens."""
    from datetime import datetime, timedelta

    from app.db.models import EmailVerification, PasswordReset
    from app.db.session import SessionLocal
    admin, _ = admin_client()
    _, ubody = auth_client()
    uid = ubody["user"]["id"]

    db = SessionLocal()
    try:
        exp = datetime.utcnow() + timedelta(hours=1)
        db.add_all([
            PasswordReset(user_id=uid, token_hash=f"pr-{uid}", expires_at=exp),
            EmailVerification(user_id=uid, token_hash=f"ev-{uid}", expires_at=exp),
        ])
        db.commit()
    finally:
        db.close()

    assert admin.delete(f"/admin/users/{uid}").json()["status"] == "deleted"

    db = SessionLocal()
    try:
        assert db.query(PasswordReset).filter(PasswordReset.user_id == uid).count() == 0
        assert db.query(EmailVerification).filter(EmailVerification.user_id == uid).count() == 0
    finally:
        db.close()


# ------------------------------- scan management -------------------------------
def test_admin_scan_management(monkeypatch):
    admin, _ = admin_client()
    set_fetch(monkeypatch, good_bundle)
    owner, _ = auth_client()
    sid = owner.post("/v1/scan", json={"url": "http://admin-scan.example/"}).json()["scan_id"]

    found = admin.get("/admin/scans", params={"q": "admin-scan"}).json()
    assert any(s["id"] == sid for s in found["items"])
    rep = admin.get(f"/admin/scans/{sid}").json()
    assert rep["report"]["scorecard"]["overall_score"] is not None
    logs = admin.get(f"/admin/scans/{sid}/logs").json()
    assert logs["logs"] and any("signal" in ln for ln in logs["logs"])
    rr = admin.post(f"/admin/scans/{sid}/rerun").json()
    assert rr["ok"] and rr["scan_id"] != sid
    assert admin.delete(f"/admin/scans/{sid}").json()["status"] == "deleted"


# ------------------------------- monitor management -------------------------------
def test_admin_monitor_management(monkeypatch):
    admin, _ = admin_client()
    set_fetch(monkeypatch, good_bundle)
    owner, _ = auth_client()
    mid = owner.post("/monitors", json={"url": "http://admin-mon.example/", "frequency": "manual"}).json()["id"]

    listed = admin.get("/admin/monitors").json()
    assert any(m["id"] == mid for m in listed["items"])
    assert admin.post(f"/admin/monitors/{mid}/pause").json()["status"] == "paused"
    assert admin.post(f"/admin/monitors/{mid}/resume").json()["status"] == "active"
    run = admin.post(f"/admin/monitors/{mid}/run").json()
    assert run["ok"] and run["latest_score"] is not None
    assert admin.delete(f"/admin/monitors/{mid}").json()["status"] == "deleted"


# ------------------------------- analytics -------------------------------
def test_admin_analytics(monkeypatch):
    admin, _ = admin_client()
    set_fetch(monkeypatch, good_bundle)
    owner, _ = auth_client()
    owner.post("/v1/scan", json={"url": "http://shop-analytics.example/"})
    a = admin.get("/admin/analytics").json()
    for k in ("scans", "daily_series", "average_score", "issue_distribution",
              "score_distribution", "top_issue_categories", "most_scanned_industries",
              "most_scanned_domains", "most_common_failures"):
        assert k in a
    assert isinstance(a["daily_series"], list) and len(a["daily_series"]) == 14
    assert a["scans"]["total"] >= 1
    assert any(i["industry"] == "E-commerce" for i in a["most_scanned_industries"])


# ------------------------------- system health -------------------------------
def test_admin_system_health():
    admin, _ = admin_client()
    s = admin.get("/admin/system").json()
    assert s["overall"] in ("healthy", "degraded")
    assert s["database"] == "ok"
    assert s["redis"] == "not_configured"     # in-memory in tests
    assert "pending" in s["queue"]
    assert s["scheduler"] in ("enabled", "disabled")
    assert s["email"] == "not_configured"
    assert "memory_mb" in s and "disk" in s


# ------------------------------- audit logs -------------------------------
def test_admin_actions_are_audited():
    admin, _ = admin_client()
    target, tbody = auth_client()
    admin.post(f"/admin/users/{tbody['user']['id']}/suspend")
    logs = admin.get("/admin/logs").json()
    assert logs["total"] >= 1
    actions = {l["action"] for l in logs["items"]}
    assert "suspend_user" in actions
    filtered = admin.get("/admin/logs", params={"action": "suspend_user"}).json()
    assert all(l["action"] == "suspend_user" for l in filtered["items"])


def test_admin_login_is_audited():
    from tests.authutil import DEFAULT_PASSWORD
    admin, body = admin_client()
    email = body["user"]["email"]
    # logging in as an admin records an admin_login audit entry
    TestClient(app).post("/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    db = SessionLocal()
    try:
        n = db.query(AuditLog).filter(AuditLog.action == "admin_login",
                                      AuditLog.actor_email == email).count()
    finally:
        db.close()
    assert n >= 1


# ------------------------------- feature flags -------------------------------
def test_feature_flags_gate_behavior(monkeypatch):
    admin, _ = admin_client()
    set_fetch(monkeypatch, good_bundle)
    owner, _ = auth_client()
    sid = owner.post("/v1/scan", json={"url": "http://flag.example/"}).json()["scan_id"]

    flags = admin.get("/admin/settings").json()["feature_flags"]
    assert "pdf_export" in flags and "monitoring" in flags

    # disable pdf_export -> owner PDF download blocked
    admin.patch("/admin/settings", json={"feature_flags": {"pdf_export": False}})
    assert owner.get(f"/reports/{sid}/pdf").status_code == 403
    admin.patch("/admin/settings", json={"feature_flags": {"pdf_export": True}})
    assert owner.get(f"/reports/{sid}/pdf").status_code == 200

    # disable monitoring -> creating a monitor blocked
    admin.patch("/admin/settings", json={"feature_flags": {"monitoring": False}})
    assert owner.post("/monitors", json={"url": "http://x.example/", "frequency": "manual"}).status_code == 403
    admin.patch("/admin/settings", json={"feature_flags": {"monitoring": True}})
    assert owner.post("/monitors", json={"url": "http://x.example/", "frequency": "manual"}).status_code == 201


# ------------------------------- system settings / maintenance -------------------------------
def test_maintenance_mode_blocks_non_admin_scans(monkeypatch):
    admin, _ = admin_client()
    set_fetch(monkeypatch, good_bundle)
    owner, _ = auth_client()
    try:
        admin.patch("/admin/settings", json={"settings": {"maintenance_mode": True}})
        assert owner.post("/v1/scan", json={"url": "http://maint.example/"}).status_code == 503
        # admin can still scan during maintenance
        assert admin.post("/v1/scan", json={"url": "http://maint.example/"}).status_code == 200
    finally:
        admin.patch("/admin/settings", json={"settings": {"maintenance_mode": False}})
    assert owner.post("/v1/scan", json={"url": "http://maint2.example/"}).status_code == 200


def test_admin_settings_writable_keys_only():
    admin, _ = admin_client()
    r = admin.patch("/admin/settings", json={"settings": {"scanner_version": "9.9.9", "secret_key": "hax"}})
    applied = r.json()["applied"]["settings"]
    assert "scanner_version" in applied and "secret_key" not in applied
