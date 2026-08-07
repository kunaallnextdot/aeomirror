"""Schema detection: robust JSON-LD entity extraction + the three distinct states.

Drives the schema signal end-to-end (HTML -> SignalContext -> analyze) so the parse
layer, multi-block handling and @graph/subtype flattening are all exercised, plus the
pure extractor helpers and the de-hardcoded recommendation example.
"""
from app.reports.engine import build_report
from app.scanner.models import PageBundle
from app.scanner.schema_extract import (
    analyze_jsonld, collect_nodes, has_entity_schema, types_of,
)
from app.scanner.signals import schema as schema_sig
from app.scanner.signals.base import SignalContext


def _ctx(*ld_json_blocks: str) -> SignalContext:
    scripts = "".join(
        f'<script type="application/ld+json">{b}</script>' for b in ld_json_blocks
    )
    html = f"<!doctype html><html><head>{scripts}</head><body><h1>Hi</h1></body></html>"
    return SignalContext(PageBundle(url="https://audited.example/", html=html))


def _analyze(*blocks):
    return schema_sig.analyze(_ctx(*blocks))


# ------------------------- MUST detect an entity -------------------------
def test_graph_wrapper_with_organization():
    r = _analyze('{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"X"}]}')
    assert r.evidence["state"] == "present"
    assert r.evidence["has_entity"] is True
    assert "Organization" in r.evidence["entity_types"]


def test_top_level_organization_no_graph():
    r = _analyze('{"@context":"https://schema.org","@type":"Organization","name":"X"}')
    assert r.evidence["has_entity"] is True


def test_type_array_organization_localbusiness():
    r = _analyze('{"@context":"https://schema.org","@type":["Organization","LocalBusiness"],"name":"X"}')
    assert r.evidence["has_entity"] is True


def test_localbusiness_medical_subtype_dentist():
    r = _analyze('{"@context":"https://schema.org","@type":"Dentist","name":"Dr X"}')
    assert r.evidence["has_entity"] is True
    assert "Dentist" in r.evidence["entity_types"]


def test_two_scripts_entity_only_in_second():
    r = _analyze(
        '{"@context":"https://schema.org","@type":"WebSite","url":"https://audited.example/"}',
        '{"@context":"https://schema.org","@type":"Organization","name":"X"}',
    )
    assert r.evidence["has_entity"] is True
    assert r.evidence["blocks"] == 2


def test_organization_nested_under_publisher():
    r = _analyze('{"@context":"https://schema.org","@type":"Article",'
                 '"publisher":{"@type":"Organization","name":"X"}}')
    assert r.evidence["has_entity"] is True


# ------------------------- MUST NOT detect an entity -------------------------
def test_only_breadcrumb_is_not_an_entity():
    r = _analyze('{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[]}')
    assert r.evidence["state"] == "present"
    assert r.evidence["has_entity"] is False
    assert r.evidence["content"].get("BreadcrumbList") is True
    # copy must say "present, entity missing" — NOT "no schema"
    assert any("no Organization/LocalBusiness" in i for i in r.issues)


def test_malformed_json_is_flagged_malformed_not_absent():
    r = _analyze("{ this is : not valid json ,,, }")
    assert r.evidence["state"] == "malformed"        # distinct from 'absent'
    assert any("failed to parse" in i for i in r.issues)


def test_no_blocks_is_absent_distinct_state():
    r = schema_sig.analyze(SignalContext(PageBundle(
        url="https://x/", html="<html><body>no schema here</body></html>")))
    assert r.evidence["state"] == "absent"
    assert r.score == 20


def test_three_states_produce_distinct_copy():
    absent = schema_sig.analyze(SignalContext(PageBundle(url="https://x/", html="<html></html>")))
    malformed = _analyze("{bad json}")
    missing = _analyze('{"@context":"https://schema.org","@type":"BreadcrumbList"}')
    msgs = {absent.issues[0], malformed.issues[0], missing.issues[0]}
    assert len(msgs) == 3   # all three read differently


# ------------------------- pure extractor helpers -------------------------
def test_types_of_handles_string_and_array():
    assert types_of({"@type": "Organization"}) == ["Organization"]
    assert types_of({"@type": ["Organization", "LocalBusiness"]}) == ["Organization", "LocalBusiness"]
    assert types_of({"@type": ["Organization", 5]}) == ["Organization"]   # ignores non-strings
    assert types_of({}) == []


def test_collect_nodes_flattens_graph_and_nesting():
    doc = {"@graph": [{"@type": "WebSite"}, {"@type": "Article",
                                             "author": {"@type": "Person"}}]}
    types = {t for n in collect_nodes(doc) for t in types_of(n)}
    assert {"WebSite", "Article", "Person"} <= types


def test_analyze_jsonld_states_directly():
    assert analyze_jsonld([])["state"] == "absent"
    assert analyze_jsonld([{"__parse_error__": True}])["state"] == "malformed"
    present = analyze_jsonld([{"@type": "Organization"}])
    assert present["state"] == "present" and has_entity_schema([{"@type": "Dentist"}])


# ------------------------- task 6: no hardcoded AEOMirror/example.com -------------------------
def test_recommendation_example_localized_to_audited_site():
    scan = {
        "scan_id": "s", "url": "https://smile-dental.com/", "domain": "smile-dental.com",
        "overall_score": 30, "scanner_version": "3.0.0", "scanned_at": "2026-01-01T00:00:00Z",
        "sections": [
            {"id": "schema", "label": "Structured Data", "score": 20, "status": "fail",
             "weight": 15, "issues": ["Structured data is present, but no Organization/LocalBusiness entity."],
             "recommendations": ["Add Organization JSON-LD."], "evidence": {}},
        ],
    }
    rec = build_report(scan)["recommendations"][0]
    example = rec["fix_template"]["implementation_example"]
    assert "AEOMirror" not in example and "example.com" not in example
    assert "smile-dental.com" in example
    assert "Smile Dental" in example    # brand derived from the audited domain
