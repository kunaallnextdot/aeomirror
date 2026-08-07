"""Structured-data signal: JSON-LD presence + entity/content detection + validity.

Robust to how real sites emit schema — @graph wrappers, array @type, LocalBusiness
subtypes (incl. medical: Dentist, MedicalBusiness, …), nested entities, and multiple
ld+json blocks — via app.scanner.schema_extract. The three JSON-LD states (absent /
malformed / present-but-no-entity) are reported DISTINCTLY, never collapsed into a
generic "no schema", and the actual detected types are surfaced in the evidence.
"""
from __future__ import annotations

from app.scanner.schema_extract import analyze_jsonld
from app.scanner.signals.base import SignalContext, SignalResult

ID, LABEL, WEIGHT = "schema", "Structured Data", 15


def analyze(ctx: SignalContext) -> SignalResult:
    r = analyze_jsonld(ctx.jsonld)
    state = r["state"]

    # State 1 — no JSON-LD on the page at all.
    if state == "absent":
        return SignalResult.build(
            ID, LABEL, WEIGHT, 20,
            issues=["No JSON-LD structured data found — AI engines have no "
                    "machine-readable entity to identify."],
            recommendations=["Add JSON-LD: at minimum Organization and WebSite; "
                             "Article/FAQ/Breadcrumb where relevant."],
            evidence={"state": state, "blocks": 0, "detected_types": [],
                      "entity_types": [], "has_entity": False},
        )

    # State 2 — JSON-LD blocks present but NONE parsed (all malformed).
    if state == "malformed":
        return SignalResult.build(
            ID, LABEL, WEIGHT, 25,
            issues=["JSON-LD is present but failed to parse — malformed schema is "
                    "invisible to AI engines."],
            recommendations=["Fix the malformed JSON-LD (validate at "
                             "validator.schema.org) so engines can read it."],
            evidence={"state": state, "blocks": r["blocks"],
                      "malformed": r["malformed"], "detected_types": [],
                      "entity_types": [], "has_entity": False},
        )

    # State 3 — JSON-LD present and parsed. Score from what was ACTUALLY detected.
    content = r["content"]
    detected = r["types"]
    issues: list = []
    recs: list = []
    score = 30.0  # valid JSON-LD present

    if r["has_entity"]:
        score += 25
    else:
        # Present, but no brand entity — a DIFFERENT problem from "no schema".
        issues.append("Structured data is present, but no Organization/LocalBusiness "
                      "entity — AI can't reliably identify the brand entity.")
        recs.append("Add Organization (or a LocalBusiness type such as "
                    "MedicalBusiness/Dentist for a practice) JSON-LD: name, url, logo, sameAs.")

    if content.get("WebSite"):
        score += 10
    if content.get("Article") or content.get("FAQPage"):
        score += 20
    else:
        recs.append("Add Article or FAQPage schema matching the page content.")
    if content.get("BreadcrumbList"):
        score += 10

    # Some (not all) blocks malformed → partial credit already given, small penalty.
    if r["malformed"]:
        score -= 10
        issues.append(f"{r['malformed']} JSON-LD block(s) present but malformed — "
                      "engines skip unparseable schema.")
        recs.append("Fix the malformed JSON-LD block(s) (validate at validator.schema.org).")

    return SignalResult.build(
        ID, LABEL, WEIGHT, score, issues=issues, recommendations=recs,
        evidence={
            "state": state,
            "blocks": r["blocks"],
            "malformed": r["malformed"],
            "has_entity": r["has_entity"],
            "entity_types": r["entity_types"],           # e.g. ["Organization"]
            "detected_types": detected[:30],             # actual data: "Found: …"
            "content": {k: v for k, v in content.items() if v},
        },
    )
