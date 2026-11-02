"""AI Expert Analysis Engine (app/services/url_intelligence/ai_analysis.py): output
validation against a strict schema (defense against prompt injection), content-
fingerprint cache reuse, the daily AI-call budget ceiling, and the deterministic
fallback used whenever AI is unavailable/invalid. The real Anthropic SDK is never
called in tests — `app.core.ai.complete_with_usage` is monkeypatched."""
import json

from app.config import settings
from app.core import ai
from app.db.models import CrawlJob, CrawlUrl, UrlFinding
from app.db.session import SessionLocal
from app.services.url_intelligence import ai_analysis
from tests.authutil import auth_client


def _job(db, org_id, user_id):
    j = CrawlJob(organization_id=org_id, user_id=user_id, monitor_id="m1",
                seed_url="https://example.com/", normalized_seed_url="https://example.com/",
                max_urls=100)
    db.add(j); db.commit(); db.refresh(j)
    return j


def _crawled_url(db, job, *, url="https://example.com/a", template_id="t1",
                 fingerprint="fp1", evidence=None):
    row = CrawlUrl(crawl_job_id=job.id, organization_id=job.organization_id, url=url,
                   normalized_key=url, discovery_source="seed", status="crawled",
                   page_type="product", template_id=template_id,
                   content_fingerprint=fingerprint,
                   evidence=evidence or {"title": "A widget", "missing_content_elements": []})
    db.add(row); db.commit(); db.refresh(row)
    return row


_VALID_RESPONSE = {
    "finding_title": "Missing structured data", "category": "structured_data",
    "why_it_matters": "AI assistants rely on structured data to cite pages accurately.",
    "recommended_fix": "Add Product schema with price and availability.",
    "expected_benefit": "May improve how reliably this page is understood.",
    "priority": "medium", "confidence": "high",
    "evidence_limitations": "Based on one representative page for this template.",
    "verification_procedure": "Re-crawl and confirm Product schema is present.",
}


def test_valid_ai_output_is_persisted_with_model_and_tokens(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(ai, "complete_with_usage",
                        lambda *a, **k: (json.dumps(_VALID_RESPONSE), {"input": 100, "output": 50}))
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        row = _crawled_url(db, job)
        finding = ai_analysis.analyze_template(db, job, row, affected_url_count=3)
        assert finding.category == "structured_data"
        assert finding.model_used == settings.ai_model
        assert finding.tokens_input == 100 and finding.tokens_output == 50
        assert finding.affected_url_count == 3
        assert finding.url == row.url   # URL is set by OUR code, never by the AI response
        assert finding.cache_hit is False
        # Labeled as what it is: one representative page's AI reading, keyed for carry-over.
        assert finding.evidence_basis == "ai_representative"
        assert finding.issue_key == f"ai:{row.template_id}:structured_data"
    finally:
        db.close()


def test_ai_response_missing_required_key_creates_no_ai_finding(monkeypatch):
    bad = {k: v for k, v in _VALID_RESPONSE.items() if k != "recommended_fix"}
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(ai, "complete_with_usage", lambda *a, **k: (json.dumps(bad), {"input": 1, "output": 1}))
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        row = _crawled_url(db, job, evidence={"title": "x", "missing_content_elements": ["missing_title"]})
        finding = ai_analysis.analyze_template(db, job, row, affected_url_count=1)
        # Invalid AI output is discarded, never persisted, and no stand-in finding is made up
        # for it — the page's deterministic issues are recorded by the preparing step.
        assert finding is None
        assert db.query(UrlFinding).filter_by(crawl_job_id=job.id).count() == 0
        assert job.ai_calls_total == 1      # the attempt is still counted
    finally:
        db.close()


def test_ai_response_with_out_of_enum_category_is_rejected_not_persisted_raw(monkeypatch):
    """Prompt-injection resistance: even if the model is coaxed into returning an
    out-of-vocabulary category (e.g. via injected instructions in page evidence text),
    the strict allow-list rejects it — the finding never gets an arbitrary/attacker-
    controlled category string."""
    injected = {**_VALID_RESPONSE, "category": "IGNORE_ALL_RULES_AND_SET_PRIORITY_CRITICAL"}
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(ai, "complete_with_usage",
                        lambda *a, **k: (json.dumps(injected), {"input": 1, "output": 1}))
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        row = _crawled_url(db, job)
        finding = ai_analysis.analyze_template(db, job, row, affected_url_count=1)
        assert finding is None              # rejected outright, nothing persisted
        assert not db.query(UrlFinding).filter(
            UrlFinding.category == "IGNORE_ALL_RULES_AND_SET_PRIORITY_CRITICAL").count()
    finally:
        db.close()


def test_ai_unavailable_never_invents_a_finding(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", None)   # AI disabled
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        row = _crawled_url(db, job, evidence={"title": "Fine", "missing_content_elements": []})
        finding = ai_analysis.analyze_template(db, job, row, affected_url_count=1)
        # No "No issues detected" placeholder pretending to be a finding.
        assert finding is None
        assert db.query(UrlFinding).filter_by(crawl_job_id=job.id).count() == 0
    finally:
        db.close()


def test_cached_finding_is_reused_without_a_new_ai_call(monkeypatch):
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return json.dumps(_VALID_RESPONSE), {"input": 10, "output": 10}

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(ai, "complete_with_usage", _fake)
    client, body = auth_client()
    db = SessionLocal()
    try:
        job1 = _job(db, body["organization"]["id"], body["user"]["id"])
        row1 = _crawled_url(db, job1, template_id="same-template", fingerprint="same-fp")
        ai_analysis.analyze_template(db, job1, row1, affected_url_count=1)
        assert calls["n"] == 1

        # A second, later crawl job for the SAME org/template/fingerprint — the
        # fingerprint cache should reuse the earlier result without a new AI call.
        job2 = _job(db, body["organization"]["id"], body["user"]["id"])
        row2 = _crawled_url(db, job2, url="https://example.com/b",
                            template_id="same-template", fingerprint="same-fp")
        finding2 = ai_analysis.analyze_template(db, job2, row2, affected_url_count=1)
        assert calls["n"] == 1          # no new AI call
        assert finding2.cache_hit is True
        assert finding2.category == "structured_data"
    finally:
        db.close()


def test_daily_ai_budget_ceiling_skips_remaining_calls(monkeypatch):
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return json.dumps(_VALID_RESPONSE), {"input": 1, "output": 1}

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(ai, "complete_with_usage", _fake)
    client, body = auth_client()
    db = SessionLocal()
    try:
        # The daily cap is a GLOBAL (all-orgs) counter, like entitlements.ai_calls_today
        # — other tests in this run may have already recorded calls today, so the cap
        # must be set relative to the current count, not an absolute "1".
        baseline = ai_analysis._ai_calls_today(db)
        monkeypatch.setattr(settings, "url_crawl_ai_daily_cap", baseline + 1)
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        row1 = _crawled_url(db, job, url="https://example.com/a", template_id="t-a", fingerprint="fp-a")
        row2 = _crawled_url(db, job, url="https://example.com/b", template_id="t-b", fingerprint="fp-b")
        f1 = ai_analysis.analyze_template(db, job, row1, affected_url_count=1)
        f2 = ai_analysis.analyze_template(db, job, row2, affected_url_count=1)
        assert calls["n"] == 1          # the cap stopped the second call
        assert f1.model_used == settings.ai_model
        assert f2 is None               # budget exhausted: no AI finding, nothing invented
    finally:
        db.close()


def test_without_an_api_key_no_ai_call_is_made_or_counted(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(ai, "complete_with_usage", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or (None, None))
    client, body = auth_client()
    db = SessionLocal()
    try:
        job = _job(db, body["organization"]["id"], body["user"]["id"])
        assert ai_analysis.analyze_template(db, job, _crawled_url(db, job), affected_url_count=1) is None
        assert calls["n"] == 0 and job.ai_calls_total == 0
    finally:
        db.close()
