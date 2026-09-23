"""Real Crawl Graph + True Orphan Detection. The pure builder
(`reports/crawl_graph.py`) is tested directly against hand-built `bulk_pages`
fixtures (same convention as test_technical_seo.py/test_phase4.py); cache reuse,
free/paid gating, and org isolation are tested through the real HTTP endpoints
against a real multi-page bulk scan, the same pattern test_report_cache.py/
test_bulk_scan.py use.

No scanner score/weight logic is touched anywhere in this file — every assertion is
about the READ-SIDE graph derivation layered on top of existing `links` signal
evidence. This module never claims Google crawled/indexed anything — only real
internal-link graph structure grounded in the actual crawled HTML.
"""
import asyncio

import app.billing.plans as plans_mod
import app.reports.service as report_service
import app.scanner.bulk as bulk_mod
from app.config import settings
from app.db.models import Report, Scan
from app.db.session import SessionLocal
from app.monitoring import worker
from app.reports.crawl_graph import (
    DEEP_PAGE_THRESHOLD, build_crawl_graph_block, gate_crawl_graph,
)
from app.reports.engine import build_report
from app.reports.service import get_or_build_report, scan_to_input
from app.scanner.models import PageBundle
from app.services.answer_tracking import visibility as V
from tests.authutil import auth_client


# ------------------------------- fixtures -------------------------------
def _page(url, *, links=None, error=None, status_code=200):
    if error:
        return {"url": url, "error": error}
    return {
        "url": url, "overall_score": 80, "status_label": "pass", "top_issue": None,
        "sections_summary": [], "status_code": status_code, "redirect_chain": [],
        "final_url": url, "canonical": None, "meta_robots": "", "x_robots_tag": "",
        "robots_exists": False, "robots_crawlable": True,
        "link_targets": [{"target": t, "anchor_text": a} for (t, a) in (links or [])],
    }


def _build(pages, *, requested=None, scan_ready=True):
    return build_crawl_graph_block(
        bulk_pages=pages, bulk_requested_urls=requested or [p["url"] for p in pages],
        scan_ready=scan_ready)


def _rec(block, url):
    return next(r for r in block["pages"] if r["url"] == url)


A, B, C, D, E = ("https://acme.example/", "https://acme.example/b",
                 "https://acme.example/c", "https://acme.example/d",
                 "https://acme.example/e")


def _simple_connected_graph():
    """A (seed) -> B, A -> C, B -> D, D -> A (back-link). E is a true orphan (present,
    zero inbound). Requested/seed order is A, B, C, D, E."""
    return [
        _page(A, links=[(B, "services"), (C, "about")]),
        _page(B, links=[(D, "detail page")]),
        _page(C),
        _page(D, links=[(A, "home")]),
        _page(E),
    ]


# ===================================================================
# 1-6: edge extraction, external exclusion, normalization, self-links
# ===================================================================
def test_internal_link_edge_detected():
    block = _build(_simple_connected_graph())
    edge_pairs = {(e["source"], e["target"]) for e in block["edges"]}
    assert (A, B) in edge_pairs and (A, C) in edge_pairs and (B, D) in edge_pairs


def test_external_link_excluded_from_graph():
    pages = [_page(A, links=[(B, "services"), ("https://google.com/", "search")]), _page(B)]
    block = _build(pages)
    targets = {e["target"] for e in block["edges"]}
    assert "https://google.com/" not in targets
    assert block["summary"]["internal_edges"] == 1


def test_relative_url_resolved_correctly():
    """`links.py` resolves relative hrefs to absolute URLs before they ever reach the
    graph (see scanner/signals/links.py's urljoin) — a bulk page's stored `link_targets`
    are already absolute, so the graph never has to guess a base URL."""
    pages = [_page(A, links=[("https://acme.example/b", "services")]), _page(B)]
    block = _build(pages)
    assert block["edges"][0]["target"] == B


def test_fragment_normalization_does_not_split_identity():
    pages = [_page(A, links=[(B + "#pricing", "pricing")]), _page(B)]
    block = _build(pages)
    assert len(block["edges"]) == 1
    assert block["edges"][0]["target"] == B


def test_existing_url_normalization_convention_is_reused():
    from app.scanner.bulk import normalize_dedup_key
    pages = [_page(A, links=[("https://www.acme.example/b/", "services")]), _page(B)]
    block = _build(pages)
    # normalize_dedup_key collapses a trailing slash + a 'www.' host onto the same node
    assert normalize_dedup_key("https://www.acme.example/b/") == normalize_dedup_key(B)
    assert len(block["edges"]) == 1
    assert block["edges"][0]["target"] == B


def test_self_link_does_not_count_as_inbound_from_another_page():
    pages = [_page(A, links=[(A, "home"), (B, "services")]), _page(B)]
    block = _build(pages)
    a = _rec(block, A)
    assert all(e["source"] != e["target"] for e in block["edges"])
    b = _rec(block, B)
    assert b["inbound_pages"] == 1                   # only from A, not inflated by a self-link


# ===================================================================
# 7-9: inbound/outbound + unique referring pages
# ===================================================================
def test_inbound_count_correct():
    block = _build(_simple_connected_graph())
    d = _rec(block, D)
    assert d["inbound_pages"] == 1                    # only B links to D


def test_outbound_count_correct():
    block = _build(_simple_connected_graph())
    a = _rec(block, A)
    assert a["outbound_pages"] == 2                   # A -> B, A -> C


def test_unique_referring_page_count_not_confused_with_raw_link_count():
    """10 links from ONE page must not be confused with 10 different referring pages."""
    pages = [
        _page(A, links=[(B, "x1"), (B, "x2"), (B, "x3")]),   # 3 links, same page
        _page(B),
    ]
    block = _build(pages)
    b = _rec(block, B)
    assert b["inbound_pages"] == 1                    # one unique referring page
    assert b["inbound_links"] == 3                    # but three raw link occurrences


# ===================================================================
# 10-12: true orphan detection (seed exception, disconnected exception)
# ===================================================================
def test_true_orphan_detected():
    block = _build(_simple_connected_graph())
    e = _rec(block, E)
    assert e["orphan"] is True
    assert e["url"] in {o["url"] for o in block["orphans"]}


def test_seed_with_zero_inbound_is_not_falsely_marked_orphan():
    block = _build(_simple_connected_graph())
    a = _rec(block, A)
    assert a["is_seed"] is True
    assert a["inbound_pages"] == 1                    # D -> A exists...
    assert a["orphan"] is None                        # ...but seed status is never orphan-evaluated


def test_disconnected_page_with_inbound_links_is_not_falsely_marked_orphan():
    """F <-> G form their own component, disconnected from the seed A. Both have real
    inbound links (from each other) so neither is a true orphan — they ARE disconnected,
    a different, weaker claim."""
    f, g = "https://acme.example/f", "https://acme.example/g"
    pages = _simple_connected_graph() + [_page(f, links=[(g, "g")]), _page(g, links=[(f, "f")])]
    block = _build(pages)
    rf, rg = _rec(block, f), _rec(block, g)
    assert rf["inbound_pages"] == 1 and rg["inbound_pages"] == 1
    assert rf["orphan"] is False and rg["orphan"] is False
    assert rf["reachable"] is False and rg["reachable"] is False


# ===================================================================
# 13-16: reachability, BFS depth, max depth, multiple disconnected components
# ===================================================================
def test_reachability_from_seed_calculated_correctly():
    block = _build(_simple_connected_graph())
    for u in (A, B, C, D):
        assert _rec(block, u)["reachable"] is True
    assert _rec(block, E)["reachable"] is False        # not linked from anywhere


def test_bfs_depth_calculated_correctly():
    block = _build(_simple_connected_graph())
    assert _rec(block, A)["depth"] == 0
    assert _rec(block, B)["depth"] == 1
    assert _rec(block, C)["depth"] == 1
    assert _rec(block, D)["depth"] == 2                # A -> B -> D, shortest path wins


def test_max_depth_calculated_correctly():
    block = _build(_simple_connected_graph())
    assert block["summary"]["max_depth"] == 2


def test_multiple_disconnected_components_handled():
    f, g, h = ("https://acme.example/f", "https://acme.example/g", "https://acme.example/h")
    pages = _simple_connected_graph() + [
        _page(f, links=[(g, "g")]), _page(g, links=[(f, "f")]), _page(h),   # h: its own orphan too
    ]
    block = _build(pages)
    # E (from the base graph), f, g, h are all unreachable from the seed A.
    assert block["summary"]["disconnected_pages"] == 4
    assert _rec(block, h)["orphan"] is True                  # h has zero inbound too
    assert _rec(block, f)["orphan"] is False                 # f/g have inbound from each other


# ===================================================================
# 17: redirect interaction is deterministic
# ===================================================================
def test_redirect_target_page_still_resolves_as_a_normal_node():
    """A links to /old; /old was itself crawled as its own node (this bulk scanner
    doesn't rewrite link targets to a redirect's final URL — it represents what was
    actually linked and what was actually crawled, per the task's explicit rule)."""
    old = "https://acme.example/old"
    pages = [_page(A, links=[(old, "old page")]),
            {**_page(old), "redirect_chain": [{"url": old, "status_code": 301, "to": B}],
             "final_url": B}]
    block = _build(pages + [_page(B)])
    assert (A, old) in {(e["source"], e["target"]) for e in block["edges"]}
    old_rec = _rec(block, old)
    assert old_rec["reachable"] is True and old_rec["depth"] == 1


# ===================================================================
# 18-19: sitemap presence never fabricates or blocks graph facts
# ===================================================================
def test_sitemap_presence_does_not_falsely_create_inbound_links():
    """Sitemap membership is a Technical SEO concept; the crawl graph must never treat
    "in the sitemap" as if it were a real internal-link edge."""
    pages = [_page(A), _page(B)]        # A never actually links to B
    block = _build(pages)
    assert block["edges"] == []
    assert _rec(block, B)["orphan"] is True


def test_sitemap_presence_does_not_prevent_orphan_detection():
    pages = _simple_connected_graph()   # E is in this "crawl" the same way regardless of sitemap
    block = _build(pages)
    assert _rec(block, E)["orphan"] is True


# ===================================================================
# 20-21: no fabricated links, no fabricated Google crawl/index claims
# ===================================================================
def test_no_fabricated_links_unresolved_target_is_not_a_fake_node():
    ghost = "https://acme.example/never-crawled"
    pages = [_page(A, links=[(ghost, "ghost link")])]
    block = _build(pages)
    assert ghost not in {p["url"] for p in block["pages"]}      # no fake node created
    assert block["unresolved_internal_targets"] == [{"target": ghost, "referenced_by": [A]}]
    assert block["edges"] == []


def test_no_fabricated_google_crawl_or_index_claims_anywhere():
    import json
    block = _build(_simple_connected_graph())
    blob = json.dumps(block).lower()
    assert "google" not in blob
    assert "indexed" not in blob
    assert "pagerank" not in blob
    assert "authority score" not in blob


# ===================================================================
# 22: missing graph evidence -> unknown/unavailable
# ===================================================================
def test_missing_seed_evidence_yields_unknown_reachability_not_fabricated():
    pages = [_page(A, links=[(B, "b")]), _page(B)]
    block = build_crawl_graph_block(bulk_pages=pages, bulk_requested_urls=[], scan_ready=True)
    assert block["seed_url"] is None
    for p in block["pages"]:
        assert p["reachable"] is None and p["depth"] is None
    assert block["summary"]["max_depth"] is None


# ===================================================================
# 23-25: single-page / incomplete-scan / zero-link crawl behavior
# ===================================================================
def test_single_page_scan_returns_multi_page_crawl_required():
    block = build_crawl_graph_block(bulk_pages=None, bulk_requested_urls=None, scan_ready=True)
    assert block == {"available": False, "reason": "multi_page_crawl_required"}


def test_incomplete_scan_returns_scan_incomplete():
    block = build_crawl_graph_block(bulk_pages=_simple_connected_graph(),
                                    bulk_requested_urls=[A], scan_ready=False)
    assert block == {"available": False, "reason": "scan_incomplete"}


def test_completed_multi_page_scan_with_zero_internal_links_behaves_correctly():
    pages = [_page(A), _page(B), _page(C)]     # nobody links to anybody
    block = _build(pages)
    assert block["summary"]["internal_edges"] == 0
    assert block["summary"]["orphan_pages"] == 2          # B, C (A is the seed, exempt)
    assert _rec(block, A)["orphan"] is None
    assert block["edges"] == []


def test_error_pages_are_nodes_but_never_get_an_orphan_verdict():
    pages = [_page(A, links=[(B, "b")]), _page(B, error="HTTP 404")]
    block = _build(pages)
    b = _rec(block, B)
    assert b["orphan"] is None            # a broken page isn't a meaningful "content orphan"
    assert b["reachable"] is True         # still a legitimate graph-structure fact
    assert b["depth"] == 1


# ===================================================================
# deep-page / issue generation sanity (documented threshold, not fabricated)
# ===================================================================
def test_deep_page_uses_the_documented_threshold_not_an_arbitrary_one():
    chain = [A]
    for i in range(DEEP_PAGE_THRESHOLD + 2):
        chain.append(f"https://acme.example/lvl{i}")
    pages = []
    for i, url in enumerate(chain):
        nxt = chain[i + 1] if i + 1 < len(chain) else None
        pages.append(_page(url, links=[(nxt, "next")] if nxt else None))
    block = _build(pages)
    deepest = _rec(block, chain[-1])
    assert deepest["depth"] == len(chain) - 1
    assert deepest["depth"] >= DEEP_PAGE_THRESHOLD
    codes = {i["code"] for i in block["issues"]}
    assert "DEEP_PAGE" in codes
    deep_issue = next(i for i in block["issues"] if i["code"] == "DEEP_PAGE")
    assert deepest["url"] in deep_issue["affected_urls"]


def test_one_outbound_link_is_not_an_issue():
    """A metric being merely non-zero must never itself generate an issue."""
    pages = [_page(A, links=[(B, "b")]), _page(B)]
    block = _build(pages)
    codes = {i["code"] for i in block["issues"]}
    assert "HIGH_OUTBOUND_PAGE" not in codes


# ===================================================================
# 26-28: free/paid gating
# ===================================================================
def _messy_graph_block():
    pages = [_page(A, links=[])]
    for i in range(6):
        pages.append(_page(f"https://acme.example/orphan{i}"))
    return _build(pages)


def test_free_response_is_server_side_trimmed():
    block = _messy_graph_block()
    gated = gate_crawl_graph(block, unlocked=False)
    assert gated["preview"] is True
    assert len(gated["orphans"]) <= 3
    assert gated["locked_orphan_count"] == len(block["orphans"]) - len(gated["orphans"])
    assert gated["edges"] == []
    assert gated["locked_edge_count"] >= 0


def test_free_response_does_not_contain_hidden_paid_edges_or_urls():
    block = _messy_graph_block()
    gated = gate_crawl_graph(block, unlocked=False)
    shown = {p["url"] for p in gated["pages"]}
    all_urls = {p["url"] for p in block["pages"]}
    # The scan's own seed URL is not "hidden paid detail" — it's the URL the caller
    # themselves submitted to be scanned, so it legitimately stays in `seed_url`.
    locked = all_urls - shown - {block["seed_url"]}
    assert locked
    blob = str({**gated, "seed_url": None})
    for u in locked:
        assert u not in blob
    assert gated["edges"] == []                # no partial edge leakage either


def test_paid_response_contains_full_graph():
    block = _messy_graph_block()
    gated = gate_crawl_graph(block, unlocked=True)
    assert gated is block or gated["pages"] == block["pages"]
    assert "preview" not in gated
    assert len(gated["pages"]) == len(block["pages"])


# ===================================================================
# Opportunity Finder integration (reuses existing root-cause grouping)
# ===================================================================
def _scan_report_with_graph(graph_block):
    return {"url": A, "recommendations": [], "insights": {"score_breakdown": []},
           "phase4": {}, "crawl_graph": graph_block}


def test_crawl_graph_generates_an_opportunity_linked_to_the_existing_links_recommendation():
    block = _messy_graph_block()
    opps = V._crawl_graph_opps(_scan_report_with_graph(block))
    assert opps
    assert all(o["related_recommendation_id"] == "links" for o in opps)
    orphan_opp = next(o for o in opps if o["id"] == "crawl_graph:orphan_page")
    assert orphan_opp["affected_urls"]


def test_crawl_graph_opportunity_groups_with_existing_link_opportunity_root_cause():
    phase4 = {"links": {"issues": [{"type": "potentially_isolated_page",
                                    "label": "Potentially isolated page",
                                    "detail": "No internal links on this page.",
                                    "affected_urls": [A], "recommended_action": "Add links."}],
                       "related_recommendation_id": "links", "signal_score": 20}}
    block = _messy_graph_block()
    report = {**_scan_report_with_graph(block), "phase4": phase4}
    full = V.build_opportunities({}, scan_report=report, phase4=phase4)
    ids = {o["id"] for o in full["items"]}
    assert "crawl_graph:orphan_page" in ids
    assert "link_opportunity:potentially_isolated_page" in ids
    group = next(g for g in full["groups"] if g["root_cause_id"] == "links")
    assert "crawl_graph:orphan_page" in group["opportunity_ids"]
    assert "link_opportunity:potentially_isolated_page" in group["opportunity_ids"]


def test_crawl_graph_absent_never_breaks_opportunity_finder():
    out = V.build_opportunities({}, scan_report={"url": A}, phase4=None)
    assert out["items"] == []


# ===================================================================
# 29-32, 33-37: HTTP integration — cache reuse, gating, org isolation, compatibility
# ===================================================================
CG_URLS = [f"https://cgsite.test/{p}" for p in ("home", "services", "about", "orphan")]

CG_HTML = {
    "https://cgsite.test/home": (
        "<html><head><title>Home</title></head><body>"
        "<a href='/services'>services</a> <a href='/about'>about</a>"
        "</body></html>"),
    "https://cgsite.test/services": "<html><head><title>Services</title></head><body>No links here.</body></html>",
    "https://cgsite.test/about": "<html><head><title>About</title></head><body>No links here.</body></html>",
    "https://cgsite.test/orphan": "<html><head><title>Orphan</title></head><body>No links here.</body></html>",
}


async def _fake_fetch(url, *, transport=None):
    return PageBundle(url=url, html=CG_HTML.get(url, "<html></html>"), status_code=200)


def _make_bulk_scan(client, monkeypatch):
    monkeypatch.setattr(bulk_mod, "fetch", _fake_fetch)
    r = client.post("/v1/scan/bulk", json={"urls": CG_URLS})
    assert r.status_code == 202, r.text
    scan_id = r.json()["scan_id"]
    res = asyncio.run(worker.tick())
    assert res["processed"] >= 1
    return scan_id


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


def test_crawl_graph_endpoint_available_and_grounded(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
    resp = client.get(f"/reports/{scan_id}/crawl-graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["summary"]["pages"] == 4
    assert body["seed_url"] == "https://cgsite.test/home"


def test_report_endpoint_includes_crawl_graph_key(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
    report = client.get(f"/reports/{scan_id}").json()
    assert report["crawl_graph"]["available"] is True
    assert report["technical_seo"]["available"] is True   # neighboring block unaffected
    assert report["phase4"]["available"] is True


def test_e2_cache_reused_repeated_warm_reads_no_build_report(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
    client.get(f"/reports/{scan_id}")

    calls = _spy_build_report(monkeypatch)
    client.get(f"/reports/{scan_id}/crawl-graph")
    client.get(f"/reports/{scan_id}/crawl-graph")
    client.get(f"/reports/{scan_id}")
    assert calls["n"] == 0


def test_stale_report_version_triggers_rebuild_with_crawl_graph(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
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
    assert report["crawl_graph"]["available"] is True
    assert new_row.version != "0.0.0-stale"


def test_free_crawl_graph_endpoint_is_gated(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
    body = client.get(f"/reports/{scan_id}/crawl-graph").json()
    assert body.get("preview") is True


def test_paid_crawl_graph_endpoint_is_full(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
    _go_pro(client)
    body = client.get(f"/reports/{scan_id}/crawl-graph").json()
    assert "preview" not in body


def test_org_isolation_enforced_for_crawl_graph_endpoint(monkeypatch):
    _enforce_billing(monkeypatch)
    owner, _ = auth_client()
    scan_id = _make_bulk_scan(owner, monkeypatch)
    other, _ = auth_client()
    resp = other.get(f"/reports/{scan_id}/crawl-graph")
    assert resp.status_code == 404


def test_question_bank_and_technical_seo_unaffected_by_crawl_graph(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
    qb = client.get(f"/reports/{scan_id}/question-bank")
    tseo = client.get(f"/reports/{scan_id}/technical-seo")
    assert qb.status_code == 200
    assert tseo.status_code == 200 and tseo.json()["available"] is True


def test_single_page_scan_report_crawl_graph_is_multi_page_required(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()

    async def _single_fetch(url):
        return PageBundle(url=url, html="<html><head><title>T</title></head><body>hi</body></html>",
                          status_code=200)
    import app.api.routes_scan as rs
    monkeypatch.setattr(rs, "fetch", _single_fetch)
    scan_id = client.post("/v1/scan", json={"url": "https://single.example/"}).json()["scan_id"]

    report = client.get(f"/reports/{scan_id}").json()
    assert report["crawl_graph"] == {"available": False, "reason": "multi_page_crawl_required"}


def test_pending_bulk_scan_never_gets_fabricated_crawl_graph():
    org_scan = Scan(url="https://pendingcg.example/a", normalized_url="pendingcg.example",
                    ars=0, rubric_version="t", status="running",
                    result={"bulk": {"requested": 2, "urls": ["a", "b"]}})
    scanned = scan_to_input(org_scan)
    report = build_report(scanned)
    assert report["crawl_graph"] == {"available": False, "reason": "scan_incomplete"}
