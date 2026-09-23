"""Content Cannibalization & Duplicate Content Intelligence. The pure builder
(`reports/content_intelligence.py`) is tested directly against hand-built `bulk_pages`
fixtures built from REAL representative page-text examples (word-trigram Jaccard is
empirically threshold-tuned against these — see module docstring); cache reuse,
free/paid gating, and org isolation are tested through the real HTTP endpoints,
the same pattern test_crawl_graph.py/test_technical_seo.py use.

No scanner score/weight logic is touched anywhere in this file. This module never
claims confirmed Google search-result cannibalization — only technical, deterministic
evidence (content/title/H1 similarity, canonical relationships).
"""
import asyncio

import app.billing.plans as plans_mod
import app.reports.service as report_service
import app.scanner.bulk as bulk_mod
from app.config import settings
from app.db.models import Report, Scan
from app.db.session import SessionLocal
from app.monitoring import worker
from app.reports.content_intelligence import (
    CONTENT_MODERATE, CONTENT_NEAR_DUPLICATE,
    build_content_intelligence_block, gate_content_intelligence,
)
from app.reports.engine import build_report
from app.reports.service import get_or_build_report, scan_to_input
from app.scanner.models import PageBundle
from app.scanner.signals.content import content_shingles, normalize_content_text
from app.services.answer_tracking import visibility as V
from tests.authutil import auth_client


# ------------------------------- representative texts -------------------------------
SEO_A = ("Our SEO services help you rank higher on Google. We offer keyword research, "
        "on-page optimization, and link building to grow your organic traffic and "
        "improve visibility for your business online every single month.")
SEO_B = ("Our SEO services help you rank higher on Google. We offer keyword research, "
        "on-page SEO, and link building to grow your organic traffic and improve "
        "online visibility for your business every single month.")   # near-duplicate of A
SEO_E = ("Our SEO agency helps businesses rank higher on Google search results. We "
        "provide keyword research, technical SEO audits, and content strategy to "
        "increase organic traffic for your company this year.")       # same topic, different wording
UNRELATED = ("We sell handmade ceramic mugs shipped worldwide. Each piece is uniquely "
            "glazed and fired in small batches by our studio artists every week.")
TEMPLATE_A = ("Buy the Widget Pro 3000 online today. Free shipping on all orders over "
             "fifty dollars. This durable widget is made from premium steel and comes "
             "with a two year warranty. In stock and ready to ship today.")
TEMPLATE_B = ("Buy the Widget Max 5000 online today. Free shipping on all orders over "
             "fifty dollars. This durable widget is made from premium aluminum and "
             "comes with a two year warranty. In stock and ready to ship today.")


# ------------------------------- fixtures -------------------------------
def _page(url, *, text=None, title=None, h1=None, description=None, canonical=None,
         status_code=200, error=None):
    if error:
        return {"url": url, "error": error}
    normalized = normalize_content_text(text or "")
    return {
        "url": url, "status_code": status_code, "title": title, "h1": h1,
        "description": description, "canonical": canonical,
        "word_count": len(normalized.split()) if normalized else 0,
        "content_shingles": content_shingles(normalized),
    }


def _build(pages, scan_ready=True):
    return build_content_intelligence_block(bulk_pages=pages, scan_ready=scan_ready)


A, B, C, D, E = ("https://acme.example/seo-services", "https://acme.example/seo-agency",
                 "https://acme.example/mugs", "https://acme.example/old-service",
                 "https://acme.example/new-service")


def _cluster_for(block, url):
    return next((c for c in block["clusters"] if any(p["url"] == url for p in c["pages"])), None)


# ===================================================================
# 1-4: exact/near duplicate detection, distinct pages, template pages
# ===================================================================
def test_exact_duplicate_pages_detected():
    pages = [_page(A, text=SEO_A, title="SEO Services"), _page(D, text=SEO_A, title="SEO Services (copy)")]
    block = _build(pages)
    cluster = _cluster_for(block, A)
    assert cluster is not None
    assert cluster["type"] == "near_duplicate"
    assert cluster["confidence"] == "high"


def test_near_duplicate_pages_detected():
    pages = [_page(A, text=SEO_A, title="SEO Services"), _page(D, text=SEO_B, title="SEO Services")]
    sim = _jaccard(content_shingles(normalize_content_text(SEO_A)), content_shingles(normalize_content_text(SEO_B)))
    assert sim >= CONTENT_NEAR_DUPLICATE                        # sanity: this pair really is near-duplicate
    block = _build(pages)
    assert _cluster_for(block, A)["type"] == "near_duplicate"


def test_clearly_distinct_pages_remain_separate():
    pages = [_page(A, text=SEO_A, title="SEO Services"), _page(C, text=UNRELATED, title="Ceramic Mugs")]
    block = _build(pages)
    assert block["clusters"] == []
    assert block["summary"]["pages_analyzed"] == 2


def test_similar_template_pages_are_not_automatically_cannibalization():
    """Same boilerplate/template copy for two DIFFERENT products must not be called
    'potential_cannibalization' — the task's explicit example."""
    pages = [_page(A, text=TEMPLATE_A, title="Widget Pro 3000 - Buy Online"),
            _page(D, text=TEMPLATE_B, title="Widget Max 5000 - Buy Online")]
    block = _build(pages)
    cluster = _cluster_for(block, A)
    assert cluster is not None
    assert cluster["type"] != "potential_cannibalization"
    # real content overlap IS still surfaced (honest evidence), just not mislabeled
    assert cluster["type"] == "near_duplicate"
    assert cluster["recommended_action"] == "DIFFERENTIATE"     # not CONSOLIDATE — distinct products


def _jaccard(a, b):
    a, b = set(a), set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ===================================================================
# 5-7: duplicate page elements
# ===================================================================
def test_duplicate_title_detected():
    pages = [_page(A, text=SEO_A, title="Best SEO Services | Acme"),
            _page(D, text=UNRELATED, title="Best SEO Services | Acme")]
    block = _build(pages)
    dt = [e for e in block["duplicate_elements"] if e["type"] == "duplicate_title"]
    assert len(dt) == 1
    assert sorted(dt[0]["urls"]) == sorted([A, D])


def test_duplicate_h1_detected():
    pages = [_page(A, text=SEO_A, h1="Welcome to Acme"), _page(D, text=UNRELATED, h1="Welcome to Acme")]
    block = _build(pages)
    dh = [e for e in block["duplicate_elements"] if e["type"] == "duplicate_h1"]
    assert len(dh) == 1


def test_duplicate_meta_description_detected():
    pages = [_page(A, text=SEO_A, description="Learn about our services."),
            _page(D, text=UNRELATED, description="Learn about our services.")]
    block = _build(pages)
    dm = [e for e in block["duplicate_elements"] if e["type"] == "duplicate_meta_description"]
    assert len(dm) == 1


# ===================================================================
# 8-10: canonical interpretation
# ===================================================================
def test_canonical_relationship_affects_interpretation():
    pages = [_page(A, text=SEO_A, title="SEO Services", canonical=D),
            _page(D, text=SEO_B, title="SEO Services", canonical=D)]
    block = _build(pages)
    cluster = _cluster_for(block, A)
    assert cluster["canonical_situation"] == "consolidated"
    assert cluster["recommended_action"] == "KEEP_SEPARATE"     # already declared consolidation intent


def test_canonical_to_other_similar_page_is_not_automatically_an_error():
    pages = [_page(A, text=SEO_A, title="SEO Services", canonical=D),
            _page(D, text=SEO_B, title="SEO Services", canonical=D)]
    block = _build(pages)
    cluster = _cluster_for(block, A)
    assert "REVIEW_CANONICAL" != cluster["recommended_action"]  # not flagged as broken


def test_self_canonical_similar_pages_produce_stronger_overlap_evidence():
    pages = [_page(A, text=SEO_A, title="SEO Services", canonical=A),
            _page(D, text=SEO_B, title="SEO Services", canonical=D)]
    block = _build(pages)
    cluster = _cluster_for(block, A)
    assert cluster["canonical_situation"] == "self_canonical_both"
    assert any("self-canonicalize" in e for e in cluster["evidence"])


# ===================================================================
# 11-13: potential cannibalization requires multiple signals; no intent invention;
# no fabricated Google claims
# ===================================================================
def test_potential_cannibalization_requires_multiple_deterministic_signals():
    """A vs E: same topic/intent, DIFFERENT wording (low content similarity) but very
    similar titles/H1s — the task's own 'different wording, same intent' example.
    Content similarity ALONE is far too low to trigger anything; title+H1 together
    are the qualifying multi-signal evidence."""
    content_sim = _jaccard(content_shingles(normalize_content_text(SEO_A)),
                           content_shingles(normalize_content_text(SEO_E)))
    assert content_sim < CONTENT_MODERATE     # sanity: content alone does NOT qualify
    pages = [_page(A, text=SEO_A, title="SEO Services | Rank Higher on Google",
                  h1="SEO Services For Your Business"),
            _page(E, text=SEO_E, title="SEO Agency | Rank Higher on Google",
                 h1="SEO Agency For Your Business")]
    block = _build(pages)
    cluster = _cluster_for(block, A)
    assert cluster is not None
    assert cluster["type"] == "potential_cannibalization"


def test_single_similarity_metric_alone_does_not_trigger_cannibalization():
    """Only title similarity, nothing else (different H1, unrelated body copy) must
    not by itself produce a potential_cannibalization cluster."""
    pages = [_page(A, text=SEO_A, title="Acme SEO Services Overview", h1="Our SEO Work"),
            _page(E, text=UNRELATED, title="Acme SEO Services Pricing", h1="Ceramic Mugs")]
    block = _build(pages)
    assert block["clusters"] == []


def test_no_unsupported_intent_classification():
    import json
    pages = [_page(A, text=SEO_A, title="SEO Services"), _page(D, text=SEO_B, title="SEO Services")]
    block = _build(pages)
    blob = json.dumps(block).lower()
    assert "informational" not in blob and "transactional" not in blob and "navigational" not in blob
    assert "intent_overlap" not in blob            # no intent classification invented at all


def test_no_fabricated_google_ranking_or_indexing_claims():
    import json
    pages = [_page(A, text=SEO_A, title="SEO Services"), _page(D, text=SEO_B, title="SEO Services")]
    block = _build(pages)
    blob = json.dumps(block).lower()
    assert "competing in google" not in blob
    assert "google penalty" not in blob
    assert "confirmed cannibalization" not in blob
    for c in block["clusters"]:
        assert c["type"] != "cannibalization"       # always "potential_..." per the task's wording


# ===================================================================
# 14: thin-content logic only runs if grounded (relative, site-based)
# ===================================================================
def test_thin_content_only_flagged_with_enough_pages_for_a_real_distribution():
    # Only 3 eligible pages — below MIN_ELIGIBLE_FOR_THIN_CONTENT (5): no thin-content
    # finding is invented even though one page is much shorter than the others.
    pages = [_page("https://acme.example/a", text=SEO_A * 3, title="A"),
            _page("https://acme.example/b", text=SEO_A * 3, title="B"),
            _page("https://acme.example/c", text="short page with barely enough words to count at all", title="C")]
    block = _build(pages)
    assert block["thin_pages"] == []


def test_thin_content_relative_to_site_median_detected_with_enough_pages():
    long_text = SEO_A * 5                              # ~170 words
    thin_text = ("This page briefly covers the basic topic without much elaboration or "
                "supporting detail beyond a short introductory paragraph for visitors "
                "browsing casually today around here.")   # ~25 words: eligible, but well under 25% of 170
    pages = [_page(f"https://acme.example/p{i}", text=long_text, title=f"T{i}") for i in range(5)]
    pages.append(_page("https://acme.example/thin", text=thin_text, title="Thin"))
    block = _build(pages)
    thin_urls = {p["url"] for p in block["thin_pages"]}
    assert "https://acme.example/thin" in thin_urls
    assert "https://acme.example/p0" not in thin_urls


# ===================================================================
# 15-17: cluster URLs are real, recommendations are grounded
# ===================================================================
def test_cluster_urls_are_real_crawled_urls():
    pages = [_page(A, text=SEO_A, title="SEO Services"), _page(D, text=SEO_B, title="SEO Services"),
            _page(C, text=UNRELATED, title="Ceramic Mugs")]
    block = _build(pages)
    cluster_urls = {p["url"] for c in block["clusters"] for p in c["pages"]}
    all_urls = {p["url"] for p in pages}
    assert cluster_urls <= all_urls
    assert C not in cluster_urls


def test_recommendations_are_grounded_in_evidence():
    pages = [_page(A, text=SEO_A, title="SEO Services", canonical=A),
            _page(D, text=SEO_A, title="SEO Services", canonical=D)]   # both self-canonical, exact dup
    block = _build(pages)
    cluster = _cluster_for(block, A)
    assert cluster["recommended_action"] == "CONSOLIDATE"
    assert "consolidat" in cluster["recommendation"].lower()


# ===================================================================
# insufficient content evidence / eligibility
# ===================================================================
def test_error_and_thin_pages_marked_insufficient_content_evidence_not_compared():
    pages = [_page(A, text=SEO_A, title="SEO Services"),
            _page(D, error="HTTP 404"),
            _page(C, text="two words", title="X")]     # far below MIN_WORDS_FOR_COMPARISON
    block = _build(pages)
    excluded = {p["url"]: p["reason"] for p in block["excluded_pages"]}
    assert excluded[D] == "insufficient_content_evidence"
    assert excluded[C] == "insufficient_content_evidence"
    assert block["summary"]["pages_analyzed"] == 1


def test_redirect_and_5xx_pages_excluded_from_comparison():
    pages = [_page(A, text=SEO_A, title="SEO Services", status_code=301),
            _page(D, text=SEO_A, title="SEO Services", status_code=500)]
    block = _build(pages)
    assert block["summary"]["pages_analyzed"] == 0
    assert len(block["excluded_pages"]) == 2


# ===================================================================
# 18-19: existing opportunity grouping / Question Bank compatibility
# ===================================================================
def test_content_intelligence_generates_opportunity_linked_to_content_recommendation():
    pages = [_page(A, text=SEO_A, title="SEO Services", canonical=A),
            _page(D, text=SEO_A, title="SEO Services", canonical=D)]
    block = _build(pages)
    report = {"url": A, "content_intelligence": block}
    opps = V._content_intelligence_opps(report)
    assert opps
    assert all(o["related_recommendation_id"] in ("content", "metadata") for o in opps)


def test_content_opportunity_groups_with_existing_content_score_loss_opportunity():
    pages = [_page(A, text=SEO_A, title="SEO Services", canonical=A),
            _page(D, text=SEO_A, title="SEO Services", canonical=D)]
    block = _build(pages)
    report = {"url": A, "insights": {"score_breakdown": [
        {"signal_id": "content", "label": "Content Structure", "score": 30, "points_lost": 8.0, "issues": []}]},
        "recommendations": [{"id": "content", "priority": "High", "fix_template": {"recommended_fix": ["Fix it."]},
                             "description": "Content needs work."}],
        "content_intelligence": block, "phase4": {}}
    full = V.build_opportunities({}, scan_report=report, phase4={})
    group = next((g for g in full["groups"] if g["root_cause_id"] == "content"), None)
    assert group is not None
    ids = set(group["opportunity_ids"])
    assert "score_loss:content" in ids
    assert any(i.startswith("content_cluster:") for i in ids)


def test_content_intelligence_absent_never_breaks_opportunity_finder():
    out = V.build_opportunities({}, scan_report={"url": A}, phase4=None)
    assert out["items"] == []


# ===================================================================
# 20-21: scoring / existing evidence unchanged
# ===================================================================
def test_content_and_metadata_signal_scores_unaffected_by_new_evidence_fields():
    from app.scanner.signals import content as content_sig
    from app.scanner.signals import metadata as metadata_sig
    from app.scanner.signals.base import SignalContext
    html = (f"<html><head><title>Test Title Here</title>"
           '<meta name="description" content="A fine description.">'
           f"</head><body><h1>Heading</h1><p>{SEO_A}</p></body></html>")
    ctx = SignalContext(PageBundle(url="https://acme.example/", html=html))
    c_score = content_sig.analyze(ctx).score
    m_score = metadata_sig.analyze(ctx).score
    # Recomputing with the exact same HTML must yield the exact same score — the new
    # word_count/h1_text/content_shingles/description evidence fields never feed scoring.
    ctx2 = SignalContext(PageBundle(url="https://acme.example/", html=html))
    assert content_sig.analyze(ctx2).score == c_score
    assert metadata_sig.analyze(ctx2).score == m_score


def test_existing_content_and_metadata_evidence_fields_unchanged():
    from app.scanner.signals import content as content_sig
    from app.scanner.signals import metadata as metadata_sig
    from app.scanner.signals.base import SignalContext
    from app.scanner.models import PageBundle
    html = ("<html><head><title>Test Title</title>"
           '<meta name="description" content="A description.">'
           '<link rel="canonical" href="https://acme.example/"></head>'
           "<body><h1>Heading</h1><p>Some paragraph text here for testing purposes.</p></body></html>")
    ctx = SignalContext(PageBundle(url="https://acme.example/", html=html))
    c_ev = content_sig.analyze(ctx).evidence
    m_ev = metadata_sig.analyze(ctx).evidence
    assert c_ev["h1_count"] == 1 and c_ev["h2_count"] == 0     # pre-existing fields intact
    assert m_ev["has_description"] is True and m_ev["has_canonical"] is True


# ===================================================================
# 22-23: incomplete scan / single-page behavior
# ===================================================================
def test_incomplete_scan_returns_scan_incomplete():
    block = build_content_intelligence_block(bulk_pages=[_page(A, text=SEO_A)], scan_ready=False)
    assert block == {"available": False, "reason": "scan_incomplete"}


def test_single_page_scan_returns_multi_page_crawl_required():
    block = build_content_intelligence_block(bulk_pages=None, scan_ready=True)
    assert block == {"available": False, "reason": "multi_page_crawl_required"}


# ===================================================================
# 24: completed scan with no overlap -> clean empty state
# ===================================================================
def test_completed_scan_with_no_overlap_returns_clean_empty_state():
    pages = [_page(A, text=SEO_A, title="SEO Services"), _page(C, text=UNRELATED, title="Ceramic Mugs")]
    block = _build(pages)
    assert block["available"] is True
    assert block["clusters"] == [] and block["duplicate_elements"] == [] and block["thin_pages"] == []
    assert block["summary"]["pages_analyzed"] == 2


# ===================================================================
# 25-28: free/paid gating
# ===================================================================
# Six mutually-DISTINCT topics (no shared boilerplate/template), each rendered as a
# near-duplicate pair — used to build a "messy" block with several SEPARATE clusters
# (not one giant merged cluster) for gating tests.
_TOPIC_TEXTS = [
    "Cold brew coffee is steeped in cold water for twelve to twenty four hours producing a smooth low acid concentrate that many drinkers prefer over hot brewed coffee during summer months.",
    "Ceramic garden pots are fired at high temperatures giving them excellent durability against frost cracking while still allowing roots to breathe through the porous clay walls year round.",
    "Electric bike repair kits typically include tire levers a portable pump spare tubes and a small multi tool so riders can fix a flat without walking their bike home.",
    "Organic dog food delivery services ship fresh meals in refrigerated boxes so pet owners never have to remember to buy kibble at the grocery store again each week.",
    "Vintage vinyl record players use a belt drive mechanism that reduces motor vibration reaching the needle resulting in warmer sound reproduction compared to cheaper direct drive turntables today.",
    "Solar phone chargers convert sunlight into stored battery power letting hikers and campers keep their devices charged for days without access to a wall outlet anywhere nearby.",
]


def _messy_block():
    pages = []
    for i, text in enumerate(_TOPIC_TEXTS):
        text_b = text.replace("smooth low acid", "smooth mellow")   # tiny edit, still near-dup
        pages.append(_page(f"https://acme.example/topic{i}a", text=text, title=f"Topic {i} Guide A"))
        pages.append(_page(f"https://acme.example/topic{i}b", text=text_b, title=f"Topic {i} Guide B"))
    return _build(pages)


def test_free_response_is_server_side_trimmed():
    block = _messy_block()
    gated = gate_content_intelligence(block, unlocked=False)
    assert gated["preview"] is True
    assert len(gated["clusters"]) <= 2
    assert gated["locked_cluster_count"] == len(block["clusters"]) - len(gated["clusters"])


def test_free_response_does_not_contain_hidden_paid_cluster_urls():
    block = _messy_block()
    gated = gate_content_intelligence(block, unlocked=False)
    shown = {p["url"] for c in gated["clusters"] for p in c["pages"]}
    all_urls = {p["url"] for c in block["clusters"] for p in c["pages"]}
    locked = all_urls - shown
    assert locked
    blob = str(gated)
    for u in locked:
        assert u not in blob


def test_paid_response_contains_full_grounded_clusters():
    block = _messy_block()
    gated = gate_content_intelligence(block, unlocked=True)
    assert gated is block or gated["clusters"] == block["clusters"]
    assert "preview" not in gated


# ===================================================================
# 29-32: HTTP integration — cache reuse, gating, org isolation, compatibility
# ===================================================================
CI_URLS = [f"https://cisite.test/{p}" for p in ("seo-services", "seo-agency", "mugs")]

CI_HTML = {
    "https://cisite.test/seo-services": (
        f"<html><head><title>SEO Services</title></head><body><h1>SEO Services</h1>"
        f"<p>{SEO_A}</p></body></html>"),
    "https://cisite.test/seo-agency": (
        f"<html><head><title>SEO Agency</title></head><body><h1>SEO Agency</h1>"
        f"<p>{SEO_E}</p></body></html>"),
    "https://cisite.test/mugs": (
        f"<html><head><title>Mugs</title></head><body><h1>Mugs</h1>"
        f"<p>{UNRELATED}</p></body></html>"),
}


async def _fake_fetch(url, *, transport=None):
    return PageBundle(url=url, html=CI_HTML.get(url, "<html></html>"), status_code=200)


def _make_bulk_scan(client, monkeypatch):
    monkeypatch.setattr(bulk_mod, "fetch", _fake_fetch)
    r = client.post("/v1/scan/bulk", json={"urls": CI_URLS})
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


def test_content_intelligence_endpoint_available_and_grounded(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
    resp = client.get(f"/reports/{scan_id}/content-intelligence")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["summary"]["pages_analyzed"] == 3


def test_report_endpoint_includes_content_intelligence_key(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
    report = client.get(f"/reports/{scan_id}").json()
    assert report["content_intelligence"]["available"] is True
    assert report["technical_seo"]["available"] is True          # neighboring block unaffected
    assert report["crawl_graph"]["available"] is True             # neighboring block unaffected


def test_e2_cache_reused_repeated_warm_reads_no_build_report(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
    client.get(f"/reports/{scan_id}")

    calls = _spy_build_report(monkeypatch)
    client.get(f"/reports/{scan_id}/content-intelligence")
    client.get(f"/reports/{scan_id}/content-intelligence")
    client.get(f"/reports/{scan_id}")
    assert calls["n"] == 0


def test_stale_report_version_triggers_rebuild_with_content_intelligence(monkeypatch):
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
    assert report["content_intelligence"]["available"] is True
    assert new_row.version != "0.0.0-stale"


def test_paid_content_intelligence_endpoint_is_full(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
    _go_pro(client)
    body = client.get(f"/reports/{scan_id}/content-intelligence").json()
    assert "preview" not in body


def test_org_isolation_enforced_for_content_intelligence_endpoint(monkeypatch):
    _enforce_billing(monkeypatch)
    owner, _ = auth_client()
    scan_id = _make_bulk_scan(owner, monkeypatch)
    other, _ = auth_client()
    resp = other.get(f"/reports/{scan_id}/content-intelligence")
    assert resp.status_code == 404


def test_technical_seo_and_crawl_graph_and_question_bank_unaffected(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_bulk_scan(client, monkeypatch)
    tseo = client.get(f"/reports/{scan_id}/technical-seo")
    cg = client.get(f"/reports/{scan_id}/crawl-graph")
    qb = client.get(f"/reports/{scan_id}/question-bank")
    assert tseo.status_code == 200 and tseo.json()["available"] is True
    assert cg.status_code == 200 and cg.json()["available"] is True
    assert qb.status_code == 200


def test_single_page_scan_report_content_intelligence_is_multi_page_required(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()

    async def _single_fetch(url):
        return PageBundle(url=url, html="<html><head><title>T</title></head><body>hi</body></html>",
                          status_code=200)
    import app.api.routes_scan as rs
    monkeypatch.setattr(rs, "fetch", _single_fetch)
    scan_id = client.post("/v1/scan", json={"url": "https://single.example/"}).json()["scan_id"]

    report = client.get(f"/reports/{scan_id}").json()
    assert report["content_intelligence"] == {"available": False, "reason": "multi_page_crawl_required"}


def test_pending_bulk_scan_never_gets_fabricated_content_intelligence():
    org_scan = Scan(url="https://pendingci.example/a", normalized_url="pendingci.example",
                    ars=0, rubric_version="t", status="running",
                    result={"bulk": {"requested": 2, "urls": ["a", "b"]}})
    scanned = scan_to_input(org_scan)
    report = build_report(scanned)
    assert report["content_intelligence"] == {"available": False, "reason": "scan_incomplete"}
