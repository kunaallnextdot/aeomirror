"""Phase 9 billing tests: plans, checkout, webhook signature verification, the
subscription lifecycle, one-time report purchase, refunds, invoices, and feature
gating (enforcement flipped on per-test)."""
import json

import app.billing.entitlements as ent
import app.billing.plans as plans_mod
from app.billing.providers.stripe_provider import make_stripe_signature
from app.config import settings
from app.db.models import (
    Invoice, NotificationLog, Payment, PLAN_PRO, Subscription,
)
from app.db.session import SessionLocal
from app.main import app
from fastapi.testclient import TestClient
from tests.authutil import auth_client
from tests.test_monitoring import good_bundle, set_fetch

FIXED_TS = 1700000000


# ------------------------------- plans + default subscription -------------------------------
def test_list_plans():
    client, _ = auth_client()
    plans = client.get("/billing/plans").json()["plans"]
    codes = {p["code"] for p in plans}
    assert codes == {"free", "report", "pro"}
    pro = next(p for p in plans if p["code"] == "pro")
    assert pro["interval"] == "month" and pro["price_cents"] > 0 and pro["features"]


def test_default_subscription_is_free():
    client, _ = auth_client()
    s = client.get("/billing/subscription").json()
    assert s["plan"] == "free"
    assert s["subscription"] is None
    assert "scans" in s["usage"]


# ------------------------------- checkout + dev completion -------------------------------
def test_checkout_and_dev_complete_activates_pro():
    client, body = auth_client(organization_name="Upgrade Co")
    co = client.post("/billing/checkout", json={"plan_code": "pro"}).json()
    assert co["dev_mode"] is True and co["reference"] and "/billing/complete" in co["checkout_url"]

    done = client.post("/billing/checkout/complete", json={"reference": co["reference"]})
    assert done.status_code == 200

    s = client.get("/billing/subscription").json()
    assert s["plan"] == "pro"
    assert s["subscription"]["status"] == "active"
    # invoice + payment recorded, activation email logged
    assert client.get("/billing/invoices").json()["invoices"]
    pays = client.get("/billing/payments").json()["payments"]
    assert any(p["status"] == "succeeded" and p["kind"] == "subscription" for p in pays)
    db = SessionLocal()
    try:
        org_id = body["organization"]["id"]
        assert db.query(NotificationLog).filter(
            NotificationLog.organization_id == org_id,
            NotificationLog.kind == "subscription_activated").count() >= 1
    finally:
        db.close()


def test_duplicate_dev_complete_is_idempotent():
    client, _ = auth_client()
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})
    client.post("/billing/checkout/complete", json={"reference": ref})   # again
    db = SessionLocal()
    try:
        # exactly one succeeded subscription payment for this reference
        n = db.query(Payment).filter(Payment.reference == ref, Payment.status == "succeeded").count()
    finally:
        db.close()
    assert n == 1


def test_checkout_report_requires_scan_id():
    client, _ = auth_client()
    r = client.post("/billing/checkout", json={"plan_code": "report"})
    assert r.status_code == 400


# ------------------------------- webhook signature verification -------------------------------
def _stripe_event(evt_type, obj, evt_id="evt_test"):
    return {"id": evt_id, "type": evt_type, "data": {"object": obj}}


def _post_webhook(client, event, secret):
    payload = json.dumps(event).encode()
    sig = make_stripe_signature(payload, secret, FIXED_TS)
    return client.post("/billing/webhooks/stripe", content=payload,
                       headers={"Stripe-Signature": sig, "Content-Type": "application/json"})


def test_webhook_valid_signature_activates(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    client, _ = auth_client()
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    event = _stripe_event("checkout.session.completed",
                          {"client_reference_id": ref, "metadata": {"reference": ref}},
                          evt_id="evt_wh_1")
    r = _post_webhook(client, event, "whsec_test")
    assert r.status_code == 200 and r.json()["received"] is True
    assert client.get("/billing/subscription").json()["plan"] == "pro"


def test_webhook_invalid_signature_rejected(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    client, _ = auth_client()
    event = _stripe_event("checkout.session.completed", {"client_reference_id": "nope"})
    payload = json.dumps(event).encode()
    bad = "t=1700000000,v1=deadbeef"
    r = client.post("/billing/webhooks/stripe", content=payload,
                    headers={"Stripe-Signature": bad, "Content-Type": "application/json"})
    assert r.status_code == 400


def test_webhook_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    client, _ = auth_client()
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    event = _stripe_event("checkout.session.completed",
                          {"client_reference_id": ref}, evt_id="evt_dup_1")
    assert _post_webhook(client, event, "whsec_test").json()["outcome"] == "processed"
    assert _post_webhook(client, event, "whsec_test").json()["outcome"] == "duplicate"


# ------------------------------- subscription management -------------------------------
def test_cancel_and_resume():
    client, body = auth_client()
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})

    c = client.patch("/billing/subscription/cancel").json()
    assert c["subscription"]["cancel_at_period_end"] is True
    r = client.patch("/billing/subscription/resume").json()
    assert r["subscription"]["cancel_at_period_end"] is False
    db = SessionLocal()
    try:
        assert db.query(NotificationLog).filter(
            NotificationLog.organization_id == body["organization"]["id"],
            NotificationLog.kind == "subscription_cancelled").count() >= 1
    finally:
        db.close()


def test_cancel_without_subscription_400():
    client, _ = auth_client()
    assert client.patch("/billing/subscription/cancel").status_code == 400


# ------------------------------- refund -------------------------------
def test_refund_marks_payment_and_cancels_sub(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    client, _ = auth_client()
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})
    assert client.get("/billing/subscription").json()["plan"] == "pro"

    refund = _stripe_event("charge.refunded", {"metadata": {"reference": ref}}, evt_id="evt_refund_1")
    assert _post_webhook(client, refund, "whsec_test").status_code == 200
    db = SessionLocal()
    try:
        p = db.query(Payment).filter(Payment.reference == ref).first()
        assert p.status == "refunded"
    finally:
        db.close()
    assert client.get("/billing/subscription").json()["plan"] == "free"   # sub canceled


# ------------------------------- one-time report purchase + gating -------------------------------
def test_one_time_report_unlocks_only_that_scan(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    # This test needs two scans; raise the Free scan cap so scan quota isn't the gate.
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", 50)
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()
    sid = client.post("/v1/scan", json={"url": "http://report-buy.example/"}).json()["scan_id"]
    other = client.post("/v1/scan", json={"url": "http://other-scan.example/"}).json()["scan_id"]

    # Free: can view the on-screen report but not download it.
    assert client.get(f"/reports/{sid}").status_code == 200
    assert client.get(f"/reports/{sid}/pdf").status_code == 402

    # Buy the one-time report for that scan.
    ref = client.post("/billing/checkout", json={"plan_code": "report", "scan_id": sid}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})

    assert client.get(f"/reports/{sid}/pdf").status_code == 200      # unlocked
    assert client.get(f"/reports/{other}/pdf").status_code == 402    # only that scan
    assert client.get("/billing/subscription").json()["plan"] == "free"  # plan unchanged


# ------------------------------- feature gating (enforcement on) -------------------------------
def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def test_team_invites_are_pro_only(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()

    # Free cannot invite teammates (team is a Pro feature).
    assert client.post("/org/invitations", json={"email": "t@example.com", "role": "member"}).status_code == 402

    _go_pro(client)

    monkeypatch.setattr("app.api.routes_org.send_invitation_email", lambda *a, **k: True)
    assert client.post("/org/invitations", json={"email": "t@example.com", "role": "member"}).status_code == 201


def test_free_compare_quota_enforced(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    # Raise the Free scan cap so creating the two scans to compare isn't the gate.
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", 50)
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()
    s1 = client.post("/v1/scan", json={"url": "http://c1.example/"}).json()["scan_id"]
    s2 = client.post("/v1/scan", json={"url": "http://c2.example/"}).json()["scan_id"]
    payload = {"a_id": s1, "b_id": s2}

    # Free includes 1 comparison / month.
    assert client.post("/api/compare", json=payload).status_code == 200
    blocked = client.post("/api/compare", json=payload)   # 2nd this month
    assert blocked.status_code == 402
    assert "1 comparison" in blocked.json()["detail"]

    _go_pro(client)
    assert client.post("/api/compare", json=payload).status_code == 200   # unlimited


def test_compare_quota_is_month_scoped(monkeypatch):
    from datetime import datetime

    from app.db.models import USAGE_COMPARE, UsageEvent

    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", 50)
    set_fetch(monkeypatch, good_bundle)
    client, body = auth_client()
    org_id = body["organization"]["id"]

    # Two comparisons in a PRIOR month must not count against this month's quota.
    db = SessionLocal()
    try:
        for _ in range(2):
            db.add(UsageEvent(organization_id=org_id, kind=USAGE_COMPARE,
                              created_at=datetime(2000, 1, 1)))
        db.commit()
    finally:
        db.close()

    s1 = client.post("/v1/scan", json={"url": "http://cm1.example/"}).json()["scan_id"]
    s2 = client.post("/v1/scan", json={"url": "http://cm2.example/"}).json()["scan_id"]
    payload = {"a_id": s1, "b_id": s2}
    assert client.post("/api/compare", json=payload).status_code == 200   # month reset (Free = 1)
    assert client.post("/api/compare", json=payload).status_code == 402   # 2nd this month


def test_subscription_usage_exposes_all_quotas():
    client, _ = auth_client()
    usage = client.get("/billing/subscription").json()["usage"]
    assert set(usage) >= {"scans", "monitors", "compares", "bulk_trial"}
    for key in ("scans", "monitors", "compares"):
        assert set(usage[key]) >= {"limit", "used", "remaining", "unlimited"}
    assert "available" in usage["bulk_trial"]


def test_free_scan_quota_enforced(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", 1)
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()
    assert client.post("/v1/scan", json={"url": "http://q1.example/"}).status_code == 200
    assert client.post("/v1/scan", json={"url": "http://q2.example/"}).status_code == 402   # Free quota hit
    _go_pro(client)
    assert client.post("/v1/scan", json={"url": "http://q3.example/"}).status_code == 200   # Pro: within 15/month


def test_free_history_limit(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "history_limit", 1)
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()
    client.post("/v1/scan", json={"url": "http://h1.example/"})
    client.post("/v1/scan", json={"url": "http://h2.example/"})
    rows = client.get("/api/scans").json()
    assert len(rows) == 1   # capped to history_limit for Free
