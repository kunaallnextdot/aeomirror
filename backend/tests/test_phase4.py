"""Phase 4 — deeper AEO intelligence: Schema, Internal Link, Entity, and Question
Mining. Pure builders (`reports/phase4.py`) are tested directly against hand-built
`sections` fixtures (same convention as test_insights.py/test_phase2.py); the
Opportunity Finder cross-linking and free/paid/org-isolation behavior are tested
through the real HTTP endpoints against a real scanned page (GOOD_HTML), the same
pattern test_gating_cleanup.py already uses.

No scanner score/weight logic is touched anywhere in this file — every assertion is
about the READ-SIDE derivations layered on top of existing signal evidence."""
import app.api.routes_scan as rs
import app.billing.plans as plans_mod
from app.config import settings
from app.reports.engine import build_report
from app.reports.phase4 import (
    build_entity_intelligence, build_link_intelligence,
    build_phase4_block, build_question_mining, build_schema_intelligence,
    gate_phase4,
)
from app.scanner.models import PageBundle
from app.services.answer_tracking import visibility as V
from tests.authutil import auth_client
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS


# ------------------------------- fixtures -------------------------------
def _schema_section(*, state="present", has_entity=False, entity_types=None,
                    content=None, entity_evidence=None, faq_questions=None,
                    malformed=0, score=30, weight=15):
    return {
        "id": "schema", "label": "Structured Data", "weight": weight, "score": score,
        "status": "fail" if score < 45 else "warn" if score < 75 else "pass",
        "issues": [], "recommendations": [],
        "evidence": {
            "state": state, "malformed": malformed, "has_entity": has_entity,
            "entity_types": entity_types or [],
            "detected_types": entity_types or [],
            "content": content or {},
            "entity_evidence": entity_evidence or {"name": None, "url": None,
                                                    "logo": None, "same_as": []},
            "faq_questions": faq_questions or [],
        },
    }


def _links_section(*, internal=0, has_nav=False, diversity=0.0, generic=0, empty=0,
                   score=20, weight=8):
    return {
        "id": "links", "label": "Internal Linking", "weight": weight, "score": score,
        "status": "fail" if score < 45 else "warn" if score < 75 else "pass",
        "issues": [], "recommendations": [],
        "evidence": {"internal_links": internal, "has_nav": has_nav,
                    "anchor_diversity": diversity, "generic_anchors": generic,
                    "empty_anchors": empty},
    }


def _content_section(*, heading_questions=None, word_count=0, body_text="",
                     score=80, weight=12):
    return {
        "id": "content", "label": "Content Structure", "weight": weight, "score": score,
        "status": "pass", "issues": [], "recommendations": [],
        "evidence": {"h1_count": 1, "h2_count": 2, "heading_jumps": 0,
                    "semantic_html": True, "paragraphs": 5, "lists": 1,
                    "heading_questions": heading_questions or [],
                    "word_count": word_count,
                    "body_evidence": {"word_count": word_count, "truncated": False,
                                      "chunks": ([{"id": "chunk-0", "text": body_text,
                                                  "start_word": 0, "end_word": word_count}]
                                                 if body_text else [])}},
    }


# A page with real, qualifying evidence for every gated schema type (long-form body
# text, a question-shaped heading, price text, service-offer language, and a review/
# rating phrase) — used by tests that need every gated type to be genuinely relevant,
# without relying on any single test asserting the exact relevance heuristic itself.
_RELEVANT_CONTENT = _content_section(
    heading_questions=["Why should you choose us?"],
    word_count=500,
    body_text=(
        "Why should you choose us? " + ("Our team has served customers for years. " * 60)
        + "We offer premium consulting services starting at $99 per month. "
        "Read our customer reviews — rated 5 stars by over 200 clients."
    ),
)


def _scan(sections, url="https://acme.example/", overall_score=50):
    return {"scan_id": "s1", "url": url, "domain": "acme.example",
            "overall_score": overall_score, "scanner_version": "3.0.0",
            "scanned_at": "2026-01-01T00:00:00Z", "sections": sections}


# ===================================================================
# 1-4: Schema Intelligence
# ===================================================================
def test_schema_detected_types_reported_verbatim():
    sec = [_schema_section(state="present", has_entity=True, entity_types=["Organization"],
                           content={"WebSite": True, "Article": True})]
    si = build_schema_intelligence(sec, "https://acme.example/")
    assert si["state"] == "present"
    assert "WebSite" in si["present_types"] and "Article" in si["present_types"]
    assert "Organization" in si["present_types"]      # has_entity => counted as present


def test_schema_missing_opportunity_has_why_and_action():
    sec = [_schema_section(state="present", has_entity=False, content={"WebSite": True})]
    si = build_schema_intelligence(sec, "https://acme.example/")
    orgs = [m for m in si["missing_types"] if m["type"] == "Organization"]
    assert len(orgs) == 1
    assert "identify" in orgs[0]["why_it_matters"].lower() or orgs[0]["why_it_matters"]
    assert "organization" in orgs[0]["recommended_action"].lower()
    assert "WebSite" not in [m["type"] for m in si["missing_types"]]   # present, not missing


def test_schema_affected_url_is_the_real_scanned_url_only():
    sec = [_schema_section(state="absent")]
    si = build_schema_intelligence(sec, "https://real-scanned-page.example/report")
    for m in si["missing_types"]:
        assert m["affected_urls"] == ["https://real-scanned-page.example/report"]


def test_schema_never_fabricates_a_detected_type():
    """An absent-state page never reports a type that wasn't actually found, and
    never a fabricated 'detected' type. With NO content-relevance evidence at all,
    only the universally-applicable types (Organization/WebSite/BreadcrumbList) are
    surfaced as missing — the content-type-dependent types (Article/FAQPage/Product/
    Service/Review) require real supporting evidence (see test_schema_relevance_*
    below), never a blind checklist applied regardless of page content."""
    sec = [_schema_section(state="absent")]
    si = build_schema_intelligence(sec, "https://acme.example/")
    assert si["detected_types"] == []
    assert si["present_types"] == []
    assert {m["type"] for m in si["missing_types"]} == {"Organization", "WebSite", "BreadcrumbList"}


def test_schema_relevance_product_recommended_when_real_evidence_supports_it():
    """A page whose content genuinely looks like a product page (real price text in
    the scanned body) DOES get a Product schema recommendation, with real evidence
    quoted in why_it_matters."""
    sec = [_schema_section(state="absent"), _RELEVANT_CONTENT]
    si = build_schema_intelligence(sec, "https://acme.example/")
    product = [m for m in si["missing_types"] if m["type"] == "Product"]
    assert len(product) == 1
    assert "price" in product[0]["why_it_matters"].lower()


def test_schema_relevance_product_not_recommended_without_evidence():
    """A page with no product-page signal at all (no price text, no product-ish URL
    path) never gets a 'Missing Product schema' recommendation — the false positive
    this whole feature exists to eliminate."""
    sec = [_schema_section(state="absent"), _content_section(word_count=50, body_text="Contact us for more information.")]
    si = build_schema_intelligence(sec, "https://acme.example/contact")
    assert not any(m["type"] == "Product" for m in si["missing_types"])


def test_schema_relevance_review_requires_genuine_review_context():
    """Review schema is not recommended without genuine review/rating language or a
    review-shaped URL path — a plain contact page never gets it."""
    sec = [_schema_section(state="absent"), _content_section(word_count=50, body_text="Contact us for more information.")]
    si = build_schema_intelligence(sec, "https://acme.example/contact")
    assert not any(m["type"] == "Review" for m in si["missing_types"])

    sec_with_reviews = [_schema_section(state="absent"), _RELEVANT_CONTENT]
    si2 = build_schema_intelligence(sec_with_reviews, "https://acme.example/")
    review = [m for m in si2["missing_types"] if m["type"] == "Review"]
    assert len(review) == 1
    assert "review" in review[0]["why_it_matters"].lower() or "rating" in review[0]["why_it_matters"].lower()


def test_schema_relevance_service_available_for_service_pages():
    """Service schema remains available (not silently dropped) for a page whose URL
    path or content genuinely describes a service."""
    sec = [_schema_section(state="absent")]
    si = build_schema_intelligence(sec, "https://acme.example/services/consulting")
    service = [m for m in si["missing_types"] if m["type"] == "Service"]
    assert len(service) == 1
    assert "service" in service[0]["why_it_matters"].lower()


def test_schema_relevance_service_not_recommended_without_evidence():
    """Phase I regression: a page with no service-ish URL path and no service-offer
    language never gets a 'Missing Service schema' recommendation — same false-
    positive-elimination rule applied to the other gated types."""
    sec = [_schema_section(state="absent"), _content_section(word_count=50, body_text="Contact us for more information.")]
    si = build_schema_intelligence(sec, "https://acme.example/contact")
    assert not any(m["type"] == "Service" for m in si["missing_types"])


def test_schema_relevance_article_requires_substantial_body_content():
    """Article schema is only suggested for genuinely long-form pages — a thin page
    never gets it, a substantial one does, both using the real word_count signal."""
    sec_thin = [_schema_section(state="absent"), _content_section(word_count=50, body_text="Short page.")]
    si_thin = build_schema_intelligence(sec_thin, "https://acme.example/")
    assert not any(m["type"] == "Article" for m in si_thin["missing_types"])

    sec_long = [_schema_section(state="absent"), _RELEVANT_CONTENT]
    si_long = build_schema_intelligence(sec_long, "https://acme.example/")
    assert any(m["type"] == "Article" for m in si_long["missing_types"])


def test_schema_relevance_faqpage_requires_real_question_headings():
    sec_no_faq = [_schema_section(state="absent"), _content_section(word_count=50, body_text="Contact us.")]
    si = build_schema_intelligence(sec_no_faq, "https://acme.example/")
    assert not any(m["type"] == "FAQPage" for m in si["missing_types"])

    sec_faq = [_schema_section(state="absent"), _RELEVANT_CONTENT]
    si2 = build_schema_intelligence(sec_faq, "https://acme.example/")
    faq = [m for m in si2["missing_types"] if m["type"] == "FAQPage"]
    assert len(faq) == 1 and "question" in faq[0]["why_it_matters"].lower()


# ===================================================================
# 5-9: Internal Link Intelligence
# ===================================================================
def test_link_weak_coverage_reported_with_real_count():
    sec = [_links_section(internal=2, has_nav=True)]
    li = build_link_intelligence(sec, "https://acme.example/page")
    weak = [i for i in li["issues"] if i["type"] == "weak_internal_linking"]
    assert len(weak) == 1 and "2 internal link" in weak[0]["detail"]
    assert weak[0]["affected_urls"] == ["https://acme.example/page"]


def test_link_generic_anchor_issue_uses_real_count():
    sec = [_links_section(internal=6, has_nav=True, generic=3)]
    li = build_link_intelligence(sec, "https://acme.example/")
    generic = [i for i in li["issues"] if i["type"] == "generic_anchor_text"]
    assert len(generic) == 1 and "3 link" in generic[0]["detail"]


def test_link_potential_isolation_not_orphan_claim():
    sec = [_links_section(internal=0)]
    li = build_link_intelligence(sec, "https://acme.example/")
    isolated = [i for i in li["issues"] if i["type"] == "potentially_isolated_page"]
    assert len(isolated) == 1
    assert isolated[0]["label"] == "Potentially isolated page"
    assert "orphan" not in isolated[0]["label"].lower()
    assert "orphan" not in isolated[0]["detail"].lower()


def test_link_never_claims_true_orphan_without_graph_data():
    sec = [_links_section(internal=0)]
    li = build_link_intelligence(sec, "https://acme.example/")
    assert li["inbound_link_graph_available"] is False
    assert "insufficient crawl graph data" in li["inbound_link_graph_note"].lower()
    assert "source-page analysis required" in \
        [i for i in li["issues"] if i["type"] == "potentially_isolated_page"][0]["recommended_action"].lower()


def test_link_affected_url_is_real():
    sec = [_links_section(internal=0)]
    li = build_link_intelligence(sec, "https://real.example/only-page")
    for iss in li["issues"]:
        assert iss["affected_urls"] == ["https://real.example/only-page"]


# ===================================================================
# 10-13: Entity Intelligence
# ===================================================================
def test_entity_primary_entity_detected_from_real_schema():
    sec = [_schema_section(has_entity=True, entity_types=["Organization"],
                           entity_evidence={"name": "Acme Inc", "url": "https://acme.example/",
                                            "logo": None, "same_as": []},
                           content={"WebSite": True})]
    ei = build_entity_intelligence(sec, "https://acme.example/")
    assert ei["primary_entity_types"] == ["Organization"]
    assert ei["primary_entity_name"] == "Acme Inc"
    assert ei["checks"]["has_entity_schema"] is True


def test_entity_sameas_detected_when_present():
    sec = [_schema_section(has_entity=True, entity_types=["Organization"],
                           entity_evidence={"name": "Acme", "url": None, "logo": None,
                                            "same_as": ["https://linkedin.com/company/acme"]})]
    ei = build_entity_intelligence(sec, "https://acme.example/")
    assert ei["same_as"] == ["https://linkedin.com/company/acme"]
    assert ei["same_as_note"] is None


def test_entity_missing_signal_when_no_sameas():
    sec = [_schema_section(has_entity=True, entity_types=["Organization"])]
    ei = build_entity_intelligence(sec, "https://acme.example/")
    assert ei["same_as"] == []
    assert ei["same_as_note"] == "No sameAs relationship detected."
    assert "has_sameas" in ei["missing_signals"]
    assert ei["completeness_pct"] < 100.0


def test_entity_never_claims_knowledge_graph_presence():
    sec = [_schema_section(has_entity=True, entity_types=["Organization"],
                           entity_evidence={"name": "Acme", "url": "https://acme.example/",
                                            "logo": "https://acme.example/logo.png",
                                            "same_as": ["https://linkedin.com/company/acme"]},
                           content={"WebSite": True})]
    ei = build_entity_intelligence(sec, "https://acme.example/")
    assert "knowledge graph" in ei["knowledge_graph_note"].lower()
    assert "cannot" in ei["knowledge_graph_note"].lower() or "neither" in ei["knowledge_graph_note"].lower()
    # even at 100% checklist completeness, nothing here claims KG/Knowledge Panel presence
    assert ei["completeness_pct"] == 100.0
    other_text = " ".join(str(v) for k, v in ei.items() if k != "knowledge_graph_note")
    assert "knowledge panel" not in other_text.lower()
    assert "knowledge graph" not in other_text.lower()


# ===================================================================
# 14-18: Question Mining
# ===================================================================
def test_question_mining_extracts_real_faq_and_heading_questions():
    sec = [
        _schema_section(faq_questions=[{"question": "What does Acme treat?", "answer": "Back pain."}]),
        _content_section(heading_questions=["How does physical therapy work?"]),
    ]
    qm = build_question_mining(sec, "https://acme.example/")
    texts = {q["text"] for q in qm["questions"]}
    assert "What does Acme treat?" in texts
    assert "How does physical therapy work?" in texts
    sources = {q["source"] for q in qm["questions"]}
    assert sources == {"schema_faq", "page_heading"}


def test_question_mining_provenance_kept_per_question():
    sec = [_schema_section(faq_questions=[{"question": "What does Acme treat?", "answer": None}])]
    qm = build_question_mining(sec, "https://acme.example/practice")
    q = qm["questions"][0]
    assert q["source"] == "schema_faq"
    assert q["affected_url"] == "https://acme.example/practice"


def test_question_mining_deterministic_classification():
    sec = [_content_section(heading_questions=[
        "How much does treatment cost?", "How to book an appointment?",
        "What is Acme vs Rival?", "Do you offer service near me?"])]
    qm = build_question_mining(sec, "https://acme.example/")
    by_text = {q["text"]: q["category"] for q in qm["questions"]}
    assert by_text["How much does treatment cost?"] == "pricing"
    assert by_text["How to book an appointment?"] == "how_to"
    assert by_text["What is Acme vs Rival?"] == "comparison"
    assert by_text["Do you offer service near me?"] == "local"
    # deterministic: same input -> same output every time
    assert build_question_mining(sec, "https://acme.example/") == qm


def test_question_mining_never_fabricates_a_question():
    """No FAQ schema and no question-shaped headings -> empty, not invented content."""
    sec = [_schema_section(faq_questions=[]), _content_section(heading_questions=[])]
    qm = build_question_mining(sec, "https://acme.example/")
    assert qm["questions"] == [] and qm["total_count"] == 0


def test_question_mining_never_claims_search_volume():
    sec = [_content_section(heading_questions=["How much does it cost?"])]
    qm = build_question_mining(sec, "https://acme.example/")
    assert "volume" not in str(qm).lower() and "demand" not in str(qm).lower()


# ===================================================================
# 19-24: Opportunity Finder integration
# ===================================================================
def _summary(per_prompt=None):
    return {"per_prompt": per_prompt or [], "leaderboard": [], "competitors": [],
            "citation_diagnosis": {}, "citation_search_samples": 0}


def test_phase4_opportunities_surfaced_in_same_shape():
    sections = [_schema_section(state="present", has_entity=False, content={"WebSite": True}),
               _links_section(internal=0)]
    report = build_report(_scan(sections))
    op = V.build_opportunities(_summary(), report, report["phase4"])
    schema_ops = [o for o in op["items"] if o["type"] == "schema_opportunity"]
    link_ops = [o for o in op["items"] if o["type"] == "internal_link_opportunity"]
    assert schema_ops and link_ops
    required_keys = {"id", "type", "title", "description", "priority", "impact",
                     "evidence", "source", "affected_prompts", "affected_urls",
                     "recommended_action"}
    for o in schema_ops + link_ops:
        assert required_keys.issubset(o.keys())


def test_phase4_opportunities_deduplicated_and_linked_not_duplicated():
    sections = [_schema_section(state="present", has_entity=False, content={"WebSite": True})]
    report = build_report(_scan(sections))
    op = V.build_opportunities(_summary(), report, report["phase4"])
    ids = [o["id"] for o in op["items"]]
    assert len(ids) == len(set(ids))                                       # no duplicate ids
    schema_ops = [o for o in op["items"] if o["type"] == "schema_opportunity"]
    assert all(o["related_recommendation_id"] == "schema" for o in schema_ops)
    # the generic score_loss:schema opportunity still exists too — cross-linked, not replaced
    assert any(o["id"] == "score_loss:schema" for o in op["items"])


def test_phase4_opportunity_priority_present_and_valid():
    sections = [_schema_section(state="absent"), _links_section(internal=0)]
    report = build_report(_scan(sections))
    op = V.build_opportunities(_summary(), report, report["phase4"])
    for o in op["items"]:
        assert o["priority"] in ("Critical", "High", "Medium", "Low")


def test_phase4_schema_and_link_impact_matches_score_loss_impact():
    sections = [_schema_section(state="present", has_entity=False, content={"WebSite": True})]
    report = build_report(_scan(sections))
    op = V.build_opportunities(_summary(), report, report["phase4"])
    score_loss = next(o for o in op["items"] if o["id"] == "score_loss:schema")
    schema_gaps = [o for o in op["items"] if o["type"] == "schema_opportunity"]
    assert schema_gaps and all(o["impact"] == score_loss["impact"] for o in schema_gaps)


def test_untracked_question_opportunity_has_no_fabricated_impact():
    sections = [_content_section(heading_questions=["How much does treatment cost?"])]
    report = build_report(_scan(sections))
    op = V.build_opportunities(_summary(per_prompt=[]), report, report["phase4"])
    qops = [o for o in op["items"] if o["type"] == "question_opportunity"]
    assert qops and qops[0]["impact"] is None
    assert qops[0]["priority"] == "Low"


def test_tracked_question_is_not_duplicated_as_question_opportunity():
    sections = [_content_section(heading_questions=["How much does treatment cost?"])]
    report = build_report(_scan(sections))
    tracked = _summary(per_prompt=[{"prompt_id": "p1", "text": "How much does treatment cost?",
                                    "mention_rate": 50.0, "is_gap": False,
                                    "recommended_entities": [], "gap": None, "samples": 2, "mentions": 1}])
    op = V.build_opportunities(tracked, report, report["phase4"])
    assert not any(o["type"] == "question_opportunity" for o in op["items"])


# ===================================================================
# 25-27: Billing (gate_phase4)
# ===================================================================
def test_gate_phase4_free_trims_missing_types_and_issues():
    block = build_phase4_block(
        [_schema_section(state="absent"), _links_section(internal=0)],
        "https://acme.example/")
    gated = gate_phase4(block, unlocked=False)
    assert len(gated["schema"]["missing_types"]) <= 2
    assert gated["schema"]["preview"] is True
    assert gated["schema"]["locked_missing_count"] >= 0
    assert len(gated["links"]["issues"]) <= 2


def test_gate_phase4_paid_returns_full_block_unchanged():
    block = build_phase4_block(
        [_schema_section(state="absent"), _links_section(internal=0)],
        "https://acme.example/")
    gated = gate_phase4(block, unlocked=True)
    assert gated == block


def test_gate_phase4_no_paid_data_leaks_in_free_preview():
    block = build_phase4_block(
        [_schema_section(state="absent"), _links_section(internal=0, generic=5, empty=3)],
        "https://acme.example/")
    gated = gate_phase4(block, unlocked=False)
    full_missing = {m["type"] for m in block["schema"]["missing_types"]}
    free_missing = {m["type"] for m in gated["schema"]["missing_types"]}
    assert free_missing < full_missing                       # strictly fewer than the full set
    full_issues = {i["type"] for i in block["links"]["issues"]}
    free_issues = {i["type"] for i in gated["links"]["issues"]}
    assert free_issues < full_issues


# ===================================================================
# Fix 1: scan-readiness guard — never fabricate a diagnosis for an
# incomplete/pending/running/failed scan.
# ===================================================================


def test_pending_scan_does_not_show_all_schema_types_as_missing():
    """A scan with NO real evidence yet (scan_ready=False) must never be presented as
    a completed diagnosis, even though `sections=[]` looks identical to a completed
    scan that genuinely has no JSON-LD."""
    block = build_phase4_block([], "https://acme.example/", scan_ready=False)
    assert block == {"available": False, "reason": "scan_incomplete"}
    assert "schema" not in block and "links" not in block
    assert "entity" not in block and "questions" not in block


def test_running_scan_does_not_show_completed_phase4_diagnosis():
    block = build_phase4_block([_schema_section(state="absent")],
                               "https://acme.example/", scan_ready=False)
    assert block["available"] is False
    # even with sections present, scan_ready=False wins — no diagnosis is derived from them
    assert "schema" not in block


def test_incomplete_scan_does_not_fabricate_opportunities():
    """`build_opportunities` must not surface schema/link/entity/question opportunities
    sourced from an unready scan's phase4 block."""
    unready = build_phase4_block([_schema_section(state="absent"), _links_section(internal=0)],
                                 "https://acme.example/", scan_ready=False)
    summary = _summary()
    op = V.build_opportunities(summary, None, unready)
    phase4_types = {"schema_opportunity", "internal_link_opportunity",
                    "entity_opportunity", "question_opportunity"}
    assert not any(o["type"] in phase4_types for o in op["items"])


def test_completed_scan_with_no_schema_still_shows_missing_schema():
    """The flip side of the fix: a GENUINELY complete scan with no structured data
    must still say so plainly — readiness and "found nothing" are different facts.
    Only the universally-applicable types are unconditional (see
    test_schema_never_fabricates_a_detected_type for why the content-type-dependent
    types aren't included without real relevance evidence)."""
    block = build_phase4_block([_schema_section(state="absent")],
                               "https://acme.example/", scan_ready=True)
    assert block["available"] is True
    assert {m["type"] for m in block["schema"]["missing_types"]} == {
        "Organization", "WebSite", "BreadcrumbList"}


def test_completed_scan_with_schema_still_works():
    block = build_phase4_block(
        [_schema_section(state="present", has_entity=True, entity_types=["Organization"],
                         content={"WebSite": True, "Article": True})],
        "https://acme.example/", scan_ready=True)
    assert block["available"] is True
    assert "Organization" in block["schema"]["present_types"]
    assert "WebSite" in block["schema"]["present_types"]


def test_incomplete_scan_generates_no_fake_urls_or_prompts():
    unready = build_phase4_block([_schema_section(state="absent")],
                                 "https://acme.example/", scan_ready=False)
    op = V.build_opportunities(_summary(), None, unready)
    for o in op["items"]:
        for u in o.get("affected_urls") or []:
            assert u   # never an empty/placeholder URL
        for p in o.get("affected_prompts") or []:
            assert p   # never a fabricated prompt entry


def test_gate_phase4_passes_through_unavailable_state_unchanged_for_every_tier():
    unready = build_phase4_block([], "https://acme.example/", scan_ready=False)
    assert gate_phase4(unready, unlocked=False) == unready
    assert gate_phase4(unready, unlocked=True) == unready


# ===================================================================
# End-to-end: real scan through the API (billing + security + regression)
# ===================================================================
async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"content-encoding": "gzip", "cache-control": "max-age=60"})


def _make_scan(client, monkeypatch, url="https://phase4.example/"):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    return client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def _enforce_billing(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", 50)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def test_free_report_phase4_is_gated_end_to_end(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)

    body = client.get(f"/reports/{scan_id}").json()
    p4 = body["phase4"]
    assert p4["schema"]["preview"] is True
    assert len(p4["schema"]["missing_types"]) <= 2
    assert p4["links"]["preview"] is True
    assert p4["entity"]["preview"] is True
    assert p4["questions"]["preview"] is True


def test_paid_report_phase4_is_complete_end_to_end(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    _go_pro(client)

    body = client.get(f"/reports/{scan_id}").json()
    p4 = body["phase4"]
    assert "preview" not in p4["schema"]
    assert "preview" not in p4["links"]
    # GOOD_HTML has real Organization + Article JSON-LD -> genuinely detected, not invented
    assert "Organization" in p4["schema"]["present_types"]
    assert "Article" in p4["schema"]["present_types"]
    assert p4["entity"]["primary_entity_name"] == "BrewLab"


def test_phase4_org_isolation(monkeypatch):
    _enforce_billing(monkeypatch)
    client_a, _ = auth_client()
    client_b, _ = auth_client()
    scan_id = _make_scan(client_a, monkeypatch)
    assert client_a.get(f"/reports/{scan_id}").status_code == 200
    assert client_b.get(f"/reports/{scan_id}").status_code == 404


def test_phase4_present_in_insights_endpoint_and_consistent_with_report(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    report = client.get(f"/reports/{scan_id}").json()
    insights = client.get(f"/reports/{scan_id}/insights").json()
    assert report["phase4"]["schema"]["missing_types"] == insights["phase4"]["schema"]["missing_types"]


# --- Fix 1, end-to-end: a real PENDING/RUNNING/FAILED Scan row via the real API ---
def _make_incomplete_scan(status, *, org_id, result=None):
    from app.db.models import Scan
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        row = Scan(url="https://incomplete.example/", normalized_url="incomplete.example",
                  ars=0, rubric_version="t", status=status,
                  result=result or {"bulk": {"requested": 3, "urls": ["a", "b", "c"]}},
                  organization_id=org_id)
        db.add(row); db.commit(); db.refresh(row)
        return row.id
    finally:
        db.close()


def test_pending_bulk_scan_report_shows_honest_incomplete_state(monkeypatch):
    _enforce_billing(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    scan_id = _make_incomplete_scan("pending", org_id=org_id)

    resp = client.get(f"/reports/{scan_id}").json()
    assert resp["phase4"] == {"available": False, "reason": "scan_incomplete"}
    # Phase 1/2 also degrade safely — no crash, no fabricated recommendations either.
    assert resp["recommendations"] == []


def test_running_scan_report_shows_honest_incomplete_state(monkeypatch):
    _enforce_billing(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    scan_id = _make_incomplete_scan("running", org_id=org_id)
    resp = client.get(f"/reports/{scan_id}").json()
    assert resp["phase4"]["available"] is False


def test_failed_scan_report_shows_honest_incomplete_state_not_fabricated(monkeypatch):
    _enforce_billing(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    scan_id = _make_incomplete_scan(
        "failed", org_id=org_id,
        result={"error": "The bulk scan could not be completed.", "bulk": {"urls": []}})
    resp = client.get(f"/reports/{scan_id}").json()
    assert resp["phase4"] == {"available": False, "reason": "scan_incomplete"}
    insights = client.get(f"/reports/{scan_id}/insights").json()
    assert insights["phase4"] == {"available": False, "reason": "scan_incomplete"}
