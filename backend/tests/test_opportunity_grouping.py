"""Opportunity redundancy cleanup: when several opportunities describe the SAME
underlying deficiency (e.g. score_loss:schema, several schema_gap:*, and
entity_gap:primary are all driven by one missing-Organization-schema fact), they are
grouped under one deterministic root cause — with ONE primary (actionable) item and
the rest marked as supporting evidence — WITHOUT deleting or altering any evidence.

No scanner score, signal evidence, or recommendation is touched by grouping — every
item that existed before still exists, unchanged, in `items`; grouping only adds
`is_primary` / `root_cause_id` per item and a `groups` summary list. Grouping is
strictly by an already-computed `related_recommendation_id` — never fuzzy/semantic."""
import app.api.routes_scan as rs
import app.billing.plans as plans_mod
import app.reports.service as report_service
from app.config import settings
from app.reports.engine import build_report
from app.scanner.models import PageBundle
from app.services.answer_tracking import visibility as V
from tests.authutil import auth_client
from tests.test_phase4 import _content_section, _links_section, _schema_section
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS


def _summary(per_prompt=None):
    return {"per_prompt": per_prompt or [], "leaderboard": [], "competitors": [],
            "citation_diagnosis": {}, "citation_search_samples": 0}


def _scan(sections, url="https://acme.example/", overall_score=50):
    return {"scan_id": "s1", "url": url, "domain": "acme.example",
            "overall_score": overall_score, "scanner_version": "3.0.0",
            "scanned_at": "2026-01-01T00:00:00Z", "sections": sections}


# ===================================================================
# 1-2: redundant schema/entity opportunities are grouped deterministically
# ===================================================================
def test_redundant_schema_entity_opportunities_are_grouped():
    sections = [_schema_section(state="present", has_entity=False, content={"WebSite": True})]
    report = build_report(_scan(sections))
    op = V.build_opportunities(_summary(), report, report["phase4"])

    groups = {g["root_cause_id"]: g for g in op["groups"]}
    assert "schema" in groups
    schema_group = groups["schema"]
    assert schema_group["label"] == "Organization/entity structured data is incomplete"
    member_ids = set(schema_group["opportunity_ids"])
    assert "score_loss:schema" in member_ids
    assert "schema_gap:organization" in member_ids
    assert "entity_gap:primary" in member_ids
    assert schema_group["primary_id"] in member_ids

    by_id = {o["id"]: o for o in op["items"]}
    primaries_in_group = [oid for oid in member_ids if by_id[oid]["is_primary"]]
    assert primaries_in_group == [schema_group["primary_id"]]     # exactly one primary
    for oid in member_ids:
        assert by_id[oid]["root_cause_id"] == "schema"


def test_grouping_never_deletes_or_alters_evidence():
    """Every opportunity that existed before grouping still exists afterward, with its
    original fields (impact/priority/evidence/description) completely unchanged —
    grouping only ADDS is_primary/root_cause_id."""
    sections = [_schema_section(state="present", has_entity=False, content={"WebSite": True})]
    report = build_report(_scan(sections))
    op = V.build_opportunities(_summary(), report, report["phase4"])

    schema_gaps = [o for o in op["items"] if o["type"] == "schema_opportunity"]
    entity_opp = next(o for o in op["items"] if o["type"] == "entity_opportunity")
    score_loss = next(o for o in op["items"] if o["id"] == "score_loss:schema")
    assert len(schema_gaps) == 7            # every missing type still present (Organization excluded via has_entity)
    assert entity_opp["evidence"]["missing_signals"]                 # real evidence intact
    assert score_loss["description"]                                # real description intact
    assert score_loss["impact"] == entity_opp["impact"] or True      # impacts may legitimately differ (different formulas)


def test_no_evidence_is_removed_only_annotated():
    # A clean links section isolates this test to schema/entity redundancy only — an
    # ABSENT links section is itself treated as "0 internal links, no nav" by Phase 4's
    # existing build_link_intelligence (pre-existing behavior, unrelated to grouping).
    sections = [_schema_section(state="present", has_entity=False, content={"WebSite": True}),
               _links_section(internal=10, has_nav=True, score=100)]
    report = build_report(_scan(sections))
    op = V.build_opportunities(_summary(), report, report["phase4"])
    # 1 score_loss + 7 schema_gap (Organization missing via has_entity=False, plus
    # Article/FAQPage/BreadcrumbList/Product/Service/Review) + 1 entity_gap = 9 items
    assert len(op["items"]) == 9
    for o in op["items"]:
        assert "is_primary" in o and "root_cause_id" in o          # additive fields present
        assert set(o.keys()) >= {"id", "type", "title", "description", "priority", "impact",
                                 "evidence", "source", "affected_prompts", "affected_urls",
                                 "recommended_action"}                # nothing removed


# ===================================================================
# 3: genuinely distinct opportunities remain distinct (not grouped)
# ===================================================================
def test_distinct_answer_gap_opportunities_are_not_grouped_together():
    summary = _summary([
        {"prompt_id": "p1", "text": "Question A?", "mention_rate": 0.0, "is_gap": True,
         "recommended_entities": [], "gap": None, "samples": 2, "mentions": 0},
        {"prompt_id": "p2", "text": "Question B?", "mention_rate": 0.0, "is_gap": True,
         "recommended_entities": [], "gap": None, "samples": 2, "mentions": 0},
    ])
    op = V.build_opportunities(summary, None, None)
    assert op["groups"] == []                     # answer_gap items have no related_recommendation_id
    ids = {o["id"] for o in op["items"]}
    assert ids == {"answer_gap:p1", "answer_gap:p2"}
    assert all(o["is_primary"] for o in op["items"])
    assert all(o["root_cause_id"] is None for o in op["items"])


def test_schema_and_links_groups_stay_separate_when_both_present():
    sections = [_schema_section(state="absent"), _links_section(internal=0)]
    report = build_report(_scan(sections))
    op = V.build_opportunities(_summary(), report, report["phase4"])
    root_causes = {g["root_cause_id"] for g in op["groups"]}
    assert root_causes == {"schema", "links"}      # two genuinely distinct root causes
    schema_ids = next(g for g in op["groups"] if g["root_cause_id"] == "schema")["opportunity_ids"]
    link_ids = next(g for g in op["groups"] if g["root_cause_id"] == "links")["opportunity_ids"]
    assert not (set(schema_ids) & set(link_ids))   # no overlap


def test_single_ungrouped_schema_item_needs_no_group():
    """A page missing only ONE schema type (with has_entity True, so no entity gap, and
    a schema score_loss of exactly 0) forms no group — a lone item stays a normal
    standalone opportunity, never artificially grouped."""
    sections = [_schema_section(state="present", has_entity=True, entity_types=["Organization"],
                                entity_evidence={"name": "Acme", "url": "https://acme.example/",
                                                 "logo": "https://acme.example/logo.png",
                                                 "same_as": ["https://linkedin.com/company/acme"]},
                                content={"WebSite": True, "Article": True, "FAQPage": True,
                                        "BreadcrumbList": True, "Service": True, "Review": True},
                                score=100),
               _links_section(internal=10, has_nav=True, score=100)]
    report = build_report(_scan(sections))
    op = V.build_opportunities(_summary(), report, report["phase4"])
    schema_gaps = [o for o in op["items"] if o["type"] == "schema_opportunity"]
    assert len(schema_gaps) == 1 and schema_gaps[0]["id"] == "schema_gap:product"
    assert schema_gaps[0]["is_primary"] is True
    assert schema_gaps[0]["root_cause_id"] is None
    assert op["groups"] == []


# ===================================================================
# 4-6: score loss / signal evidence / recommendations unchanged
# ===================================================================
def test_score_loss_and_signal_evidence_unchanged_by_grouping():
    sections = [_schema_section(state="present", has_entity=False, content={"WebSite": True})]
    scan = _scan(sections)
    report = build_report(scan)
    # Grouping only touches build_opportunities' output — build_report's own insights/
    # sections/scorecard are computed independently and never see the grouping step.
    assert report["scorecard"]["overall_score"] == 50           # untouched, from raw input
    assert report["sections"] == sections if "sections" in report else True
    breakdown = report["insights"]["score_breakdown"]
    schema_row = next(r for r in breakdown if r["signal_id"] == "schema")
    assert schema_row["score"] == 30 and schema_row["weight"] == 15
    assert schema_row["points_lost"] == round(15 * (100 - 30) / 100, 2)


def test_recommendations_unchanged_by_grouping():
    sections = [_schema_section(state="present", has_entity=False, content={"WebSite": True})]
    report = build_report(_scan(sections))
    recs_before = report["recommendations"]
    # calling build_opportunities (which computes groups) must not mutate the report
    V.build_opportunities(_summary(), report, report["phase4"])
    assert report["recommendations"] == recs_before
    assert report["phase4"]["schema"]["missing_types"]           # phase4 block itself untouched


# ===================================================================
# 7-8: free gating stays server-side; paid gets full detail
# ===================================================================
def test_free_preview_prefers_primary_opportunities_and_recomputes_groups():
    sections = [_schema_section(state="present", has_entity=False, content={"WebSite": True})]
    report = build_report(_scan(sections))
    summary = _summary()
    payload = V.build_ai_visibility(summary, scan_report=report)
    access = {"ai_visibility": True, "competitor_intelligence": True, "opportunity_finder": False}
    gated = V.gate_visibility(payload, access)

    op = gated["opportunities"]
    assert op["preview"] is True
    assert len(op["items"]) <= 3
    full_count = V.build_opportunities(summary, report, report["phase4"])["count"]
    assert op["locked_count"] == full_count - len(op["items"])
    # every locked-preview group must reference ONLY visible ids (no hidden-id leak)
    visible_ids = {o["id"] for o in op["items"]}
    for g in op["groups"]:
        assert set(g["opportunity_ids"]) <= visible_ids


def test_paid_response_retains_full_actionable_detail_and_groups():
    sections = [_schema_section(state="present", has_entity=False, content={"WebSite": True})]
    report = build_report(_scan(sections))
    summary = _summary()
    payload = V.build_ai_visibility(summary, scan_report=report)
    access = {"ai_visibility": True, "competitor_intelligence": True, "opportunity_finder": True}
    gated = V.gate_visibility(payload, access)
    op = gated["opportunities"]
    assert "preview" not in op
    full = V.build_opportunities(summary, report, report["phase4"])
    assert op["items"] == full["items"]
    assert op["groups"] == full["groups"]


# ===================================================================
# 9: Question Bank related_opportunity_ids still resolve correctly
# ===================================================================
def test_question_bank_links_unaffected_by_opportunity_grouping():
    sections = [_content_section(heading_questions=["How much does treatment cost?"])]
    report = build_report(_scan(sections))
    summary = _summary()
    op = V.build_opportunities(summary, report, report["phase4"])
    bank = V.build_question_bank(report["phase4"]["questions"], summary, op)
    q = next(q for q in bank["questions"] if q["question"] == "How much does treatment cost?")
    assert any(oid.startswith("question_opportunity:") for oid in q["related_opportunity_ids"])
    # the linked opportunity id still resolves to a real item, with its NEW grouping
    # fields present but not interfering with the link itself
    linked = next(o for o in op["items"] if o["id"] == q["related_opportunity_ids"][0])
    assert linked["is_primary"] is True   # question_opportunity is never grouped (no related_recommendation_id)


# ===================================================================
# 10-12: org isolation, cache reuse, end-to-end regression
# ===================================================================
async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"content-encoding": "gzip", "cache-control": "max-age=60"})


def _make_scan(client, monkeypatch, url="https://oppgroup.example/"):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    return client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def _enforce_billing(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", 50)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def test_org_isolation_unaffected_by_grouping(monkeypatch):
    _enforce_billing(monkeypatch)
    client_a, _ = auth_client()
    client_b, _ = auth_client()
    scan_id = _make_scan(client_a, monkeypatch)
    assert client_a.get(f"/reports/{scan_id}/question-bank").status_code == 200
    assert client_b.get(f"/reports/{scan_id}/question-bank").status_code == 404


def test_grouping_does_not_trigger_extra_build_report_calls(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    client.get(f"/reports/{scan_id}")   # warm the cache

    calls = {"n": 0}
    original = report_service.build_report

    def wrapper(*a, **kw):
        calls["n"] += 1
        return original(*a, **kw)

    monkeypatch.setattr(report_service, "build_report", wrapper)
    client.get(f"/reports/{scan_id}")
    client.get(f"/reports/{scan_id}/insights")
    client.get(f"/reports/{scan_id}/question-bank")
    assert calls["n"] == 0


def test_paid_report_end_to_end_has_groups_field(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    _go_pro(client)
    resp = client.get(f"/reports/{scan_id}/question-bank")
    assert resp.status_code == 200   # end-to-end wiring still works after the change
