"""Deterministic evidence extraction (app/services/url_intelligence/evidence.py) —
reuses the scanner's own SignalContext/schema_extract, so this mainly verifies the
NEW fields (missing_content_elements, product, fingerprint) are built correctly and
never invented."""
from app.scanner.models import PageBundle
from app.services.url_intelligence.evidence import content_fingerprint, extract_evidence

_GOOD_HTML = """
<html><head><title>Wireless Mouse - Acme Store</title>
<meta name="description" content="Buy the Acme wireless mouse.">
<link rel="canonical" href="https://example.com/products/mouse">
<script type="application/ld+json">{
  "@type": "Product", "name": "Wireless Mouse",
  "offers": {"@type": "Offer", "price": "29.99", "priceCurrency": "USD", "availability": "InStock"},
  "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.5", "reviewCount": "120"}
}</script>
</head><body><h1>Wireless Mouse</h1><p>%s</p>
<a href="/products/other">Other product</a><a href="https://external.example/">External</a>
</body></html>
""" % ("word " * 200)

_THIN_HTML = "<html><head></head><body><p>too short</p></body></html>"


def _bundle(html: str) -> PageBundle:
    return PageBundle(url="https://example.com/products/mouse", html=html, status_code=200)


def test_extracts_real_product_attributes_never_invented():
    evidence = extract_evidence(_bundle(_GOOD_HTML))
    assert evidence["product"]["price"] == "29.99"
    assert evidence["product"]["price_currency"] == "USD"
    assert evidence["product"]["rating_value"] == "4.5"
    assert evidence["title"] == "Wireless Mouse - Acme Store"
    assert evidence["canonical_url"] == "https://example.com/products/mouse"
    assert "missing_title" not in evidence["missing_content_elements"]


def test_thin_page_flags_missing_elements_truthfully():
    evidence = extract_evidence(_bundle(_THIN_HTML))
    assert evidence["product"] is None
    assert "missing_title" in evidence["missing_content_elements"]
    assert "missing_h1" in evidence["missing_content_elements"]
    assert "thin_content" in evidence["missing_content_elements"]
    assert "no_structured_data" in evidence["missing_content_elements"]


def test_fingerprint_stable_across_identical_content():
    e1 = extract_evidence(_bundle(_GOOD_HTML))
    e2 = extract_evidence(_bundle(_GOOD_HTML))
    assert content_fingerprint(e1) == content_fingerprint(e2)


def test_fingerprint_changes_when_title_changes():
    e1 = extract_evidence(_bundle(_GOOD_HTML))
    e2 = extract_evidence(_bundle(_GOOD_HTML.replace("Wireless Mouse - Acme Store", "Different Title")))
    assert content_fingerprint(e1) != content_fingerprint(e2)


def test_fingerprint_ignores_volatile_http_status():
    b1 = _bundle(_GOOD_HTML)
    b2 = PageBundle(url=b1.url, html=_GOOD_HTML, status_code=200, redirect_chain=[{"a": 1}])
    assert content_fingerprint(extract_evidence(b1)) == content_fingerprint(extract_evidence(b2))
