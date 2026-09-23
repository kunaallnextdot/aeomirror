"""Question Bank: consolidates real questions from Phase 4 Question Mining (scan FAQ +
question-shaped headings) and Answer Tracking (actually-tracked prompts) into one
deduplicated, evidence-linked view — no new data is invented, no new database table,
no LLM classifier. Pure builders (`services.answer_tracking.visibility.build_question_bank`
/ `gate_question_bank`) are tested directly against hand-built fixtures (same convention
as test_phase4.py); billing/security/cache behavior is tested through the real HTTP
endpoint against DB-wired scans/monitors/runs (same convention as test_phase3.py).

No scanner/scoring/Answer Tracking calculation is touched anywhere in this file."""
import app.api.routes_scan as rs
import app.billing.plans as plans_mod
from app.config import settings
from app.db.models import (
    EXTRACTION_COMPLETE, RUN_COMPLETED, Monitor, PromptResult, PromptResultAnalysis,
    PromptRun, PromptSet, Scan, TrackedPrompt,
)
from app.db.session import SessionLocal
from app.reports.engine import build_report
from app.reports.phase4 import normalize_question_key
from app.scanner.models import PageBundle
from app.services.answer_tracking import visibility as V
from tests.authutil import auth_client
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS


# ------------------------------- pure-builder fixtures -------------------------------
def _schema_section(*, faq_questions=None, score=30, weight=15):
    return {
        "id": "schema", "label": "Structured Data", "weight": weight, "score": score,
        "status": "warn", "issues": [], "recommendations": [],
        "evidence": {"state": "present", "malformed": 0, "has_entity": False,
                    "entity_types": [], "detected_types": [], "content": {},
                    "entity_evidence": {"name": None, "url": None, "logo": None, "same_as": []},
                    "faq_questions": faq_questions or []},
    }


def _content_section(*, heading_questions=None, score=80, weight=12):
    return {
        "id": "content", "label": "Content Structure", "weight": weight, "score": score,
        "status": "pass", "issues": [], "recommendations": [],
        "evidence": {"h1_count": 1, "h2_count": 2, "heading_jumps": 0, "semantic_html": True,
                    "paragraphs": 5, "lists": 1, "heading_questions": heading_questions or []},
    }


def _scan_dict(sections, url="https://acme.example/"):
    return {"scan_id": "s1", "url": url, "domain": "acme.example", "overall_score": 50,
            "scanner_version": "3.0.0", "scanned_at": "2026-01-01T00:00:00Z",
            "sections": sections, "scan_ready": True}


def _summary(per_prompt=None, average_position=None):
    return {"per_prompt": per_prompt or [], "average_position": average_position}


def _prompt_row(prompt_id, text, *, mention_rate=0.0, is_gap=True):
    return {"prompt_id": prompt_id, "text": text, "samples": 2,
            "mentions": 0 if is_gap else 1, "mention_rate": mention_rate,
            "is_gap": is_gap, "recommended_entities": [], "gap": None}


# ===================================================================
# 1-2: scan-derived questions (FAQ + headings)
# ===================================================================
def test_faq_questions_appear_in_question_bank():
    phase4_q = build_report(_scan_dict([_schema_section(
        faq_questions=[{"question": "What does Acme treat?", "answer": "Back pain."}])]))["phase4"]["questions"]
    bank = V.build_question_bank(phase4_q)
    assert any(q["question"] == "What does Acme treat?" and "scan_faq" in q["sources"]
              for q in bank["questions"])


def test_question_shaped_headings_appear_in_question_bank():
    phase4_q = build_report(_scan_dict([_content_section(
        heading_questions=["How does physical therapy work?"])]))["phase4"]["questions"]
    bank = V.build_question_bank(phase4_q)
    assert any(q["question"] == "How does physical therapy work?" and "scan_heading" in q["sources"]
              for q in bank["questions"])


# ===================================================================
# 3: Answer Tracking prompts appear when actually tracked
# ===================================================================
def test_tracked_answer_tracking_prompts_appear():
    summary = _summary([_prompt_row("p1", "What is the best CRM?", mention_rate=40.0, is_gap=False)])
    bank = V.build_question_bank(None, summary)
    q = next(q for q in bank["questions"] if q["question"] == "What is the best CRM?")
    assert q["sources"] == ["answer_tracking"]
    assert q["answer_tracking"]["tracked"] is True
    assert q["answer_tracking"]["mention_rate"] == 40.0


# ===================================================================
# 4-5: deduplication rules
# ===================================================================
def test_same_question_from_all_three_sources_deduplicates_to_one():
    phase4_q = {"questions": [
        {"text": "What is A?", "answer": None, "source": "schema_faq",
         "affected_url": "https://acme.example/", "category": "informational"},
        {"text": "what is a ?", "answer": None, "source": "page_heading",
         "affected_url": "https://acme.example/about", "category": "informational"},
    ]}
    summary = _summary([_prompt_row("p1", "What is A", mention_rate=20.0, is_gap=False)])
    bank = V.build_question_bank(phase4_q, summary)
    assert bank["total_count"] == 1
    q = bank["questions"][0]
    assert set(q["sources"]) == {"scan_faq", "scan_heading", "answer_tracking"}
    assert q["evidence_count"] == 3
    assert len(q["source_urls"]) == 2


def test_similar_but_different_questions_remain_separate():
    phase4_q = {"questions": [
        {"text": "How much does X cost?", "answer": None, "source": "page_heading",
         "affected_url": "https://acme.example/", "category": "pricing"},
        {"text": "How long does X take?", "answer": None, "source": "page_heading",
         "affected_url": "https://acme.example/", "category": "informational"},
    ]}
    bank = V.build_question_bank(phase4_q)
    assert bank["total_count"] == 2
    assert {q["question"] for q in bank["questions"]} == {"How much does X cost?", "How long does X take?"}


def test_normalize_question_key_merges_whitespace_case_and_terminal_punctuation():
    variants = ["What is A?", "what is a ?", "  What   is   A  ", "What is A!", "What is A."]
    keys = {normalize_question_key(v) for v in variants}
    assert keys == {"what is a"}


def test_normalize_question_key_does_not_merge_different_questions():
    assert normalize_question_key("How much does X cost?") != normalize_question_key("How long does X take?")


# ===================================================================
# 6-7: source traceability / no fabrication
# ===================================================================
def test_source_urls_are_real_and_preserved():
    phase4_q = {"questions": [{"text": "Real question?", "answer": None, "source": "schema_faq",
                              "affected_url": "https://real-scanned-page.example/faq",
                              "category": "informational"}]}
    bank = V.build_question_bank(phase4_q)
    assert bank["questions"][0]["source_urls"] == ["https://real-scanned-page.example/faq"]


def test_no_fabricated_questions_when_no_sources_have_data():
    bank = V.build_question_bank(None, None, None)
    assert bank == {"questions": [], "total_count": 0}
    bank2 = V.build_question_bank({"questions": []}, {"per_prompt": []}, {"items": []})
    assert bank2 == {"questions": [], "total_count": 0}


# ===================================================================
# 8: reuses the existing deterministic category logic
# ===================================================================
def test_reuses_existing_deterministic_category_logic():
    summary = _summary([_prompt_row("p1", "How much does treatment cost?", is_gap=False)])
    bank = V.build_question_bank(None, summary)
    assert bank["questions"][0]["category"] == "pricing"   # same keyword rule as Phase 4's classifier
    # deterministic: identical input -> identical output every time
    assert V.build_question_bank(None, summary) == bank


# ===================================================================
# 9: tracked gap metadata is preserved
# ===================================================================
def test_tracked_gap_metadata_preserved():
    summary = _summary([_prompt_row("p1", "Untracked-sounding gap question?", mention_rate=0.0, is_gap=True)])
    bank = V.build_question_bank(None, summary)
    at = bank["questions"][0]["answer_tracking"]
    assert at["tracked"] is True and at["is_gap"] is True and at["mention_rate"] == 0.0


# ===================================================================
# 10: related opportunities are linked, never duplicated
# ===================================================================
def test_related_opportunities_linked_without_duplication():
    summary = _summary([_prompt_row("p1", "Gap question?", mention_rate=0.0, is_gap=True)])
    opportunities = {"items": [
        {"id": "answer_gap:p1", "type": "answer_gap",
         "affected_prompts": [{"prompt_id": "p1", "prompt": "Gap question?"}], "description": "x"},
    ]}
    bank = V.build_question_bank(None, summary, opportunities)
    q = bank["questions"][0]
    assert q["related_opportunity_ids"] == ["answer_gap:p1"]
    # linking never creates a second opportunity or a second question entry
    assert bank["total_count"] == 1


def test_question_opportunity_matches_via_description_not_affected_prompts():
    phase4_q = {"questions": [{"text": "Untracked content question?", "answer": None,
                              "source": "page_heading", "affected_url": "https://acme.example/",
                              "category": "informational"}]}
    opportunities = {"items": [
        {"id": "question_opportunity:untracked content question", "type": "question_opportunity",
         "affected_prompts": [], "description": "Untracked content question?"},
    ]}
    bank = V.build_question_bank(phase4_q, None, opportunities)
    assert bank["questions"][0]["related_opportunity_ids"] == ["question_opportunity:untracked content question"]


# ===================================================================
# 12: free response must not contain the hidden full paid dataset
# ===================================================================
def test_gate_question_bank_free_preview_and_no_hidden_paid_data():
    phase4_q = {"questions": [
        {"text": f"Question {i}?", "answer": None, "source": "page_heading",
         "affected_url": "https://acme.example/", "category": "informational"}
        for i in range(5)
    ]}
    bank = V.build_question_bank(phase4_q)
    gated = V.gate_question_bank(bank, unlocked=False)
    assert gated["preview"] is True
    assert len(gated["questions"]) <= 3
    assert gated["locked_question_count"] == bank["total_count"] - len(gated["questions"])
    shown = {q["question"] for q in gated["questions"]}
    full = {q["question"] for q in bank["questions"]}
    assert shown < full   # strictly fewer than the full set — the rest is truly absent, not blurred


def test_gate_question_bank_paid_returns_full_dataset_unchanged():
    phase4_q = {"questions": [{"text": "Q?", "answer": None, "source": "page_heading",
                              "affected_url": "https://acme.example/", "category": "informational"}]}
    bank = V.build_question_bank(phase4_q)
    assert V.gate_question_bank(bank, unlocked=True) == bank


# ===================================================================
# End-to-end: real scan + monitor + run through the API
# ===================================================================
async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"content-encoding": "gzip", "cache-control": "max-age=60"})


def _make_scan(client, monkeypatch, url="https://questionbank.example/"):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    return client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def _enforce_billing(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", 50)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def _wire_monitor_and_run(db, org_id, scan_id, url, normalized, *, prompt_text="best crm?",
                          mentioned=True):
    m = Monitor(organization_id=org_id, url=url, normalized_url=normalized,
               frequency="weekly", status="active", latest_scan_id=scan_id,
               brand_name="Acme", brand_domain=normalized)
    db.add(m); db.commit(); db.refresh(m)
    ps = PromptSet(organization_id=org_id, name="S", monitor_id=m.id,
                   brand_name="Acme", brand_domain=normalized)
    db.add(ps); db.commit(); db.refresh(ps)
    p = TrackedPrompt(prompt_set_id=ps.id, organization_id=org_id, text=prompt_text)
    db.add(p); db.commit(); db.refresh(p)
    run = PromptRun(organization_id=org_id, prompt_set_id=ps.id, status=RUN_COMPLETED,
                    extraction_status=EXTRACTION_COMPLETE, total_calls=0, estimated_cost_usd=0.0)
    db.add(run); db.commit(); db.refresh(run)
    r = PromptResult(run_id=run.id, prompt_id=p.id, organization_id=org_id, provider="openai",
                     model="m", run_index=0, raw_response="Acme is great", search_enabled=True)
    db.add(r); db.commit(); db.refresh(r)
    db.add(PromptResultAnalysis(result_id=r.id, run_id=run.id, organization_id=org_id,
                                brand_mentioned=mentioned, extraction_failed=False,
                                extraction_model="e1"))
    db.commit()
    return m


def test_free_response_contains_only_allowed_preview_end_to_end(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)

    body = client.get(f"/reports/{scan_id}/question-bank").json()
    assert body["available"] is True
    assert body.get("preview") is True
    assert len(body["questions"]) <= 3
    assert "locked_question_count" in body


def test_paid_response_contains_full_grounded_data_end_to_end(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    _go_pro(client)

    body = client.get(f"/reports/{scan_id}/question-bank").json()
    assert body["available"] is True
    assert "preview" not in body
    # GOOD_HTML has no FAQPage schema and no "?"-ending headings, so total_count may be
    # 0 here — the point is the response is the FULL (ungated) shape, not a preview.
    assert "locked_question_count" not in body


def test_org_isolation_on_question_bank_endpoint(monkeypatch):
    _enforce_billing(monkeypatch)
    client_a, _ = auth_client()
    client_b, _ = auth_client()
    scan_id = _make_scan(client_a, monkeypatch)
    assert client_a.get(f"/reports/{scan_id}/question-bank").status_code == 200
    assert client_b.get(f"/reports/{scan_id}/question-bank").status_code == 404


def test_empty_source_case_returns_clean_empty_state(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)   # GOOD_HTML has no FAQ/heading questions, no monitor
    body = client.get(f"/reports/{scan_id}/question-bank").json()
    assert body["available"] is True
    assert body["questions"] == []
    assert body["total_count"] == 0
    assert body["monitor_id"] is None


def test_pending_scan_does_not_manufacture_scan_derived_questions(monkeypatch):
    _enforce_billing(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    db = SessionLocal()
    try:
        row = Scan(url="https://pendingqb.example/", normalized_url="pendingqb.example",
                  ars=0, rubric_version="t", status="pending",
                  result={"bulk": {"requested": 2, "urls": ["a", "b"]}}, organization_id=org_id)
        db.add(row); db.commit(); db.refresh(row)
        scan_id = row.id
    finally:
        db.close()

    resp = client.get(f"/reports/{scan_id}/question-bank").json()
    assert resp == {"available": False, "reason": "scan_incomplete"}


def test_question_bank_reuses_cached_report_no_extra_build_report(monkeypatch):
    """Fix 2's cache must extend to this new endpoint too — no independent rebuild."""
    import app.reports.service as report_service
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    client.get(f"/reports/{scan_id}")   # warm the cache, as a real page-load would

    calls = {"n": 0}
    original = report_service.build_report

    def wrapper(*a, **kw):
        calls["n"] += 1
        return original(*a, **kw)

    monkeypatch.setattr(report_service, "build_report", wrapper)
    client.get(f"/reports/{scan_id}/question-bank")
    assert calls["n"] == 0


def test_question_bank_includes_answer_tracking_when_monitor_and_run_exist(monkeypatch):
    _enforce_billing(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    scan_id = _make_scan(client, monkeypatch, url="https://qbwithmonitor.example/")
    _go_pro(client)

    db = SessionLocal()
    try:
        scan_row = db.get(Scan, scan_id)
        _wire_monitor_and_run(db, org_id, scan_id, scan_row.url, scan_row.normalized_url,
                              prompt_text="what is the best crm?", mentioned=True)
    finally:
        db.close()

    resp = client.get(f"/reports/{scan_id}/question-bank").json()
    assert resp["monitor_id"] is not None
    q = next((q for q in resp["questions"] if q["question"] == "what is the best crm?"), None)
    assert q is not None
    assert q["answer_tracking"]["tracked"] is True


# ===================================================================
# 18: existing Answer Tracking behavior is unaffected (spot check — full
# regression is the existing test_answer_tracking*.py suites, run separately)
# ===================================================================
def test_run_summary_endpoint_unaffected_by_question_bank(monkeypatch):
    _enforce_billing(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    scan_id = _make_scan(client, monkeypatch, url="https://qbregress.example/")
    db = SessionLocal()
    try:
        scan_row = db.get(Scan, scan_id)
        monitor = _wire_monitor_and_run(db, org_id, scan_id, scan_row.url, scan_row.normalized_url)
        ps = db.query(PromptSet).filter(PromptSet.monitor_id == monitor.id).first()
        run = db.query(PromptRun).filter(PromptRun.prompt_set_id == ps.id).first()
        run_id = run.id
    finally:
        db.close()
    resp = client.get(f"/prompt-runs/{run_id}/summary")
    assert resp.status_code == 200
    assert resp.json()["mention_rate"] == 100.0
