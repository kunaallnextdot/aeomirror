"""Part B — extraction/analysis.

For each stored answer (`prompt_results` row) we make ONE cheap LLM call that returns a
structured extraction. Mention detection is the LLM's job, NOT substring matching:
substrings produce false positives ("nextdot" in "nextdoor") and miss multi-word /
possessive forms. Brand aliases are supplied to the model as context.

Robustness:
  - Parse defensively (strip fences, tolerate trailing prose), retry ONCE with a stricter
    instruction on unparseable output.
  - On repeated parse failure persist the row with extraction_failed=true and the raw
    output. A failed extraction is NOT a negative result — never record brand_mentioned
    =false for it (that would understate the true mention rate). It is excluded from
    mention-rate denominators downstream.

Citations: prefer the provider's structured citations from Part A when present; use the
LLM-extracted URLs ONLY when the provider returned citations = None (cannot report).
None and [] mean different things and are handled distinctly.

Extraction reads stored raw responses only — it NEVER calls the answer providers, so a
run can be re-analysed cheaply (see reanalyse).
"""
from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    EXTRACTION_COMPLETE, EXTRACTION_FAILED, EXTRACTION_RUNNING,
    Monitor, PromptResult, PromptResultAnalysis, PromptRun, PromptSet, TrackedPrompt,
)

from . import providers as provider_registry

log = logging.getLogger("app.answer_tracking.extraction")

_SENTIMENTS = ("positive", "neutral", "negative")


# --------------------------- brand context ---------------------------
def brand_context(db: Session, ps: PromptSet) -> dict:
    """Resolve the brand identity for extraction. Brand fields win; where empty, fall
    back to the linked monitor (seed source)."""
    monitor = db.get(Monitor, ps.monitor_id) if ps and ps.monitor_id else None
    name = (ps.brand_name or (monitor.name if monitor else "") or "").strip()
    domain = (ps.brand_domain or (monitor.normalized_url if monitor else "") or "").strip().lower()
    aliases = [a for a in (ps.brand_aliases or []) if isinstance(a, str) and a.strip()]
    competitors = [c for c in (ps.competitor_domains or []) if isinstance(c, str) and c.strip()]
    return {"name": name, "domain": domain, "aliases": aliases, "competitors": competitors}


# --------------------------- prompt + parse ---------------------------
def build_extraction_prompt(brand: dict, prompt_text: str, answer_text: str, *,
                            strict: bool = False) -> str:
    aliases = ", ".join(brand["aliases"]) or "(none)"
    competitors = ", ".join(brand["competitors"]) or "(none provided)"
    excluded = ", ".join(settings.answer_tracking_excluded_entity_names())
    strict_note = (
        "\n\nYOUR PREVIOUS REPLY WAS NOT VALID JSON. Reply with ONLY the JSON object — no "
        "markdown, no code fences, no commentary before or after."
    ) if strict else ""
    return (
        "You analyse an AI assistant's answer to detect whether a specific brand is "
        "mentioned or cited. Judge mentions by meaning, not string matching: do not count "
        "an unrelated word that merely contains the brand as a substring, and DO count "
        "multi-word or possessive forms and the listed aliases.\n\n"
        f"BRAND NAME: {brand['name'] or '(unknown)'}\n"
        f"BRAND DOMAIN: {brand['domain'] or '(unknown)'}\n"
        f"BRAND ALIASES: {aliases}\n"
        f"KNOWN COMPETITOR DOMAINS: {competitors}\n"
        f"NOT COMPETITORS — never list these as competitors (they are AI assistants / search "
        f"engines, not competing brands): {excluded}\n\n"
        f"THE QUESTION ASKED:\n{prompt_text}\n\n"
        f"THE ASSISTANT'S ANSWER:\n{answer_text}\n\n"
        "Return a single JSON object with EXACTLY these keys:\n"
        '{"brand_mentioned": true|false, '
        '"mention_context": "the sentence containing the mention" or null, '
        '"sentiment": "positive"|"neutral"|"negative"|null, '
        '"brand_urls_cited": ["https://..."], '
        '"competitors_mentioned": [{"name": "...", "domain_if_stated": "..."|null}], '
        '"position": 1-based rank if the answer is a ranked list, else null}\n'
        "Respond with ONLY that JSON object and nothing else." + strict_note
    )


def parse_extraction(text: str | None) -> dict | None:
    """Return the parsed extraction dict, or None when it can't be recovered. Tolerates
    markdown fences and trailing prose by isolating the outermost JSON object."""
    if not text or not text.strip():
        return None
    candidates = []
    s = text.strip()
    first, last = s.find("{"), s.rfind("}")
    if first != -1 and last > first:
        candidates.append(s[first:last + 1])   # outermost braces — defeats fences + prose
    candidates.append(s)
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except Exception:   # noqa: BLE001
            continue
        if isinstance(obj, dict) and "brand_mentioned" in obj:
            return obj
    return None


def _normalize(parsed: dict, brand: dict, result: PromptResult, model: str) -> dict:
    """Build the stored analysis fields from a parsed extraction, merging citations."""
    sentiment = parsed.get("sentiment")
    sentiment = sentiment if sentiment in _SENTIMENTS else None

    position = parsed.get("position")
    try:
        position = int(position) if position is not None else None
    except (TypeError, ValueError):
        position = None

    # competitors: coerce + dedupe within THIS sample (counted once per sample later).
    competitors, seen = [], set()
    for c in (parsed.get("competitors_mentioned") or []):
        if isinstance(c, dict):
            nm = (c.get("name") or "").strip()
            dom = c.get("domain_if_stated")
        elif isinstance(c, str):
            nm, dom = c.strip(), None
        else:
            continue
        key = (nm.lower(), (dom or "").lower())
        if nm and key not in seen:
            seen.add(key)
            competitors.append({"name": nm, "domain_if_stated": (dom or None)})

    # citations: prefer provider structured citations (present => list or []), fall back
    # to LLM-extracted URLs ONLY when the provider cannot report citations (None).
    if result.citations is None:
        urls = [u for u in (parsed.get("brand_urls_cited") or []) if isinstance(u, str) and u]
    else:
        dom = brand["domain"]
        urls = [c.get("url") for c in result.citations
                if isinstance(c, dict) and c.get("url") and dom and dom in c["url"].lower()]

    return {
        "brand_mentioned": bool(parsed.get("brand_mentioned")),
        "mention_context": (parsed.get("mention_context") or None),
        "sentiment": sentiment,
        "brand_urls_cited": urls,
        "competitors_mentioned": competitors,
        "position": position,
        "extraction_failed": False,
        "extraction_model": model,
        "raw_output": None,
        "calls": 0,   # filled by caller
    }


def _failed(model: str, raw_output: str | None) -> dict:
    """A failed extraction: NOT a negative result — brand_mentioned stays null."""
    return {
        "brand_mentioned": None, "mention_context": None, "sentiment": None,
        "brand_urls_cited": None, "competitors_mentioned": None, "position": None,
        "extraction_failed": True, "extraction_model": model, "raw_output": raw_output,
        "calls": 0,
    }


# --------------------------- one extraction ---------------------------
async def extract_one(provider, brand: dict, result: PromptResult, prompt_text: str) -> dict:
    """One LLM extraction with a single retry on unparseable/failed output. Never raises."""
    timeout = settings.answer_tracking_query_timeout_seconds
    model = getattr(provider, "model", settings.answer_tracking_extraction_model_resolved)
    raw_last: str | None = None
    calls = 0
    for attempt in range(2):
        prompt = build_extraction_prompt(brand, prompt_text, result.raw_response or "",
                                         strict=attempt > 0)
        try:
            res = await provider.query(prompt, timeout=timeout)
            calls += 1
        except Exception:   # noqa: BLE001 — a provider error is an extraction failure, not a negative
            calls += 1
            raw_last = None
            continue
        raw_last = res.text
        parsed = parse_extraction(res.text)
        if parsed is not None:
            rec = _normalize(parsed, brand, result, model)
            rec["calls"] = calls
            return rec
    rec = _failed(model, raw_last)
    rec["calls"] = calls
    return rec


# --------------------------- run orchestration ---------------------------
async def extract_for_run(db: Session, run: PromptRun) -> dict:
    """Analyse every usable answer of a run. Writes prompt_result_analysis rows (replacing
    any prior ones for the run), adds extraction cost to the run, and sets extraction
    status. Never raises for an individual row."""
    run.extraction_status = EXTRACTION_RUNNING
    db.commit()

    ps = db.get(PromptSet, run.prompt_set_id)
    brand = brand_context(db, ps) if ps else {"name": "", "domain": "", "aliases": [], "competitors": []}
    provider = provider_registry.extraction_provider()

    # Re-analysis safety: drop any prior analyses for this run first.
    db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run.id).delete(
        synchronize_session=False)
    db.commit()

    if provider is None:
        run.extraction_status = EXTRACTION_FAILED
        db.commit()
        log.warning("answer-tracking: extraction skipped (no provider) run=%s", run.id)
        return {"run_id": run.id, "status": EXTRACTION_FAILED, "analyzed": 0, "calls": 0}

    results = db.query(PromptResult).filter(PromptResult.run_id == run.id).all()
    # Only rows with a usable answer body — answer-phase failures have nothing to analyse.
    analyzable = [r for r in results if r.raw_response and not r.error]
    prompt_texts = {p.id: p.text for p in db.query(TrackedPrompt)
                    .filter(TrackedPrompt.prompt_set_id == run.prompt_set_id).all()}

    sem = asyncio.Semaphore(max(1, settings.answer_tracking_max_concurrency))

    async def _one(r):
        async with sem:
            rec = await extract_one(provider, brand, r, prompt_texts.get(r.prompt_id, ""))
        return r, rec

    pairs = list(await asyncio.gather(*[_one(r) for r in analyzable])) if analyzable else []

    calls = 0
    for r, rec in pairs:
        calls += rec["calls"]
        db.add(PromptResultAnalysis(
            result_id=r.id, run_id=run.id, organization_id=run.organization_id,
            brand_mentioned=rec["brand_mentioned"], mention_context=rec["mention_context"],
            sentiment=rec["sentiment"], brand_urls_cited=rec["brand_urls_cited"],
            competitors_mentioned=rec["competitors_mentioned"], position=rec["position"],
            extraction_failed=rec["extraction_failed"], extraction_model=rec["extraction_model"],
            raw_output=rec["raw_output"],
        ))
    rate = provider_registry.rate_for(settings.answer_tracking_extraction_provider.strip().lower())
    run.estimated_cost_usd = round((run.estimated_cost_usd or 0.0) + calls * rate, 6)
    run.extraction_status = EXTRACTION_COMPLETE
    db.commit()
    return {"run_id": run.id, "status": EXTRACTION_COMPLETE, "analyzed": len(pairs), "calls": calls}


async def run_pending_extractions(db: Session) -> int:
    """Worker stage: analyse every run whose answer phase is terminal but whose extraction
    is still pending. Per-run isolation — one failure never aborts the rest. Returns the
    number of runs analysed."""
    from app.db.models import _RUN_TERMINAL, EXTRACTION_PENDING

    due = (db.query(PromptRun)
           .filter(PromptRun.status.in_(_RUN_TERMINAL),
                   PromptRun.extraction_status == EXTRACTION_PENDING)
           .all())
    done = 0
    for run in due:
        try:
            await extract_for_run(db, run)
            done += 1
        except Exception:   # noqa: BLE001 — isolate this run
            log.exception("answer-tracking: extraction failed run=%s", run.id)
            try:
                run.extraction_status = EXTRACTION_FAILED
                db.commit()
            except Exception:   # noqa: BLE001
                db.rollback()
    return done
