"""Phase 6 report tests: recommendation engine, scorecard, JSON/CSV/PDF exports,
the API endpoints (auth + org-scoped), and edge cases (large / small / missing data)."""
import csv
import io
import json

from fastapi.testclient import TestClient

import app.api.routes_scan as rs
import app.billing.plans as plans_mod
from app.config import settings
from app.main import app
from app.reports.engine import REPORT_VERSION, build_report
from app.reports.exporters import to_csv_bytes, to_json_bytes
from app.reports.pdf import build_pdf
from app.scanner.models import PageBundle
from tests.authutil import auth_client
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS


# ------------------------------- fixtures -------------------------------
def _sections(scores):
    """Build signal sections from (id,label,weight,score) tuples."""
    out = []
    for sid, label, weight, score in scores:
        status = "pass" if score >= 75 else "warn" if score >= 45 else "fail"
        out.append({
            "id": sid, "label": label, "weight": weight, "score": score, "status": status,
            "issues": [] if status == "pass" else [f"{label} issue A", f"{label} issue B"],
            "recommendations": [f"Fix {label} step 1", f"Fix {label} step 2"],
            "evidence": {"n": 2, "flag": True},
        })
    return out


FULL = [
    ("robots", "robots.txt", 10, 20), ("sitemap", "XML Sitemap", 7, 45),
    ("metadata", "Metadata", 12, 60), ("schema", "Structured Data", 15, 15),
    ("content", "Content Structure", 12, 85), ("links", "Internal Linking", 8, 55),
    ("performance", "Performance Signals", 10, 35), ("accessibility", "Accessibility", 9, 70),
    ("freshness", "Freshness", 7, 40), ("ai_readiness", "AI Extractability", 10, 25),
]


def _scan(overall=44):
    return {"scan_id": "s1", "url": "https://example.com", "domain": "example.com",
            "overall_score": overall, "scanner_version": "3.0.0",
            "scanned_at": "2026-07-25T00:00:00Z", "sections": _sections(FULL)}


# ------------------------------- engine -------------------------------
def test_report_has_all_required_recommendation_fields():
    rep = build_report(_scan())
    assert rep["report_version"] == REPORT_VERSION
    assert rep["recommendation_count"] == 9   # 9 non-passing signals
    r = rep["recommendations"][0]
    for field in ("issue_title", "severity", "category", "description", "evidence",
                  "business_impact", "ai_visibility_impact", "estimated_fix_time",
                  "difficulty", "priority"):
        assert field in r and r[field] not in (None, ""), field
    fx = r["fix_template"]
    for field in ("problem", "explanation", "recommended_fix", "implementation_example",
                  "expected_outcome"):
        assert field in fx


def test_recommendations_sorted_by_priority():
    rep = build_report(_scan())
    ranks = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    seq = [ranks[r["priority"]] for r in rep["recommendations"]]
    assert seq == sorted(seq)
    # schema (weight 15, score 15) must be the top critical item
    assert rep["recommendations"][0]["category"] == "Schema"


def test_categories_and_priority_levels_are_valid():
    rep = build_report(_scan())
    valid_cat = {"Crawlability", "Metadata", "Schema", "Content", "Performance",
                 "Accessibility", "Internal Linking", "Indexability", "AI Extractability"}
    valid_pri = {"Critical", "High", "Medium", "Low"}
    for r in rep["recommendations"]:
        assert r["category"] in valid_cat
        assert r["priority"] in valid_pri
        assert r["severity"] in valid_pri


def test_scorecard_shape():
    sc = build_report(_scan(44))["scorecard"]
    assert sc["overall_score"] == 44 and sc["grade"] == "F"
    assert len(sc["category_scores"]) == 9        # 9 categories
    assert any(s["label"] == "Content Structure" for s in sc["strengths"])  # score 85
    assert any(w["label"] == "Structured Data" for w in sc["weaknesses"])   # score 15
    assert len(sc["top_priorities"]) <= 10 and sc["top_priorities"]
    assert isinstance(sc["quick_wins"], list)
    assert sum(sc["issue_counts"].values()) == 9
    assert sc["summary"]


def test_passing_signal_is_a_strength_not_a_recommendation():
    rep = build_report(_scan())
    labels = {r["signal_label"] for r in rep["recommendations"]}
    assert "Content Structure" not in labels   # score 85 -> strength only


# ------------------------------- edge cases -------------------------------
def test_empty_report_is_valid():
    rep = build_report({"scan_id": "e", "url": "https://x.com", "domain": "x.com", "sections": []})
    assert rep["recommendation_count"] == 0
    assert rep["scorecard"]["overall_score"] is not None
    assert build_pdf(rep)[:5] == b"%PDF-"


def test_missing_fields_do_not_crash():
    rep = build_report({"sections": [{"id": "robots", "score": 10}]})
    assert rep["recommendation_count"] == 1
    assert to_json_bytes(rep) and to_csv_bytes(rep)
    assert build_pdf(rep)[:5] == b"%PDF-"


def test_large_report_renders():
    big = _scan()
    big["sections"] = _sections(FULL) * 5   # 50 sections
    rep = build_report(big)
    assert rep["recommendation_count"] == 45
    pdf = build_pdf(rep)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 20000


# ------------------------------- exporters -------------------------------
def test_json_export_roundtrips():
    rep = build_report(_scan())
    parsed = json.loads(to_json_bytes(rep))
    assert parsed["report_version"] == REPORT_VERSION
    assert len(parsed["recommendations"]) == rep["recommendation_count"]


def test_csv_export_has_header_and_rows():
    rep = build_report(_scan())
    raw = to_csv_bytes(rep).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(raw)))
    assert rows[0][:4] == ["priority", "severity", "category", "issue_title"]
    assert len(rows) == rep["recommendation_count"] + 1  # + header


def test_pdf_is_valid_and_reasonably_sized():
    pdf = build_pdf(build_report(_scan()))
    assert pdf[:5] == b"%PDF-"
    assert 8000 < len(pdf) < 2_000_000   # non-trivial but not runaway


# ------------------------------- API (auth + org-scoped) -------------------------------
async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"content-encoding": "gzip", "cache-control": "max-age=60"})


def _make_scan(client, monkeypatch, url="https://report-api.example/"):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    return client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def test_report_endpoints_require_auth():
    anon = TestClient(app)
    assert anon.get("/reports/whatever").status_code == 401
    assert anon.get("/reports/whatever/pdf").status_code == 401


def test_get_report_json_and_downloads(monkeypatch):
    client, _ = auth_client(organization_name="Report Org")
    scan_id = _make_scan(client, monkeypatch)

    rep = client.get(f"/reports/{scan_id}")
    assert rep.status_code == 200
    body = rep.json()
    assert body["scan_id"] == scan_id and "scorecard" in body

    j = client.get(f"/reports/{scan_id}/json")
    assert j.status_code == 200 and j.headers["content-type"].startswith("application/json")
    assert "attachment" in j.headers["content-disposition"]

    c = client.get(f"/reports/{scan_id}/csv")
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv")

    p = client.get(f"/reports/{scan_id}/pdf")
    assert p.status_code == 200 and p.headers["content-type"] == "application/pdf"
    assert p.content[:5] == b"%PDF-"


def test_report_scoped_to_org(monkeypatch):
    owner_a, _ = auth_client()
    owner_b, _ = auth_client()
    scan_id = _make_scan(owner_a, monkeypatch, "https://org-a-report.example/")
    assert owner_a.get(f"/reports/{scan_id}").status_code == 200
    # another org cannot read the report or export it
    assert owner_b.get(f"/reports/{scan_id}").status_code == 404
    assert owner_b.get(f"/reports/{scan_id}/pdf").status_code == 404


def test_export_history_recorded(monkeypatch):
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch, "https://history.example/")
    client.get(f"/reports/{scan_id}/pdf")
    client.get(f"/reports/{scan_id}/csv")
    # verify rows landed in report_exports
    from app.db.models import ReportExport
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        rows = db.query(ReportExport).filter(ReportExport.scan_id == scan_id).all()
        formats = {r.format for r in rows}
    finally:
        db.close()
    assert {"pdf", "csv"} <= formats


# ------------------- $9 pay-then-download export gating (billing enforced) -------------------
def _enforce_billing(monkeypatch, scan_limit=50):
    """Turn on plan enforcement; raise the Free scan cap so a test can make >1 scan."""
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", scan_limit)


def _buy_report(client, scan_id):
    ref = client.post("/billing/checkout", json={"plan_code": "report", "scan_id": scan_id}).json()["reference"]
    assert client.post("/billing/checkout/complete", json={"reference": ref}).status_code == 200


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def test_free_exports_locked_without_payment(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch)
    assert client.get(f"/reports/{sid}/access").json()["unlocked"] is False
    for fmt in ("pdf", "json", "csv"):
        r = client.get(f"/reports/{sid}/{fmt}")
        assert r.status_code == 402, fmt
        assert r.json()["unlock"] == {"kind": "report", "scan_id": sid}
        assert "$9" in r.json()["detail"]
    # viewing the on-screen report stays free
    assert client.get(f"/reports/{sid}").status_code == 200


def test_one_payment_unlocks_all_three_formats(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch)
    _buy_report(client, sid)
    assert client.get(f"/reports/{sid}/access").json()["unlocked"] is True
    assert client.get(f"/reports/{sid}/pdf").status_code == 200
    assert client.get(f"/reports/{sid}/json").status_code == 200
    assert client.get(f"/reports/{sid}/csv").status_code == 200


def test_payment_for_one_scan_does_not_unlock_another(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    sid_a = _make_scan(client, monkeypatch, "https://a-report.example/")
    sid_b = _make_scan(client, monkeypatch, "https://b-report.example/")
    _buy_report(client, sid_a)
    assert client.get(f"/reports/{sid_a}/pdf").status_code == 200          # A unlocked
    assert client.get(f"/reports/{sid_b}/access").json()["unlocked"] is False
    assert client.get(f"/reports/{sid_b}/pdf").status_code == 402          # B still locked


def test_pro_exports_always_unlocked(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch)
    _go_pro(client)
    assert client.get(f"/reports/{sid}/access").json()["unlocked"] is True
    assert client.get(f"/reports/{sid}/pdf").status_code == 200


def test_report_checkout_persists_scan_id_for_auto_unlock(monkeypatch):
    """The pending Payment carries scan_id at checkout; completion only flips status,
    so unlock is automatic and scan-scoped."""
    from app.db.models import KIND_ONE_TIME_REPORT, PAY_PENDING, Payment
    from app.db.session import SessionLocal
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch)
    ref = client.post("/billing/checkout", json={"plan_code": "report", "scan_id": sid}).json()["reference"]
    db = SessionLocal()
    try:
        p = db.query(Payment).filter(Payment.reference == ref).first()
        assert p.scan_id == sid and p.kind == KIND_ONE_TIME_REPORT and p.status == PAY_PENDING
    finally:
        db.close()
    client.post("/billing/checkout/complete", json={"reference": ref})
    assert client.get(f"/reports/{sid}/access").json()["unlocked"] is True
