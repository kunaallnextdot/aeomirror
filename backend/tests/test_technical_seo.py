"""Technical SEO & Indexability Intelligence. Pure builders
(`reports/technical_seo.py`) are tested directly against hand-built `sections`
fixtures (same convention as test_phase4.py/test_insights.py); cache reuse, free/paid
gating, and org isolation are tested through the real HTTP endpoints against a real
scanned page, the same pattern test_report_cache.py/test_gating_cleanup.py use.

No scanner score/weight logic is touched anywhere in this file — every assertion is
about the READ-SIDE indexability derivation layered on top of existing signal
evidence. This module never claims a URL IS indexed by Google — only technical
crawlability/indexability signals grounded in real evidence.
"""
import app.api.routes_scan as rs
import app.billing.plans as plans_mod
import app.reports.service as report_service
from app.config import settings
from app.db.models import Report, Scan
from app.db.session import SessionLocal
from app.reports.engine import build_report
from app.reports.service import get_or_build_report
from app.reports.technical_seo import build_technical_seo_block, gate_technical_seo
from app.scanner.models import PageBundle
from tests.authutil import auth_client


# ------------------------------- fixtures -------------------------------
def _metadata_section(*, canonical=None, robots_meta="", x_robots_tag="", score=57):
    return {
        "id": "metadata", "label": "Metadata", "weight": 12, "score": score,
        "status": "fail" if score < 45 else "warn" if score < 75 else "pass",
        "issues": [], "recommendations": [],
        "evidence": {
            "title": "T", "title_length": 20, "has_description": True,
            "has_canonical": bool(canonical), "robots_meta": robots_meta,
            "open_graph_tags": 0, "twitter_tags": 0,
            "canonical": canonical, "x_robots_tag": x_robots_tag,
        },
    }


def _robots_section(*, exists=True, crawlable=True):
    return {
        "id": "robots", "label": "robots.txt", "weight": 10,
        "score": 100 if crawlable else 15, "status": "pass" if crawlable else "fail",
        "issues": [], "recommendations": [],
        "evidence": {"exists": exists, "crawlable": crawlable, "ai_crawlers": {},
                    "blocked_paths": [], "sitemap_referenced": False},
    }


def _sitemap_section(*, urls=None, present=True):
    urls = urls or []
    return {
        "id": "sitemap", "label": "XML Sitemap", "weight": 7,
        "score": 100 if present else 30, "status": "pass" if present else "fail",
        "issues": [], "recommendations": [],
        "evidence": {"present": present, "is_index": False, "valid_xml": present,
                    "url_count": len(urls), "sample_urls": urls[:10], "urls": urls,
                    "referenced_in_robots": False},
    }


def _build(sections, *, url="https://acme.example/", status_code=200,
          redirect_chain=None, final_url=None, bulk_pages=None, scan_ready=True):
    return build_technical_seo_block(
        sections=sections, url=url, status_code=status_code,
        redirect_chain=redirect_chain or [], final_url=final_url or url,
        bulk_pages=bulk_pages, scan_ready=scan_ready)


def _rec(block, url="https://acme.example/"):
    return next(r for r in block["urls"] if r["url"] == url)


# ===================================================================
# 1-4: HTTP status intelligence
# ===================================================================
def test_200_url_classified_indexable():
    block = _build([_metadata_section(canonical="https://acme.example/"), _robots_section(),
                    _sitemap_section(urls=["https://acme.example/"])],
                   status_code=200)
    r = _rec(block)
    assert r["indexability_status"] == "indexable"
    assert r["indexable"] is True
    assert r["issues"] == []


def test_redirect_classified_correctly():
    chain = [{"url": "https://acme.example/", "status_code": 301, "to": "https://acme.example/new"}]
    block = _build([_metadata_section(), _robots_section()], status_code=200,
                   redirect_chain=chain, final_url="https://acme.example/new")
    r = _rec(block)
    assert r["redirect"] is True
    assert r["indexability_status"] == "redirected"
    assert "REDIRECT" in r["issues"]
    assert block["summary"]["redirected"] == 1


def test_404_classified_as_error():
    block = _build([_metadata_section(), _robots_section()], status_code=404)
    r = _rec(block)
    assert r["indexability_status"] == "error"
    assert "ERROR_4XX" in r["issues"]
    assert block["summary"]["errors"] == 1


def test_500_classified_as_error():
    block = _build([_metadata_section(), _robots_section()], status_code=500)
    r = _rec(block)
    assert r["indexability_status"] == "error"
    assert "ERROR_5XX" in r["issues"]


# ===================================================================
# 5-7: meta robots / X-Robots-Tag / noindex vs nofollow
# ===================================================================
def test_meta_robots_noindex_detected():
    block = _build([_metadata_section(robots_meta="noindex"), _robots_section()], status_code=200)
    r = _rec(block)
    assert r["indexability_status"] == "not_indexable"
    assert r["indexable"] is False
    assert "NOINDEX_META" in r["issues"]
    assert "noindex" in r["meta_robots"]


def test_x_robots_tag_noindex_detected():
    block = _build([_metadata_section(x_robots_tag="noindex"), _robots_section()], status_code=200)
    r = _rec(block)
    assert r["indexability_status"] == "not_indexable"
    assert "NOINDEX_XROBOTS" in r["issues"]
    assert "noindex" in r["x_robots_tag"]


def test_noindex_not_confused_with_nofollow():
    block = _build([_metadata_section(robots_meta="nofollow"), _robots_section()], status_code=200)
    r = _rec(block)
    assert r["indexability_status"] == "indexable"          # nofollow alone is NOT noindex
    assert "NOINDEX_META" not in r["issues"]
    assert r["meta_robots"] == ["nofollow"]


# ===================================================================
# 8: robots.txt blocked URL
# ===================================================================
def test_robots_blocked_url_handled_correctly():
    block = _build([_metadata_section(), _robots_section(exists=True, crawlable=False)], status_code=200)
    r = _rec(block)
    assert r["crawlable"] is False
    assert r["indexability_status"] == "blocked"
    assert "ROBOTS_BLOCKED" in r["issues"]
    assert block["summary"]["blocked"] == 1


def test_no_robots_txt_defaults_to_crawlable_not_fabricated_block():
    """No robots.txt at all -> default-allow (the real robots.txt spec default), not a
    fabricated "blocked" state."""
    block = _build([_metadata_section(), _robots_section(exists=False, crawlable=True)], status_code=200)
    r = _rec(block)
    assert r["crawlable"] is True
    assert r["indexability_status"] == "indexable"


# ===================================================================
# 9-12: canonical intelligence
# ===================================================================
def test_self_canonical_handled_correctly():
    block = _build([_metadata_section(canonical="https://acme.example/"), _robots_section()],
                   url="https://acme.example/", status_code=200)
    r = _rec(block)
    assert r["canonical_type"] == "self"
    assert "CANONICAL_EXTERNAL" not in r["issues"]
    assert r["indexability_status"] == "indexable"


def test_canonical_to_another_valid_url_handled_correctly():
    """Canonical -> another URL is NOT automatically an error, and does not by itself
    reduce indexability (per the task's explicit rule)."""
    bulk_pages = [
        {"url": "https://acme.example/a", "overall_score": 80, "status_label": "pass",
         "top_issue": None, "sections_summary": [], "status_code": 200,
         "redirect_chain": [], "final_url": "https://acme.example/a",
         "canonical": "https://acme.example/b", "meta_robots": "", "x_robots_tag": "",
         "robots_exists": True, "robots_crawlable": True},
        {"url": "https://acme.example/b", "overall_score": 80, "status_label": "pass",
         "top_issue": None, "sections_summary": [], "status_code": 200,
         "redirect_chain": [], "final_url": "https://acme.example/b",
         "canonical": "https://acme.example/b", "meta_robots": "", "x_robots_tag": "",
         "robots_exists": True, "robots_crawlable": True},
    ]
    block = _build([], bulk_pages=bulk_pages)
    a = _rec(block, "https://acme.example/a")
    assert a["canonical_type"] == "other"
    assert a["canonical_target_status"] == 200
    assert "CANONICAL_4XX" not in a["issues"] and "CANONICAL_5XX" not in a["issues"]
    assert a["indexability_status"] == "indexable"          # still indexable — not penalized


def test_canonical_to_404_detected():
    bulk_pages = [
        {"url": "https://acme.example/a", "overall_score": 80, "status_label": "pass",
         "top_issue": None, "sections_summary": [], "status_code": 200,
         "redirect_chain": [], "final_url": "https://acme.example/a",
         "canonical": "https://acme.example/gone", "meta_robots": "", "x_robots_tag": "",
         "robots_exists": True, "robots_crawlable": True},
        {"url": "https://acme.example/gone", "overall_score": 0, "status_label": "fail",
         "top_issue": None, "sections_summary": [], "status_code": 404,
         "redirect_chain": [], "final_url": "https://acme.example/gone",
         "canonical": None, "meta_robots": "", "x_robots_tag": "",
         "robots_exists": True, "robots_crawlable": True},
    ]
    block = _build([], bulk_pages=bulk_pages)
    a = _rec(block, "https://acme.example/a")
    assert a["canonical_type"] == "other"
    assert a["canonical_target_status"] == 404
    assert "CANONICAL_4XX" in a["issues"]
    codes = [i["code"] for i in block["issues"]]
    assert "CANONICAL_4XX" in codes


def test_canonical_to_redirect_detected():
    chain = [{"url": "https://acme.example/old", "status_code": 301, "to": "https://acme.example/new"}]
    bulk_pages = [
        {"url": "https://acme.example/a", "overall_score": 80, "status_label": "pass",
         "top_issue": None, "sections_summary": [], "status_code": 200,
         "redirect_chain": [], "final_url": "https://acme.example/a",
         "canonical": "https://acme.example/old", "meta_robots": "", "x_robots_tag": "",
         "robots_exists": True, "robots_crawlable": True},
        {"url": "https://acme.example/old", "overall_score": 80, "status_label": "pass",
         "top_issue": None, "sections_summary": [], "status_code": 200,
         "redirect_chain": chain, "final_url": "https://acme.example/new",
         "canonical": None, "meta_robots": "", "x_robots_tag": "",
         "robots_exists": True, "robots_crawlable": True},
    ]
    block = _build([], bulk_pages=bulk_pages)
    a = _rec(block, "https://acme.example/a")
    assert "CANONICAL_REDIRECT" in a["issues"]


def test_canonical_invalid_and_missing_are_not_fabricated_as_errors():
    block_missing = _build([_metadata_section(canonical=None), _robots_section()], status_code=200)
    r = _rec(block_missing)
    assert r["canonical_type"] == "missing"
    assert r["canonical"] is None
    assert r["indexability_status"] == "indexable"           # missing canonical != broken page

    block_invalid = _build([_metadata_section(canonical="not a url"), _robots_section()], status_code=200)
    r2 = _rec(block_invalid)
    assert r2["canonical_type"] == "invalid"
    assert "CANONICAL_INVALID" in r2["issues"]


# ===================================================================
# 13: redirect chain detected
# ===================================================================
def test_redirect_chain_detected_where_evidence_exists():
    chain = [
        {"url": "https://acme.example/", "status_code": 301, "to": "https://acme.example/mid"},
        {"url": "https://acme.example/mid", "status_code": 302, "to": "https://acme.example/end"},
    ]
    block = _build([_metadata_section(), _robots_section()], status_code=200,
                   redirect_chain=chain, final_url="https://acme.example/end")
    r = _rec(block)
    assert len(r["redirect_chain"]) == 2
    assert "REDIRECT_CHAIN" in r["issues"]


def test_redirect_loop_detected():
    chain = [
        {"url": "https://acme.example/", "status_code": 301, "to": "https://acme.example/a"},
        {"url": "https://acme.example/a", "status_code": 301, "to": "https://acme.example/"},
    ]
    block = _build([_metadata_section(), _robots_section()], status_code=200,
                   redirect_chain=chain, final_url="https://acme.example/")
    r = _rec(block)
    assert "REDIRECT_LOOP" in r["issues"]


# ===================================================================
# 14-17: sitemap <-> crawl parity
# ===================================================================
def _bulk_page(url, *, status_code=200, redirect_chain=None, meta_robots=""):
    return {"url": url, "overall_score": 80, "status_label": "pass", "top_issue": None,
            "sections_summary": [], "status_code": status_code,
            "redirect_chain": redirect_chain or [], "final_url": url,
            "canonical": url, "meta_robots": meta_robots, "x_robots_tag": "",
            "robots_exists": True, "robots_crawlable": True}


def test_sitemap_url_returning_404_detected():
    pages = [_bulk_page("https://acme.example/a"), _bulk_page("https://acme.example/broken", status_code=404)]
    sm = [_sitemap_section(urls=["https://acme.example/a", "https://acme.example/broken"])]
    block = _build(sm, bulk_pages=pages)
    r = _rec(block, "https://acme.example/broken")
    assert r["sitemap"] is True
    assert "ERROR_4XX" in r["issues"]


def test_sitemap_url_redirect_detected():
    chain = [{"url": "https://acme.example/old", "status_code": 301, "to": "https://acme.example/a"}]
    pages = [_bulk_page("https://acme.example/a"), _bulk_page("https://acme.example/old", redirect_chain=chain)]
    sm = [_sitemap_section(urls=["https://acme.example/a", "https://acme.example/old"])]
    block = _build(sm, bulk_pages=pages)
    r = _rec(block, "https://acme.example/old")
    assert r["sitemap"] is True
    assert r["redirect"] is True


def test_sitemap_url_with_noindex_detected():
    pages = [_bulk_page("https://acme.example/a"),
            _bulk_page("https://acme.example/hidden", meta_robots="noindex")]
    sm = [_sitemap_section(urls=["https://acme.example/a", "https://acme.example/hidden"])]
    block = _build(sm, bulk_pages=pages)
    r = _rec(block, "https://acme.example/hidden")
    assert r["sitemap"] is True
    assert "NOINDEX_META" in r["issues"]


def test_indexable_url_absent_from_sitemap_represented_correctly():
    pages = [_bulk_page("https://acme.example/a"), _bulk_page("https://acme.example/b")]
    sm = [_sitemap_section(urls=["https://acme.example/a"])]     # b is not in the sitemap
    block = _build(sm, bulk_pages=pages)
    b = _rec(block, "https://acme.example/b")
    assert b["sitemap"] is False
    assert "NOT_IN_SITEMAP" in b["issues"]                       # informational, not a fabricated error
    assert b["indexability_status"] == "indexable"


def test_sitemap_url_not_crawled_is_represented_not_assumed_deindexed():
    pages = [_bulk_page("https://acme.example/a")]
    sm = [_sitemap_section(urls=["https://acme.example/a", "https://acme.example/never-crawled"])]
    block = _build(sm, bulk_pages=pages)
    assert block["sitemap_urls_not_crawled"] == ["https://acme.example/never-crawled"]
    codes = [i["code"] for i in block["issues"]]
    assert "SITEMAP_URL_NOT_CRAWLED" in codes


# ===================================================================
# 18: no fabricated Google indexing claims
# ===================================================================
def test_no_fabricated_google_indexing_claims_anywhere_in_the_payload():
    import json
    pages = [_bulk_page("https://acme.example/a", status_code=404)]
    sm = [_sitemap_section(urls=["https://acme.example/a"])]
    block = _build(sm, bulk_pages=pages)
    blob = json.dumps(block).lower()
    assert "google" not in blob
    assert "search console" not in blob
    assert "ranking" not in blob
    assert "indexed by" not in blob


# ===================================================================
# 19: missing/unavailable evidence -> unknown/null, never fabricated
# ===================================================================
def test_missing_evidence_yields_unknown_not_fabricated():
    block = _build([], status_code=None)     # no metadata/robots/sitemap sections at all
    r = _rec(block)
    assert r["indexability_status"] == "unknown"
    assert r["status_code"] is None
    assert r["canonical"] is None
    assert r["meta_robots"] == [] and r["x_robots_tag"] == []


# ===================================================================
# 20-21: scan readiness + clean healthy state
# ===================================================================
def test_incomplete_scan_returns_scan_incomplete():
    block = _build([_metadata_section(), _robots_section()], scan_ready=False)
    assert block == {"available": False, "reason": "scan_incomplete"}


def test_completed_scan_with_no_issues_does_not_invent_issues():
    block = _build([_metadata_section(canonical="https://acme.example/"),
                    _robots_section(exists=True, crawlable=True),
                    _sitemap_section(urls=["https://acme.example/"])],
                   url="https://acme.example/", status_code=200)
    r = _rec(block)
    assert r["issues"] == []
    assert block["issues"] == []
    assert block["summary"] == {"total_urls": 1, "indexable": 1, "not_indexable": 0,
                                "blocked": 0, "redirected": 0, "errors": 0, "unknown": 0}


# ===================================================================
# 22-24: free/paid gating
# ===================================================================
def _messy_block():
    pages = [_bulk_page(f"https://acme.example/p{i}", status_code=404) for i in range(6)]
    return _build([], bulk_pages=pages)


def test_free_response_is_server_side_trimmed():
    block = _messy_block()
    gated = gate_technical_seo(block, unlocked=False)
    assert gated["preview"] is True
    assert len(gated["urls"]) <= 3
    assert gated["locked_url_count"] == len(block["urls"]) - len(gated["urls"])
    assert len(gated["issues"]) <= 3
    assert gated["locked_issue_count"] >= 0


def test_paid_response_contains_full_grounded_data():
    block = _messy_block()
    gated = gate_technical_seo(block, unlocked=True)
    assert gated is block or gated["urls"] == block["urls"]
    assert len(gated["urls"]) == 6
    assert "preview" not in gated


def test_free_response_does_not_contain_hidden_paid_urls():
    block = _messy_block()
    gated = gate_technical_seo(block, unlocked=False)
    shown_urls = {u["url"] for u in gated["urls"]}
    all_urls = {u["url"] for u in block["urls"]}
    locked_urls = all_urls - shown_urls
    assert locked_urls                                        # sanity: some really are locked
    # none of the locked URLs leak into the free payload anywhere
    blob = str(gated)
    for u in locked_urls:
        assert u not in blob
    assert gated["sitemap_urls_not_crawled"] == []


# ===================================================================
# 25-32: HTTP integration — cache reuse, gating, org isolation, compatibility
# ===================================================================
GOOD_HTML_TSEO = """<!doctype html><html><head>
<title>Cold Brew Buyer's Guide</title>
<meta name="description" content="A guide.">
<link rel="canonical" href="https://tseo.example/g">
</head><body><h1>Cold Brew</h1><p>Cold brew coffee brewing guide with lots of detail here about the process.</p></body></html>"""

GOOD_ROBOTS_TSEO = """User-agent: *
Allow: /
Sitemap: https://tseo.example/sitemap.xml"""


async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML_TSEO, robots_txt=GOOD_ROBOTS_TSEO,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>https://tseo.example/g</loc></url></urlset>",
                      status_code=200, headers={})


def _make_scan(client, monkeypatch, url="https://tseo.example/g"):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    return client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def _enforce_billing(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", 50)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def _spy_build_report(monkeypatch):
    calls = {"n": 0}
    original = report_service.build_report

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(report_service, "build_report", wrapper)
    return calls


def test_technical_seo_endpoint_available_and_grounded(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    resp = client.get(f"/reports/{scan_id}/technical-seo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total_urls"] == 1
    assert body["urls"][0]["url"] == "https://tseo.example/g"
    assert body["urls"][0]["canonical_type"] == "self"


def test_report_endpoint_includes_technical_seo_key(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    report = client.get(f"/reports/{scan_id}").json()
    assert report["technical_seo"]["available"] is True
    assert "phase4" in report and report["phase4"]["available"] is True   # Phase 4 unaffected


def test_e2_cache_reused_repeated_warm_reads_no_build_report(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    client.get(f"/reports/{scan_id}")   # warm the cache

    calls = _spy_build_report(monkeypatch)
    client.get(f"/reports/{scan_id}/technical-seo")
    client.get(f"/reports/{scan_id}/technical-seo")
    client.get(f"/reports/{scan_id}")
    assert calls["n"] == 0


def test_stale_report_version_triggers_rebuild_with_technical_seo(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    client.get(f"/reports/{scan_id}")

    db = SessionLocal()
    try:
        row = (db.query(Report).filter(Report.scan_id == scan_id)
               .order_by(Report.generated_at.desc()).first())
        row.version = "0.0.0-stale"
        db.commit()
        scan_row = db.get(Scan, scan_id)
        calls = _spy_build_report(monkeypatch)
        report, new_row = get_or_build_report(db, scan_row)
    finally:
        db.close()
    assert calls["n"] == 1
    assert report["technical_seo"]["available"] is True
    assert new_row.version != "0.0.0-stale"


def test_free_technical_seo_endpoint_is_gated(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    body = client.get(f"/reports/{scan_id}/technical-seo").json()
    assert body.get("preview") is True


def test_paid_technical_seo_endpoint_is_full(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    _go_pro(client)
    body = client.get(f"/reports/{scan_id}/technical-seo").json()
    assert "preview" not in body


def test_org_isolation_enforced_for_technical_seo_endpoint(monkeypatch):
    _enforce_billing(monkeypatch)
    owner, _ = auth_client()
    scan_id = _make_scan(owner, monkeypatch)
    other, _ = auth_client()
    resp = other.get(f"/reports/{scan_id}/technical-seo")
    assert resp.status_code == 404


def test_question_bank_and_opportunity_endpoints_unaffected(monkeypatch):
    """Sanity check that adding report["technical_seo"] didn't disturb the neighboring
    additive blocks it sits alongside in build_report()'s output."""
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    qb = client.get(f"/reports/{scan_id}/question-bank")
    av = client.get(f"/reports/{scan_id}/ai-visibility")
    assert qb.status_code == 200
    assert av.status_code == 200 and av.json()["available"] is False   # no monitor — expected


def test_pending_scan_never_gets_fabricated_technical_seo():
    org_scan = Scan(url="https://pendingtseo.example/", normalized_url="pendingtseo.example",
                    ars=0, rubric_version="t", status="running",
                    result={"bulk": {"requested": 2, "urls": ["a", "b"]}})
    from app.reports.service import scan_to_input
    scanned = scan_to_input(org_scan)
    report = build_report(scanned)
    assert report["technical_seo"] == {"available": False, "reason": "scan_incomplete"}
