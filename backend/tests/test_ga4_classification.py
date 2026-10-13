"""AI-referral classification (app/services/integrations/classification.py) — pure,
deterministic, no I/O. The most security/honesty-sensitive piece of the GA4
integration: it must never claim Google AI Overview traffic is measured, and must
never let a lookalike domain spoof a known assistant."""
import pytest

from app.services.integrations.classification import classify_source


@pytest.mark.parametrize("source,medium,expected", [
    ("chatgpt.com", "referral", "chatgpt"),
    ("chat.openai.com", "referral", "chatgpt"),
    ("www.chatgpt.com", "referral", "chatgpt"),           # www. normalized away
    ("perplexity.ai", "referral", "perplexity"),
    ("www.perplexity.ai", "referral", "perplexity"),
    ("gemini.google.com", "referral", "gemini"),
    ("copilot.microsoft.com", "referral", "copilot"),
    ("claude.ai", "referral", "claude"),
    ("CHATGPT.COM", "referral", "chatgpt"),                # case-insensitive
])
def test_recognized_ai_hostnames_map_to_assistant(source, medium, expected):
    assert classify_source(source, medium) == expected


@pytest.mark.parametrize("source", [
    "chatgpt.com.evil.example",     # lookalike suffix — must NOT match
    "notchatgpt.com",               # lookalike prefix — must NOT match
    "fakechat.openai.com.attacker.net",
    "perplexity.ai.phish.example",
])
def test_lookalike_domains_are_not_classified_as_the_assistant(source):
    result = classify_source(source, "referral")
    assert result != "chatgpt"
    assert result != "perplexity"


def test_ordinary_google_organic_is_never_confirmed_ai_overview():
    # This is the single most important invariant in the whole integration: the GA4
    # API cannot separate an AI Overview click from any other organic Google result.
    assert classify_source("google", "organic") == "unattributable"


def test_direct_traffic_is_not_relabeled_as_ai():
    assert classify_source("(direct)", "(none)") == "unattributable"


@pytest.mark.parametrize("source,medium", [
    (None, None),
    ("", ""),
    ("(not set)", "(not set)"),
])
def test_missing_or_unavailable_source_is_unattributable(source, medium):
    assert classify_source(source, medium) == "unattributable"


def test_other_real_referrer_is_other_identifiable_not_unattributable():
    assert classify_source("news.ycombinator.com", "referral") == "other_identifiable"
    assert classify_source("bing.com", "organic") == "other_identifiable"


def test_malformed_source_values_do_not_raise():
    classify_source(12345, True)          # type: ignore[arg-type]
    classify_source({"not": "a string"}, None)   # type: ignore[arg-type]
