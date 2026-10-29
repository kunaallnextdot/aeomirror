"""Website Intelligence Command Center (app/services/command_center.py and
GET /business-impact/command-center): every indicator traces to a real source, no
finding is produced without evidence, missing data is never shown as zero, simulator
runs never count as AI visibility, GA4 only appears on an explicitly-linked website,
and nothing crosses websites or organizations."""
from datetime import datetime, timedelta

from app.config import settings
from app.core import token_crypto
from app.db.models import (
    GA4_STATUS_ACTIVE, AnalyticsConversion, AnalyticsSession, Ga4TrafficSnapshot,
    GoogleAnalyticsConnection, Monitor, PromptResult, PromptResultAnalysis, PromptRun,
    PromptSet, Scan, TrackedPrompt,
)
from app.db.session import SessionLocal
from app.services import command_center as cc
from tests.authutil import auth_client

FAKE_KEY = "zxhjMoSGw8Hu9dZ0qVgN6W7AUHHho9mz5igY2ZvQ4oE="


def _monitor(org_id, user_id, url="https://acme.example/"):
    db = SessionLocal()
    try:
        m = Monitor(organization_id=org_id, user_id=user_id, url=url,
                    normalized_url=url.replace("https://", "").rstrip("/"),
                    frequency="manual", status="active")
        db.add(m); db.commit(); db.refresh(m)
        return m.id
    finally:
        db.close()


def _scan(db, org_id, monitor_id, *, created_at=None, sections=None):
    sections = sections if sections is not None else [
        {"id": "schema", "label": "Structured data", "score": 10, "status": "fail", "weight": 15,
         "issues": ["No JSON-LD found"], "recommendations": ["Add Organization schema"]},
        {"id": "robots", "label": "Robots", "score": 100, "status": "pass", "weight": 10, "issues": []},
    ]
    s = Scan(url="https://acme.example/", normalized_url="acme.example", ars=40, rubric_version="v1",
             organization_id=org_id, status="completed", created_at=created_at or datetime.utcnow(),
             result={"overall_score": 41, "sections": sections})
    db.add(s); db.flush()
    db.get(Monitor, monitor_id).latest_scan_id = s.id
    return s


def _answer_run(db, org_id, monitor_id, *, run_mode="provider_tracking", mentions=(True, False, False)):
    ps = PromptSet(organization_id=org_id, monitor_id=monitor_id, name="site")
    db.add(ps); db.flush()
    prompts = [TrackedPrompt(prompt_set_id=ps.id, organization_id=org_id, text=f"best tool {i}")
               for i in range(len(mentions))]
    db.add_all(prompts); db.flush()
    run = PromptRun(organization_id=org_id, prompt_set_id=ps.id, monitor_id=monitor_id,
                    status="completed", extraction_status="complete", run_mode=run_mode,
                    completed_at=datetime.utcnow())
    db.add(run); db.flush()
    for p, hit in zip(prompts, mentions):
        r = PromptResult(run_id=run.id, prompt_id=p.id, organization_id=org_id, monitor_id=monitor_id,
                         provider="openai", model="gpt-x", run_index=0)
        db.add(r); db.flush()
        db.add(PromptResultAnalysis(result_id=r.id, run_id=run.id, organization_id=org_id,
                                    monitor_id=monitor_id, brand_mentioned=hit, extraction_model="m"))
    return run


def _sessions(db, org_id, monitor_id, n, *, channel="direct", when=None, prefix="s"):
    when = when or datetime.utcnow()
    for i in range(n):
        db.add(AnalyticsSession(organization_id=org_id, monitor_id=monitor_id, session_id=f"{prefix}-{i}",
                                landing_url="https://acme.example/", channel=channel,
                                started_at=when, last_seen_at=when, page_view_count=1))


def _build(org_id, monitor_id, *, days=28, is_pro=True):
    db = SessionLocal()
    try:
        end = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return cc.build(db, org_id=org_id, monitor=db.get(Monitor, monitor_id),
                        start=end - timedelta(days=days), end=end, is_pro=is_pro)
    finally:
        db.close()


# ------------------------------- no data: nothing is invented -------------------------------

def test_a_website_with_no_data_gets_honest_not_configured_states_and_zero_findings():
    _, body = auth_client()
    mid = _monitor(body["organization"]["id"], body["user"]["id"])
    out = _build(body["organization"]["id"], mid)
    ind = out["indicators"]
    for key in ("technical", "ai_visibility", "traffic", "ga4", "action_center"):
        assert ind[key]["status"] == "not_configured", key
        assert ind[key].get("reason"), key            # always says why
    assert ind["conversions"]["status"] == "not_configured"
    assert out["findings"] == []                      # no evidence -> no insight
    assert ind["traffic"].get("current") is None      # never a fabricated 0


# ------------------------------- technical audit -------------------------------

def test_technical_indicator_and_findings_come_from_the_latest_real_audit():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        scan = _scan(db, org, mid); db.commit(); scan_id = scan.id
    finally:
        db.close()
    out = _build(org, mid)
    tech = out["indicators"]["technical"]
    assert tech["status"] == "available" and tech["score"] == 41 and tech["scan_id"] == scan_id
    assert tech["signals"] == {"pass": 1, "warn": 0, "fail": 1}
    f = next(f for f in out["findings"] if f["category"] == "technical_seo")
    assert f["link"] == f"/app/scans/{scan_id}"
    assert any(s["type"] == "observed" and "No JSON-LD found" in s["text"] for s in f["statements"])


def test_an_old_audit_is_marked_stale_not_presented_as_current():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        _scan(db, org, mid, created_at=datetime.utcnow() - timedelta(days=cc.SCAN_STALE_DAYS + 5)); db.commit()
    finally:
        db.close()
    assert _build(org, mid)["indicators"]["technical"]["status"] == "stale"


# ------------------------------- AI answer visibility -------------------------------

def test_ai_visibility_uses_real_provider_runs_and_reports_query_gaps_with_sample_size():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        _answer_run(db, org, mid, mentions=(True, False, False)); db.commit()
    finally:
        db.close()
    out = _build(org, mid)
    vis = out["indicators"]["ai_visibility"]
    assert vis["status"] == "available"
    assert vis["samples_analyzed"] == 3 and vis["query_count"] == 3
    assert vis["providers"] == ["openai"]
    f = next(f for f in out["findings"] if f["category"] == "ai_visibility")
    assert f["title"] == "Not mentioned in 2 of 3 tracked AI queries"
    assert f["sample"] == {"size": 3, "unit": "answers"}
    assert f["confidence"] == "low"                    # 3 answers is a small sample


def test_simulator_runs_never_count_as_ai_answer_visibility():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        _answer_run(db, org, mid, run_mode="simulator"); db.commit()
    finally:
        db.close()
    assert _build(org, mid)["indicators"]["ai_visibility"]["status"] == "not_configured"


def test_ai_referral_traffic_and_ai_answer_visibility_stay_separate():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        _sessions(db, org, mid, 5, channel="chatgpt"); db.commit()
    finally:
        db.close()
    out = _build(org, mid)
    # visits from ChatGPT exist, but no Answer Tracking run -> visibility is NOT inferred
    assert out["indicators"]["traffic"]["current"]["ai_sessions"] == 5
    assert out["indicators"]["ai_visibility"]["status"] == "not_configured"
    assert not any(f["category"] == "ai_visibility" for f in out["findings"])
    assert any(f["category"] == "ai_referral" for f in out["findings"])


# ------------------------------- traffic & conversions -------------------------------

def test_previous_period_is_not_compared_when_tracking_did_not_cover_it():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        _sessions(db, org, mid, 30); db.commit()   # tracking started today
    finally:
        db.close()
    traffic = _build(org, mid)["indicators"]["traffic"]
    assert traffic["previous_comparable"] is False
    assert traffic["previous"] is None and traffic["change_pct"] is None   # no made-up change


def test_session_trend_finding_requires_enough_sessions_in_both_periods():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        _sessions(db, org, mid, 40, when=now - timedelta(days=40), prefix="prev")
        _sessions(db, org, mid, 20, when=now - timedelta(days=2), prefix="cur")
        _sessions(db, org, mid, 1, when=now - timedelta(days=80), prefix="first")  # tracking long-running
        db.commit()
    finally:
        db.close()
    out = _build(org, mid)
    f = next(f for f in out["findings"] if f["category"] == "acquisition_engagement")
    assert f["title"] == "Website sessions down 50.0% vs the previous period"
    assert any(s["type"] == "calculated" and "(20 − 40) ÷ 40 = -50.0%" in s["text"] for s in f["statements"])


def test_conversion_measurement_finding_appears_only_when_nothing_is_confirmed():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        _sessions(db, org, mid, 3); db.commit()
    finally:
        db.close()
    out = _build(org, mid)
    assert any(f["category"] == "conversion_measurement" for f in out["findings"])
    assert out["indicators"]["conversions"]["first_party"] == "not_configured"
    db = SessionLocal()
    try:
        db.add(AnalyticsConversion(organization_id=org, monitor_id=mid, event_type="purchase",
                                   external_id="o-1", verified=True)); db.commit()
    finally:
        db.close()
    out = _build(org, mid)
    assert not any(f["category"] == "conversion_measurement" for f in out["findings"])
    assert out["indicators"]["conversions"]["status"] == "available"


def test_free_plan_never_receives_conversion_figures_as_zeros():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        _sessions(db, org, mid, 3)
        db.add(AnalyticsConversion(organization_id=org, monitor_id=mid, event_type="purchase",
                                   external_id="o-2", verified=True)); db.commit()
    finally:
        db.close()
    conv = _build(org, mid, is_pro=False)["indicators"]["conversions"]
    assert "confirmed_ai_conversions" not in conv and "ai_conversion_rate_pct" not in conv


# ------------------------------- GA4 website mapping -------------------------------

def _ga4_connection(org_id, user_id, *, linked_monitor_id=None):
    db = SessionLocal()
    try:
        c = GoogleAnalyticsConnection(organization_id=org_id, connected_by_user_id=user_id,
                                      encrypted_refresh_token=token_crypto.encrypt_token("rt"),
                                      status=GA4_STATUS_ACTIVE, ga4_property_id="123",
                                      ga4_property_name="Acme GA4", last_synced_at=datetime.utcnow(),
                                      linked_monitor_id=linked_monitor_id, key_events_configured=False)
        db.add(c); db.commit(); db.refresh(c)
        today = datetime.utcnow().strftime("%Y%m%d")
        db.add(Ga4TrafficSnapshot(organization_id=org_id, connection_id=c.id, date=today,
                                  channel_grouping="Referral", source="chatgpt.com", medium="referral",
                                  ai_source_label="chatgpt", sessions=7, key_events=0))
        db.commit()
        return c.id
    finally:
        db.close()


def test_ga4_is_never_shown_on_a_website_it_is_not_explicitly_linked_to(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)
    _, body = auth_client()
    org, user = body["organization"]["id"], body["user"]["id"]
    site_a = _monitor(org, user, "https://a.example/")
    site_b = _monitor(org, user, "https://b.example/")
    _ga4_connection(org, user)
    ga4 = _build(org, site_a)["indicators"]["ga4"]
    assert ga4["status"] == "not_configured" and ga4["link_required"] is True
    assert "sessions" not in ga4

    db = SessionLocal()
    try:
        db.query(GoogleAnalyticsConnection).filter_by(organization_id=org).update({"linked_monitor_id": site_a})
        db.commit()
    finally:
        db.close()
    assert _build(org, site_a)["indicators"]["ga4"]["sessions"] == 7
    assert _build(org, site_a)["indicators"]["ga4"]["key_events"] is None    # not configured != 0
    other = _build(org, site_b)["indicators"]["ga4"]
    assert other["status"] == "unavailable" and "sessions" not in other     # never substituted


def test_link_website_rejects_another_organizations_website(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)
    monkeypatch.setattr(settings, "google_oauth_client_id", "cid")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "cs")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "https://api.example.com/cb")
    client_a, body_a = auth_client()
    _, body_b = auth_client()
    _ga4_connection(body_a["organization"]["id"], body_a["user"]["id"])
    foreign = _monitor(body_b["organization"]["id"], body_b["user"]["id"])
    own = _monitor(body_a["organization"]["id"], body_a["user"]["id"])
    assert client_a.post("/integrations/google/link-website", json={"monitor_id": foreign}).status_code == 404
    r = client_a.post("/integrations/google/link-website", json={"monitor_id": own})
    assert r.status_code == 200 and r.json()["linked_monitor_id"] == own
    assert client_a.get("/integrations/google/status").json()["linked_monitor_id"] == own


# ------------------------------- API isolation + evidence validation -------------------------------

def test_command_center_requires_an_owned_website():
    client_a, body_a = auth_client()
    _, body_b = auth_client()
    foreign = _monitor(body_b["organization"]["id"], body_b["user"]["id"])
    assert client_a.get("/business-impact/command-center").status_code == 422      # never "all sites"
    assert client_a.get("/business-impact/command-center", params={"monitor_id": foreign}).status_code == 404
    own = _monitor(body_a["organization"]["id"], body_a["user"]["id"])
    r = client_a.get("/business-impact/command-center", params={"monitor_id": own})
    assert r.status_code == 200 and r.json()["website"]["id"] == own


def test_one_websites_scan_and_answers_never_appear_on_another_website():
    _, body = auth_client()
    org, user = body["organization"]["id"], body["user"]["id"]
    site_a = _monitor(org, user, "https://a.example/")
    site_b = _monitor(org, user, "https://b.example/")
    db = SessionLocal()
    try:
        _scan(db, org, site_a); _answer_run(db, org, site_a); db.commit()
    finally:
        db.close()
    b = _build(org, site_b)
    assert b["indicators"]["technical"]["status"] == "not_configured"
    assert b["indicators"]["ai_visibility"]["status"] == "not_configured"
    assert all(f["website_id"] == site_b for f in b["findings"])


def test_a_finding_without_evidence_or_an_observed_statement_is_rejected():
    good = {"evidence": [{"label": "x", "value": "1", "source": "first_party"}],
            "statements": [{"type": "observed", "text": "1 session."}]}
    assert cc.valid_finding(good)
    assert not cc.valid_finding({**good, "evidence": []})
    assert not cc.valid_finding({**good, "evidence": [{"label": "x", "value": "1", "source": None}]})
    assert not cc.valid_finding({**good, "statements": [{"type": "recommendation", "text": "Do it."}]})
    assert not cc.valid_finding({**good, "statements": [{"type": "fact?", "text": "1"}]})


def test_every_produced_finding_passes_evidence_validation():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        _scan(db, org, mid); _answer_run(db, org, mid); _sessions(db, org, mid, 25, channel="perplexity")
        db.commit()
    finally:
        db.close()
    findings = _build(org, mid)["findings"]
    assert findings and all(cc.valid_finding(f) for f in findings)


# ------------------------------- reproducible calculations + grounded claims -------------------------------

def _calc_finding(**over):
    calc = cc.calculation("pct_change", current=20, previous=40)
    f = {"title": "Website sessions down 50.0% vs the previous period",
         "evidence": [{"label": "Sessions (current / previous)", "value": "20 / 40", "source": "first_party"}],
         "statements": [{"type": "observed", "text": "20 sessions this period; 40 in the previous."},
                        {"type": "calculated", "text": "Change = (20 − 40) ÷ 40 = -50.0%."}],
         "calculation": calc}
    f.update(over)
    return f


def test_calculations_are_reproduced_from_their_declared_inputs():
    assert cc.validation_errors(_calc_finding()) == []
    wrong = dict(cc.calculation("pct_change", current=20, previous=40), result=-40.0)
    assert "calculation does not reproduce" in cc.validation_errors(_calc_finding(calculation=wrong))
    assert cc.validation_errors(_calc_finding(calculation=None))          # calculated text, no formula
    assert cc.validation_errors(_calc_finding(calculation={**wrong, "formula": "vibes"}))


def test_a_zero_or_missing_denominator_yields_no_number_at_all():
    assert cc.calculation("pct_change", current=5, previous=0) is None
    assert cc.calculation("share_pct", part=3, whole=0) is None
    assert cc.calculation("share_pct", part=None, whole=10) is None
    assert cc.calculation("share_pct", part=3, whole=12)["result"] == 25.0


def test_a_number_not_present_in_the_evidence_suppresses_the_finding():
    f = _calc_finding(title="Website sessions down 73% vs the previous period")
    assert any("ungrounded" in e for e in cc.validation_errors(f))
    f = _calc_finding(statements=_calc_finding()["statements"]
                      + [{"type": "observed", "text": "About 1,200 visitors were lost."}])
    assert any("ungrounded" in e for e in cc.validation_errors(f))


def test_observed_and_calculated_statements_may_not_claim_a_cause():
    f = _calc_finding(statements=_calc_finding()["statements"]
                      + [{"type": "observed", "text": "The drop was caused by the redesign."}])
    assert "causal claim in an observed/calculated statement" in cc.validation_errors(f)
    # A hypothesis may raise a possible cause — it is labeled as unproven.
    f = _calc_finding(statements=_calc_finding()["statements"]
                      + [{"type": "hypothesis", "text": "The redesign may have caused this."}])
    assert cc.validation_errors(f) == []


def test_unavailable_is_a_valid_statement_type():
    f = _calc_finding(statements=_calc_finding()["statements"]
                      + [{"type": "unavailable", "text": "Conversions aren't measured, so impact is unknown."}])
    assert cc.validation_errors(f) == []


def test_generated_narratives_cannot_introduce_numbers_or_causes():
    f = _calc_finding()
    assert cc.validate_narrative("Sessions fell from 40 to 20, a 50.0% decline.", f) == []
    assert cc.validate_narrative("Sessions fell 50%, costing about $4,000 in revenue.", f)
    assert cc.validate_narrative("Sessions fell 50% due to a Google update.", f)


def test_produced_findings_carry_website_timestamps_verification_and_calculations():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        _sessions(db, org, mid, 30, channel="perplexity"); db.commit()
    finally:
        db.close()
    out = _build(org, mid)
    ai = next(f for f in out["findings"] if f["category"] == "ai_referral")
    assert ai["website_id"] == mid and ai["generated_at"] and ai["verification"]["status"] == "validated"
    assert ai["calculation"]["formula"] == "share_pct"
    assert ai["calculation"]["inputs"] == {"part": 30, "whole": 30} and ai["calculation"]["result"] == 100.0
    for ind in out["indicators"].values():
        assert ind.get("provenance", {}).get("kind") in ("measured", "calculated")


def test_a_trend_with_a_zero_previous_period_explains_why_there_is_no_percentage():
    _, body = auth_client()
    org = body["organization"]["id"]
    mid = _monitor(org, body["user"]["id"])
    db = SessionLocal()
    try:
        # Tracking began long before the previous period, but it recorded nothing then.
        _sessions(db, org, mid, 1, when=datetime.utcnow() - timedelta(days=90), prefix="old")
        _sessions(db, org, mid, 25); db.commit()
    finally:
        db.close()
    tr = _build(org, mid, days=7)["indicators"]["traffic"]
    assert tr["previous_comparable"] is True and tr["previous"]["total_sessions"] == 0
    assert tr["change_pct"]["total_sessions"] is None
    assert "can't be calculated" in tr["change_note"]


# ------------------------------- GA4 sync state, coverage and attribution -------------------------------

def _linked_ga4(org, user, site, **conn_over):
    cid = _ga4_connection(org, user, linked_monitor_id=site)
    if conn_over:
        db = SessionLocal()
        try:
            db.query(GoogleAnalyticsConnection).filter_by(id=cid).update(conn_over); db.commit()
        finally:
            db.close()
    return cid


def _snap(org, cid, days_ago, *, label="chatgpt", source="chatgpt.com", medium="referral", sessions=5):
    db = SessionLocal()
    try:
        db.add(Ga4TrafficSnapshot(organization_id=org, connection_id=cid,
                                  date=(datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y%m%d"),
                                  channel_grouping="x", source=source, medium=medium,
                                  ai_source_label=label, sessions=sessions, key_events=0))
        db.commit()
    finally:
        db.close()


def test_ga4_google_organic_and_other_referrers_never_count_as_ai_sessions(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)
    _, body = auth_client()
    org, user = body["organization"]["id"], body["user"]["id"]
    site = _monitor(org, user)
    cid = _linked_ga4(org, user, site)
    _snap(org, cid, 40)    # history covers the whole 28-day range
    _snap(org, cid, 1, label="unattributable", source="google", medium="organic", sessions=50)
    _snap(org, cid, 1, label="other_identifiable", source="news.example", sessions=20)
    g = _build(org, site)["indicators"]["ga4"]
    assert g["status"] == "available"
    assert g["sessions"] == 77 and g["ai_assistant_sessions"] == 7


def test_ga4_range_starting_before_synced_history_is_partial_not_complete(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)
    _, body = auth_client()
    org, user = body["organization"]["id"], body["user"]["id"]
    site = _monitor(org, user)
    _linked_ga4(org, user, site)                       # only today's row exists
    g = _build(org, site, days=28)["indicators"]["ga4"]
    assert g["status"] == "partial" and "earlier days" in g["reason"]


def test_ga4_failed_sync_and_disconnect_are_reported_not_hidden(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)
    _, body = auth_client()
    org, user = body["organization"]["id"], body["user"]["id"]
    site = _monitor(org, user)
    cid = _linked_ga4(org, user, site, last_sync_status="error", last_sync_error_code="quota_exceeded")
    g = _build(org, site, days=1)["indicators"]["ga4"]
    assert g["status"] == "error" and g["sync_error_code"] == "quota_exceeded"
    assert "last successful sync" in g["reason"]

    db = SessionLocal()
    try:
        db.query(GoogleAnalyticsConnection).filter_by(id=cid).update({"last_synced_at": None}); db.commit()
    finally:
        db.close()
    g = _build(org, site, days=1)["indicators"]["ga4"]
    assert g["status"] == "error" and "sessions" not in g

    db = SessionLocal()
    try:
        db.query(GoogleAnalyticsConnection).filter_by(id=cid).update({"status": "revoked"}); db.commit()
    finally:
        db.close()
    g = _build(org, site, days=1)["indicators"]["ga4"]
    assert g["status"] == "not_configured" and "sessions" not in g


def test_ga4_stale_sync_is_marked_stale(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)
    _, body = auth_client()
    org, user = body["organization"]["id"], body["user"]["id"]
    site = _monitor(org, user)
    _linked_ga4(org, user, site, last_synced_at=datetime.utcnow() - timedelta(hours=cc.GA4_STALE_HOURS + 1))
    assert _build(org, site, days=1)["indicators"]["ga4"]["status"] == "stale"


def test_ga4_synced_without_any_rows_is_insufficient_not_zero(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)
    _, body = auth_client()
    org, user = body["organization"]["id"], body["user"]["id"]
    site = _monitor(org, user)
    cid = _linked_ga4(org, user, site)
    db = SessionLocal()
    try:
        db.query(Ga4TrafficSnapshot).filter_by(connection_id=cid).delete(); db.commit()
    finally:
        db.close()
    g = _build(org, site)["indicators"]["ga4"]
    assert g["status"] == "insufficient_data" and "sessions" not in g
