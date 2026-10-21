"""Deterministic page-type classification, template fingerprinting, and business-
intent weighting (app/services/url_intelligence/classification.py). No AI, no
network — pure functions of a URL + evidence dict."""
from app.services.url_intelligence.classification import (
    PAGE_BOOKING, PAGE_LEGAL_UTILITY, PAGE_OTHER, PAGE_PRICING, PAGE_PRODUCT,
    classify_page_type, classify_template, intent_weight,
)

_EMPTY_EVIDENCE = {"title": "", "schema": {"types": [], "content": {}},
                   "heading_counts": {"h1": 0}}


def test_legal_page_classified_by_path_even_with_misleading_title():
    evidence = {**_EMPTY_EVIDENCE, "title": "Pricing Plans"}  # misleading title
    assert classify_page_type("https://example.com/privacy-policy", evidence) == PAGE_LEGAL_UTILITY


def test_pricing_page_classified_by_path():
    assert classify_page_type("https://example.com/pricing", _EMPTY_EVIDENCE) == PAGE_PRICING


def test_pricing_page_classified_by_title_when_path_is_generic():
    evidence = {**_EMPTY_EVIDENCE, "title": "Our Pricing & Plans"}
    assert classify_page_type("https://example.com/page?id=4", evidence) == PAGE_PRICING


def test_product_schema_wins_over_a_generic_path():
    evidence = {**_EMPTY_EVIDENCE, "schema": {"types": ["Product"], "content": {}}}
    assert classify_page_type("https://example.com/widgets/42", evidence) == PAGE_PRODUCT


def test_booking_page_classified_by_path():
    assert classify_page_type("https://example.com/bookings/new", _EMPTY_EVIDENCE) == PAGE_BOOKING


def test_no_signal_falls_back_to_other():
    assert classify_page_type("https://example.com/", _EMPTY_EVIDENCE) == PAGE_OTHER


def test_legal_pages_never_get_high_business_intent_weight():
    assert intent_weight(PAGE_LEGAL_UTILITY) < intent_weight(PAGE_PRODUCT)
    assert intent_weight(PAGE_LEGAL_UTILITY) < intent_weight(PAGE_PRICING)
    assert intent_weight(PAGE_LEGAL_UTILITY) <= 0.1


def test_template_fingerprint_is_deterministic_for_identical_inputs():
    evidence = {"schema": {"types": ["Product"]}, "heading_counts": {"h1": 1}}
    a = classify_template("https://example.com/widgets/1", PAGE_PRODUCT, evidence)
    b = classify_template("https://example.com/widgets/2", PAGE_PRODUCT, evidence)
    assert a == b   # same page type + path shape + schema + heading pattern -> same template


def test_template_fingerprint_differs_for_different_page_types():
    evidence = {"schema": {"types": []}, "heading_counts": {"h1": 1}}
    product = classify_template("https://example.com/a/1", PAGE_PRODUCT, evidence)
    other = classify_template("https://example.com/a/1", PAGE_OTHER, evidence)
    assert product != other
