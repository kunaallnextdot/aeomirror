"""AI Answer Tracking (Part A): data model, provider layer, execution engine, cost
guards, scheduling due-logic, API CRUD + cross-org isolation, and admin_delete_org
cleanup. No real network — providers are faked and enabled_providers() is monkeypatched.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.config import settings
from app.db.models import (
    RUN_COMPLETED, RUN_PARTIAL,
    Monitor, Organization, PromptResult, PromptRun, PromptSet, TrackedPrompt, User,
)
from app.db.session import SessionLocal
from app.main import app
from app.services.answer_tracking import providers as at_providers
from app.services.answer_tracking import runner, scheduling, service
from app.services.answer_tracking.providers.base import ProviderError, ProviderResult
from fastapi.testclient import TestClient
from tests.authutil import auth_client


# ------------------------------- fake providers -------------------------------
class FakeProvider:
    """A provider stand-in. `responses` are cycled by call count; `error` (if set) is
    raised instead. Counts calls so retry behaviour is observable."""
    supports_citations = False

    def __init__(self, name="anthropic", *, responses=None, error=None, citations=None,
                 model="fake-1", fail_first=None):
        self.name = name
        self.model = model
        self._responses = responses or ["A generic answer."]
        self._error = error
        self._citations = citations
        self._fail_first = fail_first     # raise this on the FIRST call only, then succeed
        self.calls = 0

    async def query(self, prompt, *, timeout):
        idx = self.calls
        self.calls += 1
        if self._fail_first is not None and idx == 0:
            raise self._fail_first
        if self._error is not None:
            raise self._error
        text = self._responses[idx % len(self._responses)]
        return ProviderResult(text=text, citations=self._citations, model=self.model,
                              tokens={"input": 1, "output": 1}, latency_ms=5)


def _patch_providers(monkeypatch, provs):
    monkeypatch.setattr(at_providers, "enabled_providers", lambda: list(provs))


# ------------------------------- fixtures -------------------------------
def _make_set(db, org_id, *, name="Set", monitor_id=None, prompts=("What is best?",)):
    ps = PromptSet(organization_id=org_id, name=name, monitor_id=monitor_id)
    db.add(ps); db.commit(); db.refresh(ps)
    for t in prompts:
        db.add(TrackedPrompt(prompt_set_id=ps.id, organization_id=org_id, text=t))
    db.commit()
    return ps


def _make_monitor(db, org_id, *, name="Acme", url="acme.com"):
    m = Monitor(organization_id=org_id, url=f"https://{url}/", normalized_url=url,
                status="active", frequency="weekly", name=name)
    db.add(m); db.commit(); db.refresh(m)
    return m


# =====================================================================
# provider registry — skip-without-crash
# =====================================================================
def test_provider_missing_key_is_skipped_not_fatal(monkeypatch):
    monkeypatch.setattr(settings, "answer_tracking_providers", "anthropic,openai")
    monkeypatch.setattr(settings, "anthropic_api_key", "k-anthropic")
    monkeypatch.setattr(settings, "answer_tracking_model_anthropic", "claude-x")
    monkeypatch.setattr(settings, "openai_api_key", None)          # no key -> skipped
    monkeypatch.setattr(settings, "answer_tracking_model_openai", "gpt-x")
    names = [p.name for p in at_providers.enabled_providers()]
    assert names == ["anthropic"]                                  # openai skipped, no crash


def test_provider_missing_model_is_skipped(monkeypatch):
    monkeypatch.setattr(settings, "answer_tracking_providers", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    monkeypatch.setattr(settings, "answer_tracking_model_anthropic", "")   # no model -> skipped
    assert at_providers.enabled_providers() == []


# =====================================================================
# execution engine
# =====================================================================
def test_one_provider_failing_leaves_run_partial(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 1)
    _patch_providers(monkeypatch, [
        FakeProvider("anthropic", responses=["ok"]),
        FakeProvider("openai", error=ProviderError("boom", status_code=401)),
    ])
    db = SessionLocal()
    try:
        ps = _make_set(db, org)
        run = service.create_run(db, ps)
        asyncio.run(runner.execute_run(db, run))
        rows = db.query(PromptResult).filter(PromptResult.run_id == run.id).all()
        assert run.status == RUN_PARTIAL
        assert len(rows) == 2                                       # BOTH persisted (failure is data)
        assert sum(1 for r in rows if r.error) == 1
        assert sum(1 for r in rows if not r.error) == 1
    finally:
        db.close()


def test_retry_on_429_but_not_on_401(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 1)

    flaky = FakeProvider("anthropic", responses=["recovered"],
                         fail_first=ProviderError("rate", status_code=429))
    authfail = FakeProvider("openai", error=ProviderError("nope", status_code=401))
    _patch_providers(monkeypatch, [flaky, authfail])
    db = SessionLocal()
    try:
        ps = _make_set(db, org)
        run = service.create_run(db, ps)
        asyncio.run(runner.execute_run(db, run))
        assert flaky.calls == 2        # 429 -> retried once, then succeeded
        assert authfail.calls == 1     # 401 -> NOT retried
        rows = {r.provider: r for r in
                db.query(PromptResult).filter(PromptResult.run_id == run.id).all()}
        assert rows["anthropic"].error is None
        assert rows["openai"].error is not None
    finally:
        db.close()


def test_runs_per_prompt_produces_distinct_run_index(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 2)
    _patch_providers(monkeypatch, [FakeProvider("anthropic", responses=["a", "b"])])
    db = SessionLocal()
    try:
        ps = _make_set(db, org)                                    # no monitor -> no adaptive
        run = service.create_run(db, ps)
        asyncio.run(runner.execute_run(db, run))
        rows = db.query(PromptResult).filter(PromptResult.run_id == run.id).all()
        assert sorted(r.run_index for r in rows) == [0, 1]
        assert all(r.is_adaptive_run is False for r in rows)
    finally:
        db.close()


def test_adaptive_third_run_fires_on_disagreement(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 2)
    # base runs disagree on mention: one names Acme, the other does not.
    _patch_providers(monkeypatch, [
        FakeProvider("anthropic", responses=["Acme is great", "I have no idea"])])
    db = SessionLocal()
    try:
        mon = _make_monitor(db, org, name="Acme", url="acme.com")
        ps = _make_set(db, org, monitor_id=mon.id)
        run = service.create_run(db, ps)
        asyncio.run(runner.execute_run(db, run))
        rows = db.query(PromptResult).filter(PromptResult.run_id == run.id).all()
        adaptive = [r for r in rows if r.is_adaptive_run]
        assert len(rows) == 3                                      # 2 base + 1 adaptive
        assert len(adaptive) == 1 and adaptive[0].run_index == 2   # flagged distinctly
    finally:
        db.close()


def test_adaptive_third_run_skipped_on_agreement(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 2)
    _patch_providers(monkeypatch, [
        FakeProvider("anthropic", responses=["Acme wins", "Acme again"])])   # both mention
    db = SessionLocal()
    try:
        mon = _make_monitor(db, org, name="Acme", url="acme.com")
        ps = _make_set(db, org, monitor_id=mon.id)
        run = service.create_run(db, ps)
        asyncio.run(runner.execute_run(db, run))
        rows = db.query(PromptResult).filter(PromptResult.run_id == run.id).all()
        assert len(rows) == 2                                      # no adaptive run
        assert all(r.is_adaptive_run is False for r in rows)
    finally:
        db.close()


def test_citations_none_vs_empty_preserved(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 1)
    _patch_providers(monkeypatch, [
        FakeProvider("anthropic", responses=["x"], citations=None),   # cannot report
        FakeProvider("perplexity", responses=["y"], citations=[]),    # searched, cited nothing
    ])
    db = SessionLocal()
    try:
        ps = _make_set(db, org)
        run = service.create_run(db, ps)
        asyncio.run(runner.execute_run(db, run))
        rows = {r.provider: r for r in
                db.query(PromptResult).filter(PromptResult.run_id == run.id).all()}
        assert rows["anthropic"].citations is None                 # None preserved
        assert rows["perplexity"].citations == []                  # [] preserved (distinct)
        assert all(r.search_enabled == settings.answer_tracking_enable_search
                   for r in rows.values())
    finally:
        db.close()


def test_cost_estimate_matches_actual_call_count(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 2)
    monkeypatch.setattr(settings, "answer_tracking_rate_anthropic_usd", 0.01)
    _patch_providers(monkeypatch, [FakeProvider("anthropic", responses=["a"])])
    db = SessionLocal()
    try:
        ps = _make_set(db, org, prompts=("q1", "q2"))              # 2 prompts, no monitor
        est = service.estimate_run(db, ps)
        run = service.create_run(db, ps)
        asyncio.run(runner.execute_run(db, run))
        assert est["call_count"] == 4 == run.total_calls           # 2 prompts x 1 provider x 2 runs
        assert abs(est["estimated_cost_usd"] - run.estimated_cost_usd) < 1e-9
        assert run.estimated_cost_usd == 0.04
    finally:
        db.close()


# =====================================================================
# cost guards (dedup interval + monthly limit + admin override)
# =====================================================================
def test_second_run_within_interval_refused_with_next_eligible(monkeypatch):
    client, body = auth_client()
    org = body["organization"]["id"]
    _patch_providers(monkeypatch, [FakeProvider("anthropic", responses=["ok"])])
    db = SessionLocal()
    try:
        ps_id = _make_set(db, org).id
    finally:
        db.close()

    assert client.post(f"/prompt-sets/{ps_id}/run").status_code == 200   # first run ok
    r2 = client.post(f"/prompt-sets/{ps_id}/run")                        # immediate second run
    assert r2.status_code == 429
    assert r2.headers["X-Run-Refused-Reason"] == "interval"
    assert "next run allowed at" in r2.json()["detail"].lower()          # names the next eligible time


def test_admin_override_bypasses_interval_and_is_logged(monkeypatch, caplog):
    client, body = auth_client()                                   # registrant is org OWNER
    org = body["organization"]["id"]
    _patch_providers(monkeypatch, [FakeProvider("anthropic", responses=["ok"])])
    db = SessionLocal()
    try:
        ps_id = _make_set(db, org).id
    finally:
        db.close()

    assert client.post(f"/prompt-sets/{ps_id}/run").status_code == 200
    with caplog.at_level(logging.WARNING, logger="app.answer_tracking.service"):
        r2 = client.post(f"/prompt-sets/{ps_id}/run?override=true")
    assert r2.status_code == 200                                   # interval bypassed
    assert any("interval override applied" in rec.message for rec in caplog.records)


def test_monthly_run_limit_refused_cleanly(monkeypatch):
    client, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_monthly_run_limit", 0)   # nothing allowed
    _patch_providers(monkeypatch, [FakeProvider("anthropic", responses=["ok"])])
    db = SessionLocal()
    try:
        ps_id = _make_set(db, org).id
    finally:
        db.close()
    r = client.post(f"/prompt-sets/{ps_id}/run")
    assert r.status_code == 429 and r.headers["X-Run-Refused-Reason"] == "monthly_limit"
    assert "monthly run limit" in r.json()["detail"].lower()


# =====================================================================
# scheduling due-logic (biweekly == 14 days apart, NOT twice a week)
# =====================================================================
def test_biweekly_due_at_14_days_not_weekly(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_frequency", "biweekly")
    assert settings.answer_tracking_frequency_days == 14
    now = datetime(2026, 3, 1, 12, 0, 0)
    db = SessionLocal()
    try:
        ps = _make_set(db, org)
        # a completed run whose start we slide around `now`
        run = PromptRun(organization_id=org, prompt_set_id=ps.id, status=RUN_COMPLETED,
                        started_at=now - timedelta(days=7), completed_at=now - timedelta(days=7))
        db.add(run); db.commit()
        assert service.is_due(db, ps, now) is False                # 7 days -> NOT due (not weekly)
        run.started_at = now - timedelta(days=13); db.commit()
        assert service.is_due(db, ps, now) is False                # 13 days -> still not due
        run.started_at = now - timedelta(days=14); db.commit()
        assert service.is_due(db, ps, now) is True                 # 14 days -> due
    finally:
        db.close()


def test_scheduling_isolates_per_set_failure(monkeypatch):
    _, abody = auth_client()
    _, bbody = auth_client()
    orga, orgb = abody["organization"]["id"], bbody["organization"]["id"]
    now = datetime(2026, 3, 1, 12, 0, 0)
    db = SessionLocal()
    try:
        psa = _make_set(db, orga, name="A")
        psb = _make_set(db, orgb, name="B")
        for ps in (psa, psb):
            db.add(PromptRun(organization_id=ps.organization_id, prompt_set_id=ps.id,
                             status=RUN_COMPLETED, started_at=now - timedelta(days=20),
                             completed_at=now - timedelta(days=20)))
        db.commit()
    finally:
        db.close()

    ok = {"n": 0}

    async def fake_execute(db, run):
        if run.organization_id == orga:
            raise RuntimeError("set A blows up")
        ok["n"] += 1
        run.status = RUN_COMPLETED
        db.commit()
        return run
    monkeypatch.setattr(scheduling, "execute_run", fake_execute)

    started = asyncio.run(scheduling.run_scheduled(SessionLocal(), now=now))
    assert ok["n"] >= 1                                            # set B still ran despite A failing
    assert started >= 1


# =====================================================================
# API: max-prompt cap + cross-org isolation
# =====================================================================
def test_max_prompt_cap_enforced(monkeypatch):
    client, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_max_prompts", 3)
    ps = client.post("/prompt-sets", json={"name": "Capped"}).json()
    for i in range(3):
        assert client.post(f"/prompt-sets/{ps['id']}/prompts",
                           json={"text": f"prompt {i}"}).status_code == 200
    over = client.post(f"/prompt-sets/{ps['id']}/prompts", json={"text": "one too many"})
    assert over.status_code == 422 and "maximum" in over.json()["detail"].lower()


def test_cross_org_isolation(monkeypatch):
    client_a, abody = auth_client()
    client_b, _ = auth_client()
    org_a = abody["organization"]["id"]
    _patch_providers(monkeypatch, [FakeProvider("anthropic", responses=["ok"])])

    ps = client_a.post("/prompt-sets", json={"name": "A only"}).json()
    prompt = client_a.post(f"/prompt-sets/{ps['id']}/prompts", json={"text": "q"}).json()
    db = SessionLocal()
    try:
        run = PromptRun(organization_id=org_a, prompt_set_id=ps["id"], status=RUN_COMPLETED)
        db.add(run); db.commit(); db.refresh(run)
        run_id = run.id
    finally:
        db.close()

    # Every org-scoped endpoint must 404 for the other org (never leak existence).
    assert client_b.get(f"/prompt-sets/{ps['id']}").status_code == 404
    assert client_b.patch(f"/prompt-sets/{ps['id']}", json={"name": "x"}).status_code == 404
    assert client_b.delete(f"/prompt-sets/{ps['id']}").status_code == 404
    assert client_b.post(f"/prompt-sets/{ps['id']}/prompts", json={"text": "q"}).status_code == 404
    assert client_b.get(f"/prompt-sets/{ps['id']}/estimate").status_code == 404
    assert client_b.post(f"/prompt-sets/{ps['id']}/run").status_code == 404
    assert client_b.patch(f"/prompts/{prompt['id']}", json={"text": "z"}).status_code == 404
    assert client_b.delete(f"/prompts/{prompt['id']}").status_code == 404
    assert client_b.get(f"/prompt-runs/{run_id}").status_code == 404


def test_run_endpoint_executes_and_status_readable(monkeypatch):
    client, body = auth_client()
    _patch_providers(monkeypatch, [FakeProvider("anthropic", responses=["ok"])])
    ps = client.post("/prompt-sets", json={"name": "Runnable"}).json()
    client.post(f"/prompt-sets/{ps['id']}/prompts", json={"text": "q"})
    run = client.post(f"/prompt-sets/{ps['id']}/run").json()
    assert run["status"] == RUN_COMPLETED
    got = client.get(f"/prompt-runs/{run['run_id']}").json()
    assert got["total_calls"] >= 1 and "result_count" in got
    assert "analysis" not in got and "mentions" not in got          # no analysis surface (Part A)


# =====================================================================
# admin_delete_org removes all four answer-tracking tables
# =====================================================================
def test_admin_delete_org_purges_answer_tracking(monkeypatch):
    from app.api.deps import get_admin
    _, body = auth_client()
    org_id = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps = _make_set(db, org_id, prompts=("q",))
        run = PromptRun(organization_id=org_id, prompt_set_id=ps.id, status=RUN_COMPLETED)
        db.add(run); db.commit(); db.refresh(run)
        db.add(PromptResult(run_id=run.id, prompt_id="p", organization_id=org_id,
                            provider="anthropic", model="m", run_index=0))
        db.commit()
        owner = db.get(User, db.get(Organization, org_id).owner_id)
    finally:
        db.close()

    admin_client = TestClient(app)
    app.dependency_overrides[get_admin] = lambda: owner   # act as a platform admin
    try:
        assert admin_client.delete(f"/admin/organizations/{org_id}").status_code == 200
    finally:
        app.dependency_overrides.pop(get_admin, None)

    db = SessionLocal()
    try:
        for model in (PromptSet, TrackedPrompt, PromptRun, PromptResult):
            assert db.query(model).filter(model.organization_id == org_id).count() == 0
    finally:
        db.close()
