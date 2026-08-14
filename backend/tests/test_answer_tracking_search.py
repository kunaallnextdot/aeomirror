"""FIX2 — web search wiring.

Before this fix web search was NEVER passed to either adapter: `ANSWER_TRACKING_ENABLE_SEARCH`
was recorded onto result rows but the flag was inert, so no provider could return citations.
These tests pin the fixed behaviour at the adapter, runner, and cost layers. No real network —
`post_json` is stubbed and records the exact request body each adapter builds.
"""
from __future__ import annotations

import asyncio

from app.config import settings
from app.db.models import PromptResult
from app.db.session import SessionLocal
from app.services.answer_tracking import providers as at_providers
from app.services.answer_tracking import runner, service
from app.services.answer_tracking.providers import anthropic_provider, openai_provider
from tests.authutil import auth_client
from tests.test_answer_tracking import FakeProvider, _make_set, _patch_providers


def _stub_post_json(module, monkeypatch, response):
    """Replace a provider module's post_json with a recorder returning `response`."""
    captured = {}

    async def fake(url, *, headers, json, timeout):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers
        return response

    monkeypatch.setattr(module, "post_json", fake)
    return captured


# --------------------------- Anthropic adapter ---------------------------
_ANTHROPIC_SEARCH_RESP = {
    "model": "claude-test",
    "usage": {"input_tokens": 5, "output_tokens": 10},
    "content": [
        {"type": "text", "text": "Acme is great. ",
         "citations": [{"type": "web_search_result_location", "url": "https://a.com", "title": "A"}]},
        {"type": "server_tool_use", "name": "web_search"},
        {"type": "web_search_tool_result", "content": []},
        {"type": "text", "text": "See more.",
         "citations": [{"url": "https://a.com", "title": "A-dup"},   # dup url -> dropped
                       {"url": "https://b.com", "title": "B"}]},
    ],
}


def test_anthropic_passes_search_tool_and_parses_multiblock_citations(monkeypatch):
    cap = _stub_post_json(anthropic_provider, monkeypatch, _ANTHROPIC_SEARCH_RESP)
    prov = anthropic_provider.AnthropicProvider(api_key="k", model="claude-test")
    res = asyncio.run(prov.query("q", timeout=10, search=True))
    # tool wired with the configured version string
    tools = cap["body"]["tools"]
    assert tools == [{"type": settings.answer_tracking_anthropic_search_tool, "name": "web_search"}]
    # text concatenated across all text blocks
    assert res.text == "Acme is great. See more."
    # citations deduped by url, order preserved
    assert res.citations == [{"url": "https://a.com", "title": "A"},
                             {"url": "https://b.com", "title": "B"}]
    assert prov.supports_citations is True


def test_anthropic_no_search_sends_no_tool_and_citations_none(monkeypatch):
    cap = _stub_post_json(anthropic_provider, monkeypatch,
                          {"model": "claude-test", "content": [{"type": "text", "text": "hi"}]})
    prov = anthropic_provider.AnthropicProvider(api_key="k", model="claude-test")
    res = asyncio.run(prov.query("q", timeout=10, search=False))
    assert "tools" not in cap["body"]
    assert res.citations is None           # cannot report (no search) != [] (searched, none)
    assert res.text == "hi"


# --------------------------- OpenAI adapter ---------------------------
_OPENAI_SEARCH_RESP = {
    "model": "gpt-test",
    "usage": {"input_tokens": 3, "output_tokens": 7},
    "output": [
        {"type": "web_search_call"},
        {"type": "message", "content": [
            {"type": "output_text", "text": "Acme wins.",
             "annotations": [{"type": "url_citation", "url": "https://x.com", "title": "X"},
                             {"type": "url_citation", "url": "https://x.com", "title": "dup"},
                             {"type": "url_citation", "url": "https://y.com", "title": "Y"}]}]},
    ],
}


def test_openai_passes_search_tool_and_parses_url_citations(monkeypatch):
    cap = _stub_post_json(openai_provider, monkeypatch, _OPENAI_SEARCH_RESP)
    prov = openai_provider.OpenAIProvider(api_key="k", model="gpt-test")
    res = asyncio.run(prov.query("q", timeout=10, search=True))
    assert cap["url"].endswith("/v1/responses")          # Responses API, not chat completions
    assert cap["body"]["tools"] == [{"type": "web_search"}]
    assert res.text == "Acme wins."
    assert res.citations == [{"url": "https://x.com", "title": "X"},
                             {"url": "https://y.com", "title": "Y"}]
    assert prov.supports_citations is True


def test_openai_no_search_sends_no_tool_and_citations_none(monkeypatch):
    cap = _stub_post_json(openai_provider, monkeypatch, {
        "model": "gpt-test",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
    })
    prov = openai_provider.OpenAIProvider(api_key="k", model="gpt-test")
    res = asyncio.run(prov.query("q", timeout=10, search=False))
    assert "tools" not in cap["body"]
    assert res.citations is None
    assert res.text == "hi"


# --------------------------- runner plumbs the flag ---------------------------
def test_runner_passes_search_flag_to_provider(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_enable_search", True)
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 1)
    fake = FakeProvider("anthropic", responses=["a"])
    _patch_providers(monkeypatch, [fake])
    db = SessionLocal()
    try:
        ps = _make_set(db, org, prompts=("q1",))
        run = service.create_run(db, ps)
        asyncio.run(runner.execute_run(db, run))
        # the flag actually reached provider.query (was inert before FIX2)
        assert fake.search_calls == [True]
        # and it was persisted on the result row
        r = db.query(PromptResult).filter(PromptResult.run_id == run.id).one()
        assert r.search_enabled is True
    finally:
        db.close()


def test_runner_search_off_when_disabled(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_enable_search", False)
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 1)
    fake = FakeProvider("anthropic", responses=["a"])
    _patch_providers(monkeypatch, [fake])
    db = SessionLocal()
    try:
        ps = _make_set(db, org, prompts=("q1",))
        run = service.create_run(db, ps)
        asyncio.run(runner.execute_run(db, run))
        assert fake.search_calls == [False]
    finally:
        db.close()


# --------------------------- cost reflects search ---------------------------
def test_call_rate_uses_search_rate_only_when_search_enabled(monkeypatch):
    monkeypatch.setattr(settings, "answer_tracking_rate_anthropic_usd", 0.01)
    monkeypatch.setattr(settings, "answer_tracking_search_rate_anthropic_usd", 0.03)
    assert at_providers.call_rate("anthropic", False) == 0.01
    assert at_providers.call_rate("anthropic", True) == 0.03


def test_estimate_uses_search_rate_when_search_enabled(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_enable_search", True)
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 2)
    monkeypatch.setattr(settings, "answer_tracking_rate_anthropic_usd", 0.01)
    monkeypatch.setattr(settings, "answer_tracking_search_rate_anthropic_usd", 0.03)
    _patch_providers(monkeypatch, [FakeProvider("anthropic", responses=["a"])])
    db = SessionLocal()
    try:
        ps = _make_set(db, org, prompts=("q1", "q2"))
        est = service.estimate_run(db, ps)
        assert est["search_enabled"] is True
        # 2 prompts x 1 provider x 2 runs = 4 answer calls, priced at the SEARCH rate 0.03
        assert est["answer_cost_usd"] == 0.12
    finally:
        db.close()
