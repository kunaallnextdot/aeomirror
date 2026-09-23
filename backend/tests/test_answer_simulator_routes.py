"""AEO Answer Simulator — HTTP-level routing, gating, org isolation, and Question
Bank / report cross-link integration. Unit-level retrieval/scoring/provider tests
live in test_answer_simulator.py; this file exercises the real endpoints against a
real SQLite-backed org/monitor/scan, the same convention test_gating_cleanup.py and
test_answer_tracking_monitor.py already use.
"""
from __future__ import annotations

import app.billing.plans as plans_mod
from app.config import settings
from app.db.models import Monitor, PromptRun, Scan
from app.db.session import SessionLocal
from tests.authutil import auth_client


def _scan_with_faq(db, org, *, url="https://acme.example/", host="acme.example"):
    result = {
        "sections": [
            {"id": "metadata", "evidence": {"title": "Acme Attendance Software",
                                            "description": "AI attendance for colleges"}},
            {"id": "content", "evidence": {"h1_text": "Acme AI Attendance",
                                           "heading_questions": ["What is AI attendance?"]}},
            {"id": "schema", "evidence": {
                "faq_questions": [{"question": "What pricing plans are available?",
                                   "answer": "We offer Basic and Pro plans."}],
                "entity_evidence": {"name": "Acme Inc", "url": url, "logo": None, "same_as": []},
            }},
        ],
        "phase4": {"available": True, "questions": [
            {"text": "What pricing plans are available?", "source": "faq", "category": "commercial",
             "affected_url": url},
            {"text": "What is AI attendance?", "source": "heading", "category": "informational",
             "affected_url": url},
        ]},
        "overall_score": 80,
    }
    s = Scan(url=url, normalized_url=host, ars=0, rubric_version="t", status="completed",
             organization_id=org, result=result)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _scan_with_heading_question(db, org, *, question_text, url="https://acme.example/",
                                host="acme.example"):
    """Same real evidence shape as _scan_with_faq (heading_questions in the content
    signal's evidence — phase4.build_question_mining reads it from there, not from a
    directly-set result['phase4'] blob), but with caller-chosen question text so two
    scans of the "same" site can carry genuinely different questions."""
    result = {
        "sections": [
            {"id": "content", "evidence": {"h1_text": "Acme", "heading_questions": [question_text]}},
        ],
        "overall_score": 80,
    }
    s = Scan(url=url, normalized_url=host, ars=0, rubric_version="t", status="completed",
             organization_id=org, result=result)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _monitor_linked_to_scan(db, org, scan, *, name="Acme"):
    m = Monitor(organization_id=org, url=scan.url, normalized_url=scan.normalized_url,
               status="active", frequency="weekly", name=name, latest_scan_id=scan.id,
               brand_name=name)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _enforce_billing(monkeypatch, *, batch_limit=5, llm_step=False):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "answer_simulator_batch_limit", batch_limit)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "answer_simulator_llm_step", llm_step)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


# ------------------------------- questions (Question Bank reuse) -------------------------------
def test_questions_endpoint_merges_bank_and_tracked_prompts():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        scan = _scan_with_faq(db, org)
        monitor = _monitor_linked_to_scan(db, org, scan)
        mid = monitor.id
    finally:
        db.close()

    r = client.get(f"/monitors/{mid}/answer-simulator/questions")
    assert r.status_code == 200
    payload = r.json()
    bank_texts = {q["question"] for q in payload["bank_questions"]}
    assert "What pricing plans are available?" in bank_texts
    assert "What is AI attendance?" in bank_texts
    assert all(q["already_tracked"] is False for q in payload["bank_questions"])
    assert payload["tracked_prompts"] == []


def test_questions_endpoint_flags_already_tracked_bank_entries():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        scan = _scan_with_faq(db, org)
        monitor = _monitor_linked_to_scan(db, org, scan)
        mid = monitor.id
    finally:
        db.close()

    client.post(f"/monitors/{mid}/answer-simulator/run",
               json={"question_bank_keys": ["What pricing plans are available?"]})
    payload = client.get(f"/monitors/{mid}/answer-simulator/questions").json()
    flagged = next(q for q in payload["bank_questions"] if q["question"] == "What pricing plans are available?")
    assert flagged["already_tracked"] is True


def test_questions_endpoint_reflects_the_monitors_current_scan_not_an_older_one():
    """Bug fix regression: the scan-derived question bank must reflect
    monitor.latest_scan_id's OWN questions — never a stale/older scan's, even when
    an older scan for the same site still exists in the org's history. Mirrors the
    ticket's exact scenario: Scan A has "old keyword", Scan B (the CURRENT scan) has
    "new keyword" — suggestions for this monitor must show only the new one."""
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        scan_a = _scan_with_heading_question(db, org, question_text="old keyword question?")
        scan_b = _scan_with_heading_question(db, org, question_text="new keyword question?")
        assert scan_a.id != scan_b.id

        # The monitor's CURRENT scan is B — exactly what _sync_monitor_latest_scan
        # (app/api/routes_scan.py) keeps up to date after any ordinary rescan.
        monitor = _monitor_linked_to_scan(db, org, scan_b)
        mid = monitor.id
    finally:
        db.close()

    payload = client.get(f"/monitors/{mid}/answer-simulator/questions").json()
    bank_texts = {q["question"] for q in payload["bank_questions"]}
    assert "new keyword question?" in bank_texts
    assert "old keyword question?" not in bank_texts


def test_questions_endpoint_empty_when_current_scan_has_no_questions():
    """The current scan genuinely has no extracted questions -> an honest empty
    list, never a fallback to an older scan's questions just to populate the UI."""
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        old_scan = _scan_with_faq(db, org)
        empty_scan = Scan(url="https://acme.example/", normalized_url="acme.example", ars=0,
                         rubric_version="t", status="completed", organization_id=org,
                         result={"sections": [{"id": "content", "evidence": {}}], "overall_score": 80})
        db.add(empty_scan)
        db.commit()
        db.refresh(empty_scan)
        monitor = _monitor_linked_to_scan(db, org, empty_scan)
        mid = monitor.id
        assert old_scan.id != empty_scan.id
    finally:
        db.close()

    payload = client.get(f"/monitors/{mid}/answer-simulator/questions").json()
    assert payload["bank_questions"] == []


def test_questions_endpoint_empty_when_monitor_has_no_scan_yet():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        m = Monitor(organization_id=org, url="https://new.example/", normalized_url="new.example",
                   status="active", frequency="weekly", name="New")
        db.add(m)
        db.commit()
        db.refresh(m)
        mid = m.id
    finally:
        db.close()
    payload = client.get(f"/monitors/{mid}/answer-simulator/questions").json()
    assert payload["bank_questions"] == []


# ------------------------------- run -------------------------------
def test_run_with_custom_questions_deterministic_and_zero_cost():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        scan = _scan_with_faq(db, org)
        monitor = _monitor_linked_to_scan(db, org, scan)
        mid = monitor.id
    finally:
        db.close()

    r = client.post(f"/monitors/{mid}/answer-simulator/run",
                    json={"custom_questions": ["What pricing plans are available?"]})
    assert r.status_code == 200
    payload = r.json()
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["question"] == "What pricing plans are available?"
    assert result["llm_step_used"] is False
    assert result["answerability"] in ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT_EVIDENCE")
    assert isinstance(result["evidence"], list)

    db = SessionLocal()
    try:
        run = db.get(PromptRun, payload["run_id"])
        assert run.run_mode == "simulator"
        assert run.estimated_cost_usd == 0.0
    finally:
        db.close()


def test_duplicate_custom_question_reuses_the_same_tracked_prompt():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        scan = _scan_with_faq(db, org)
        monitor = _monitor_linked_to_scan(db, org, scan)
        mid = monitor.id
    finally:
        db.close()

    client.post(f"/monitors/{mid}/answer-simulator/run",
               json={"custom_questions": ["What pricing plans are available?"]})
    client.post(f"/monitors/{mid}/answer-simulator/run",
               json={"custom_questions": ["what pricing plans are available"]})   # same question, different casing/punct

    payload = client.get(f"/monitors/{mid}/answer-simulator/questions").json()
    assert len(payload["tracked_prompts"]) == 1


def test_run_requires_at_least_one_question():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        mid = _monitor_linked_to_scan(db, org, _scan_with_faq(db, org)).id
    finally:
        db.close()
    r = client.post(f"/monitors/{mid}/answer-simulator/run", json={})
    assert r.status_code == 422


def test_free_plan_batch_limit_truncates_and_reports_locked_count(monkeypatch):
    _enforce_billing(monkeypatch, batch_limit=2)
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        mid = _monitor_linked_to_scan(db, org, _scan_with_faq(db, org)).id
    finally:
        db.close()

    r = client.post(f"/monitors/{mid}/answer-simulator/run", json={
        "custom_questions": ["question one", "question two", "question three", "question four"],
    })
    assert r.status_code == 200
    payload = r.json()
    assert len(payload["results"]) == 2
    assert payload["batch_limit"] == 2
    assert payload["locked_count"] == 2


def test_pro_plan_gets_unlimited_batch(monkeypatch):
    _enforce_billing(monkeypatch, batch_limit=2)
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        mid = _monitor_linked_to_scan(db, org, _scan_with_faq(db, org)).id
    finally:
        db.close()
    _go_pro(client)

    r = client.post(f"/monitors/{mid}/answer-simulator/run", json={
        "custom_questions": ["question one", "question two", "question three"],
    })
    payload = r.json()
    assert len(payload["results"]) == 3
    assert payload["locked_count"] == 0


def test_llm_step_requires_pro_entitlement(monkeypatch):
    _enforce_billing(monkeypatch, llm_step=False)
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        mid = _monitor_linked_to_scan(db, org, _scan_with_faq(db, org)).id
    finally:
        db.close()

    r = client.post(f"/monitors/{mid}/answer-simulator/run", json={
        "custom_questions": ["What pricing plans are available?"], "request_llm_step": True,
    })
    assert r.status_code == 402


def test_llm_step_allowed_on_pro(monkeypatch):
    _enforce_billing(monkeypatch, llm_step=True)
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        mid = _monitor_linked_to_scan(db, org, _scan_with_faq(db, org)).id
    finally:
        db.close()
    _go_pro(client)

    r = client.post(f"/monitors/{mid}/answer-simulator/run", json={
        "custom_questions": ["What pricing plans are available?"], "request_llm_step": True,
    })
    assert r.status_code == 200   # allowed; local/anthropic provider unconfigured -> falls back deterministically


# ------------------------------- results + explain -------------------------------
def test_results_endpoint_returns_persisted_shape():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        mid = _monitor_linked_to_scan(db, org, _scan_with_faq(db, org)).id
    finally:
        db.close()
    run_id = client.post(f"/monitors/{mid}/answer-simulator/run",
                         json={"custom_questions": ["What pricing plans are available?"]}).json()["run_id"]

    r = client.get(f"/monitors/{mid}/answer-simulator/runs/{run_id}/results")
    assert r.status_code == 200
    payload = r.json()
    assert payload["run_id"] == run_id
    assert len(payload["results"]) == 1
    assert payload["results"][0]["question"] == "What pricing plans are available?"


def test_explain_endpoint_requires_pro(monkeypatch):
    _enforce_billing(monkeypatch, llm_step=False)
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        mid = _monitor_linked_to_scan(db, org, _scan_with_faq(db, org)).id
    finally:
        db.close()
    run = client.post(f"/monitors/{mid}/answer-simulator/run",
                      json={"custom_questions": ["unrelated spacecraft propulsion question"]})
    run_id = run.json()["run_id"]
    prompt_id = run.json()["results"][0]["prompt_id"]

    r = client.post(f"/monitors/{mid}/answer-simulator/runs/{run_id}/questions/{prompt_id}/explain")
    assert r.status_code == 402


# ------------------------------- org isolation -------------------------------
def test_org_b_cannot_reach_org_as_monitor_or_run():
    client_a, body_a = auth_client()
    client_b, _ = auth_client()
    org_a = body_a["organization"]["id"]
    db = SessionLocal()
    try:
        mid = _monitor_linked_to_scan(db, org_a, _scan_with_faq(db, org_a)).id
    finally:
        db.close()

    run_id = client_a.post(f"/monitors/{mid}/answer-simulator/run",
                           json={"custom_questions": ["What pricing plans are available?"]}).json()["run_id"]

    assert client_b.get(f"/monitors/{mid}/answer-simulator/questions").status_code == 404
    assert client_b.post(f"/monitors/{mid}/answer-simulator/run",
                         json={"custom_questions": ["x"]}).status_code == 404
    assert client_b.get(f"/monitors/{mid}/answer-simulator/runs/{run_id}/results").status_code == 404


def test_two_sites_get_independent_simulator_questions_no_cross_domain_leak():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        scan_a = _scan_with_faq(db, org, url="https://siteA.example/", host="sitea.example")
        scan_b = Scan(url="https://siteB.example/", normalized_url="siteb.example", ars=0,
                     rubric_version="t", status="completed", organization_id=org, result={
                         "sections": [
                             {"id": "metadata", "evidence": {"title": "Site B", "description": None}},
                             {"id": "content", "evidence": {"h1_text": "Site B Home", "heading_questions": []}},
                             {"id": "schema", "evidence": {"faq_questions": [], "entity_evidence": {}}},
                         ],
                         "phase4": {"available": True, "questions": []},
                     })
        db.add(scan_b)
        db.commit()
        db.refresh(scan_b)
        mid_a = _monitor_linked_to_scan(db, org, scan_a, name="Site A").id
        mid_b = _monitor_linked_to_scan(db, org, scan_b, name="Site B").id
    finally:
        db.close()

    bank_a = client.get(f"/monitors/{mid_a}/answer-simulator/questions").json()["bank_questions"]
    bank_b = client.get(f"/monitors/{mid_b}/answer-simulator/questions").json()["bank_questions"]
    assert any(q["question"] == "What pricing plans are available?" for q in bank_a)
    assert bank_b == []   # site B's own scan has no FAQ/heading questions — never borrows site A's


# ------------------------------- report cross-link -------------------------------
def test_report_answer_simulation_endpoint_reflects_latest_run():
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        scan = _scan_with_faq(db, org)
        monitor = _monitor_linked_to_scan(db, org, scan)
        mid, scan_id = monitor.id, scan.id
    finally:
        db.close()

    before = client.get(f"/reports/{scan_id}/answer-simulation").json()
    assert before == {"available": False, "reason": "no_run", "monitor_id": mid}

    client.post(f"/monitors/{mid}/answer-simulator/run",
               json={"custom_questions": ["What pricing plans are available?"]})

    after = client.get(f"/reports/{scan_id}/answer-simulation").json()
    assert after["available"] is True
    assert after["monitor_id"] == mid
    assert after["question_count"] == 1
    assert after["status"] == "completed"
