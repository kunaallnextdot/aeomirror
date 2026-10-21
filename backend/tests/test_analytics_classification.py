"""First-party channel classifier (app/services/analytics/classification.py) — the
GA4-independent counterpart to the GA4 classifier, reusing its same known-AI-host
table so there is one centrally maintained list, not two divergent ones."""
import pytest

from app.services.analytics.classification import classify_channel


@pytest.mark.parametrize("referrer,expected_channel", [
    ("https://chat.openai.com/c/abc", "chatgpt"),
    ("https://chatgpt.com/share/xyz", "chatgpt"),
    ("https://www.perplexity.ai/search?q=x", "perplexity"),
    ("https://gemini.google.com/app", "gemini"),
    ("https://copilot.microsoft.com/", "copilot"),
    ("https://claude.ai/chat/abc", "claude"),
])
def test_known_ai_assistant_referrers_are_classified(referrer, expected_channel):
    channel, host = classify_channel(referrer=referrer, landing_host="example.com")
    assert channel == expected_channel
    assert host


@pytest.mark.parametrize("referrer", [
    "https://notchatgpt.com/", "https://chatgpt.com.evil.example/",
    "https://fake-claude.ai.attacker.net/",
])
def test_lookalike_ai_domains_are_not_misclassified(referrer):
    channel, _ = classify_channel(referrer=referrer, landing_host="example.com")
    assert channel not in {"chatgpt", "claude", "perplexity", "gemini", "copilot"}


def test_ordinary_google_search_is_organic_not_ai():
    channel, _ = classify_channel(referrer="https://www.google.com/search?q=widgets",
                                  landing_host="example.com")
    assert channel == "organic_search"


def test_no_referrer_and_no_utm_is_direct():
    channel, host = classify_channel(referrer=None, landing_host="example.com")
    assert channel == "direct"
    assert host is None


def test_internal_navigation_referrer_is_treated_as_direct_not_referral():
    """A same-host 'referrer' (e.g. a restored tab mid-site) is not real external
    attribution evidence — must not be misreported as a referral."""
    channel, host = classify_channel(referrer="https://example.com/other-page",
                                     landing_host="example.com")
    assert channel == "direct"
    assert host is None


@pytest.mark.parametrize("host,expected", [
    ("facebook.com", "social"), ("l.facebook.com", "social"),
    ("instagram.com", "social"), ("linkedin.com", "social"), ("t.co", "social"),
])
def test_known_social_referrers(host, expected):
    channel, _ = classify_channel(referrer=f"https://{host}/post/1", landing_host="example.com")
    assert channel == expected


def test_unrecognized_external_host_is_referral():
    channel, host = classify_channel(referrer="https://some-blog.example.net/post",
                                     landing_host="example.com")
    assert channel == "referral"
    assert host == "some-blog.example.net"


@pytest.mark.parametrize("medium,expected", [
    ("cpc", "paid"), ("ppc", "paid"), ("paid-social", "paid"),
    ("email", "email"), ("newsletter", "email"),
    ("social", "social"),
])
def test_explicit_utm_medium_wins_over_referrer_inference(medium, expected):
    # A referrer that WOULD be "organic_search" on its own — the explicit UTM tag
    # is the site owner's own deliberate attribution and takes priority.
    channel, _ = classify_channel(referrer="https://www.google.com/search?q=x",
                                  utm_source="newsletter", utm_medium=medium,
                                  landing_host="example.com")
    assert channel == expected


def test_utm_source_naming_an_ai_assistant_is_recognized_too():
    channel, _ = classify_channel(referrer=None, utm_source="chatgpt.com", utm_medium="referral")
    assert channel == "chatgpt"


def test_viewing_an_aeo_page_alone_never_implies_an_ai_source():
    """The classifier has no concept of 'page content' at all — it can only ever see
    referrer/UTM evidence, which is the point: it CANNOT infer AI traffic from what
    page was viewed."""
    channel, _ = classify_channel(referrer=None, landing_host="example.com")
    assert channel == "direct"


@pytest.mark.parametrize("referrer,utm_source,utm_medium", [
    (12345, None, None),             # not a string
    (None, {"not": "a string"}, None),
    ("not a valid url at all ####", None, None),
])
def test_malformed_input_never_raises(referrer, utm_source, utm_medium):
    channel, _ = classify_channel(referrer=referrer, utm_source=utm_source, utm_medium=utm_medium)
    assert channel in {"direct", "unknown", "referral"}
