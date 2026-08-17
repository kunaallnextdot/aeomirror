"""Answer Tracking restructured: prompts belong to a SITE (monitor), not a free-floating
prompt set. Covers monitor-scoped isolation, the max-prompt cap, monitor_id stamping on
runs/results/analysis, starter-prompt suggestions, and the informational-prompt hint.
No real network — providers/extractor are faked.
"""
from __future__ import annotations

import asyncio

from app.config import settings
from app.db.models import (
    Monitor, PromptResult, PromptResultAnalysis, PromptRun, PromptSet, TrackedPrompt,
)
from app.db.session import SessionLocal
from app.services.answer_tracking import providers as at_providers
from app.services.answer_tracking import extraction, runner, service
from app.services.answer_tracking.providers.base import ProviderResult
from tests.authutil import auth_client
from tests.test_answer_tracking import FakeProvider, _patch_providers


def _load_migration():
    """Load the monitor-scoped migration module by path (alembic/versions is not a package)."""
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
        "c1a2b3d4e5f6_answer_tracking_monitor_scoped.py"
    spec = importlib.util.spec_from_file_location("_mig_monitor_scoped", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _monitor(db, org, *, name="Acme", url="acme.com"):
    m = Monitor(organization_id=org, url=f"https://{url}/", normalized_url=url,
                status="active", frequency="weekly", name=name)
    db.add(m); db.commit(); db.refresh(m)
    return m


# ------------------------- monitor-scoped prompt isolation -------------------------
def test_prompts_isolated_per_monitor_and_per_org():
    client_a, abody = auth_client()
    client_b, _ = auth_client()
    org_a = abody["organization"]["id"]
    db = SessionLocal()
    try:
        m1 = _monitor(db, org_a, name="Site One", url="one.com")
        m2 = _monitor(db, org_a, name="Site Two", url="two.com")
        m1_id, m2_id = m1.id, m2.id
    finally:
        db.close()

    # add a prompt to m1 only
    r = client_a.post(f"/monitors/{m1_id}/answer-tracking/prompts", json={"text": "best one.com tools"})
    assert r.status_code == 200
    # m1 has the prompt; m2 has none — prompts don't bleed across a site
    assert len(client_a.get(f"/monitors/{m1_id}/answer-tracking").json()["prompts"]) == 1
    assert len(client_a.get(f"/monitors/{m2_id}/answer-tracking").json()["prompts"]) == 0
    # another org cannot see or reach this monitor at all
    assert client_b.get(f"/monitors/{m1_id}/answer-tracking").status_code == 404
    assert client_b.post(f"/monitors/{m1_id}/answer-tracking/prompts", json={"text": "x"}).status_code == 404


def test_max_active_prompts_enforced_per_monitor():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        mid = _monitor(db, org).id
    finally:
        db.close()
    cap = settings.answer_tracking_max_prompts
    for i in range(cap):
        assert client.post(f"/monitors/{mid}/answer-tracking/prompts", json={"text": f"prompt {i}"}).status_code == 200
    over = client.post(f"/monitors/{mid}/answer-tracking/prompts", json={"text": "one too many"})
    assert over.status_code == 422
    assert str(cap) in over.json()["detail"]


def test_single_monitor_resolves_one_hidden_prompt_set():
    """Two GETs of the same monitor resolve to the SAME backing set (created once)."""
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        mid = _monitor(db, org).id
    finally:
        db.close()
    client.get(f"/monitors/{mid}/answer-tracking")
    client.get(f"/monitors/{mid}/answer-tracking")
    db = SessionLocal()
    try:
        sets = db.query(PromptSet).filter(PromptSet.monitor_id == mid).all()
        assert len(sets) == 1                       # exactly one prompt list per site
    finally:
        db.close()


# ------------------------- brand identity lives on the monitor -------------------------
def test_brand_fields_read_from_monitor():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        m = _monitor(db, org, name="Acme", url="acme.com")
        m.brand_aliases = ["AcmeCo"]
        m.competitor_domains = ["rival.com"]
        db.commit()
        mid = m.id
    finally:
        db.close()
    payload = client.get(f"/monitors/{mid}/answer-tracking").json()
    assert payload["brand_name"] == "Acme"
    assert payload["brand_domain"] == "acme.com"
    assert payload["brand_aliases"] == ["AcmeCo"]
    assert payload["competitor_domains"] == ["rival.com"]


# ------------------------- monitor_id stamped on run/result/analysis -------------------------
class _FakeExtractor:
    supports_citations = False
    name = "anthropic"
    model = "fake-x"

    async def query(self, prompt, *, timeout, search=False):
        return ProviderResult(
            text='{"brand_mentioned": true, "mention_context": "Acme is great.", '
                 '"sentiment": "positive", "brand_urls_cited": [], '
                 '"competitors_mentioned": [], "recommended_entities": [{"name": "Acme"}], '
                 '"position": 1}',
            citations=None, model=self.model, tokens=None, latency_ms=1)


def test_run_result_analysis_all_carry_monitor_id(monkeypatch):
    client, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 1)
    _patch_providers(monkeypatch, [FakeProvider("anthropic", responses=["Acme is great."])])
    monkeypatch.setattr(at_providers, "extraction_provider", lambda: _FakeExtractor())
    monkeypatch.setattr(at_providers, "gap_analysis_provider", lambda: None)
    db = SessionLocal()
    try:
        mid = _monitor(db, org).id
    finally:
        db.close()
    client.post(f"/monitors/{mid}/answer-tracking/prompts", json={"text": "best acme tools"})
    run = client.post(f"/monitors/{mid}/answer-tracking/run")
    assert run.status_code == 200
    run_id = run.json()["run_id"]

    db = SessionLocal()
    try:
        pr = db.get(PromptRun, run_id)
        assert pr.monitor_id == mid
        results = db.query(PromptResult).filter(PromptResult.run_id == run_id).all()
        assert results and all(r.monitor_id == mid for r in results)
        # extraction runs in the request path via the worker? run it explicitly here
        asyncio.run(extraction.extract_for_run(db, pr))
        analyses = db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run_id).all()
        assert analyses and all(a.monitor_id == mid for a in analyses)
    finally:
        db.close()


# ------------------------- starter-prompt suggestions (CHANGE 4) -------------------------
def test_suggestions_seeded_from_brand_name():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        mid = _monitor(db, org, name="Profound", url="tryprofound.com").id
    finally:
        db.close()
    sugg = client.get(f"/monitors/{mid}/answer-tracking").json()["suggestions"]
    assert len(sugg) == 3
    assert all("Profound" in s for s in sugg)       # seeded from the brand name


# ------------------------- informational-prompt hint (CHANGE 4b) -------------------------
def test_informational_prompt_hint_when_no_brand_recommended_does_not_disable(monkeypatch):
    """A prompt where NO provider recommends any brand is flagged informational (a HINT) —
    the prompt is never auto-disabled."""
    from app.services.answer_tracking import aggregation
    from tests.test_answer_tracking_analysis import _analysis, _result, _run, _set
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        r = _result(db, run, prompts[0].id, org)
        # 0 mentions, NO competitors, NO recommendations => informational hint
        _analysis(db, run, r.id, org, mentioned=False, competitors=[], recommended=[])
        s = aggregation.run_summary(db, run)
        row = next(p for p in s["per_prompt"] if p["prompt_id"] == prompts[0].id)
        assert row["irrelevant_hint"] is True
        assert db.get(TrackedPrompt, prompts[0].id).is_active is True   # never auto-disabled
    finally:
        db.close()


# ------------------------- migration backfill (no data loss) -------------------------
def test_migration_backfill_moves_brand_and_monitor_id():
    """Exercises the REAL migration backfill SQL: brand fields move onto the correct monitor,
    and monitor_id flows sets -> runs -> results/analysis. Nothing is lost or mis-attributed."""
    from app.db.models import RUN_COMPLETED, EXTRACTION_PENDING
    mig = _load_migration()
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        # two sites; each set carries the OLD brand fields, monitor has none yet (a fresh
        # monitor's brand_* columns are genuine SQL NULL — exactly like a just-migrated prod DB)
        m1 = _monitor(db, org, name="Site One", url="one.com")
        m2 = _monitor(db, org, name="Site Two", url="two.com")
        ps1 = PromptSet(organization_id=org, monitor_id=m1.id, name="legacy1",
                        brand_name="One Brand", brand_domain="one.com",
                        brand_aliases=["One"], competitor_domains=["rivalone.com"])
        ps2 = PromptSet(organization_id=org, monitor_id=m2.id, name="legacy2",
                        brand_name="Two Brand")
        db.add_all([ps1, ps2]); db.commit(); db.refresh(ps1)
        tp = TrackedPrompt(prompt_set_id=ps1.id, organization_id=org, text="best one tools")
        run = PromptRun(organization_id=org, prompt_set_id=ps1.id, status=RUN_COMPLETED,
                        extraction_status=EXTRACTION_PENDING)   # monitor_id NULL (pre-migration)
        db.add_all([tp, run]); db.commit(); db.refresh(run)
        res = PromptResult(run_id=run.id, prompt_id=tp.id, organization_id=org,
                           provider="anthropic", model="m", run_index=0)
        db.add(res); db.commit(); db.refresh(res)
        ana = PromptResultAnalysis(result_id=res.id, run_id=run.id, organization_id=org,
                                   extraction_model="e")
        db.add(ana); db.commit()
        m1_id, m2_id, run_id, res_id, ana_id = m1.id, m2.id, run.id, res.id, ana.id
        assert db.get(PromptRun, run_id).monitor_id is None   # pre-migration: not yet attributed

        mig.backfill(db.connection())
        db.expire_all()

        # brand fields landed on the correct monitors, not swapped
        assert db.get(Monitor, m1_id).brand_name == "One Brand"
        assert db.get(Monitor, m1_id).brand_domain == "one.com"
        assert db.get(Monitor, m1_id).brand_aliases == ["One"]
        assert db.get(Monitor, m1_id).competitor_domains == ["rivalone.com"]
        assert db.get(Monitor, m2_id).brand_name == "Two Brand"
        # monitor_id flowed to run, result, and analysis
        assert db.get(PromptRun, run_id).monitor_id == m1_id
        assert db.get(PromptResult, res_id).monitor_id == m1_id
        assert db.get(PromptResultAnalysis, ana_id).monitor_id == m1_id
        # the prompt still exists (nothing deleted)
        assert db.query(TrackedPrompt).filter(TrackedPrompt.prompt_set_id == ps1.id).count() == 1
    finally:
        db.close()


def test_migration_reports_monitorless_sets_and_leaves_them():
    """A set with no monitor is reported (returned) and NOT deleted or reassigned."""
    mig = _load_migration()
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps = PromptSet(organization_id=org, monitor_id=None, name="orphan")
        db.add(ps); db.commit()
        db.add(TrackedPrompt(prompt_set_id=ps.id, organization_id=org, text="q"))
        db.commit()
        orphan_id = ps.id
        rows = mig.monitorless_sets(db.connection())
        assert any(r[0] == orphan_id for r in rows)       # reported for manual resolution
        assert db.get(PromptSet, orphan_id) is not None   # left untouched, not deleted
    finally:
        db.close()
