"""Part B — aggregation: Share of Voice per run, and run-over-run trend.

Every prompt runs multiple times (runs_per_prompt + adaptive), so mention rate is a
PERCENTAGE ACROSS ALL SAMPLES — never a single-sample yes/no. Extraction failures are
EXCLUDED from the denominator and reported separately (a failure is not a negative).
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    _RUN_TERMINAL,
    PromptResult, PromptResultAnalysis, PromptRun, PromptSet, TrackedPrompt,
)


def _is_excluded_competitor(name: str, domain: str | None, excluded: set[str]) -> bool:
    """True when a detected 'competitor' is actually an AI assistant / search engine we
    exclude from Share of Voice (matched by name or domain root, case-insensitive)."""
    if (name or "").strip().lower() in excluded:
        return True
    d = (domain or "").strip().lower()
    if d:
        root = d.replace("www.", "").split("/")[0].split(".")[0]
        if root and root in excluded:
            return True
    return False


def _citation_diagnosis(results: list[PromptResult], citation_count: int) -> dict:
    """Explain a citation count (esp. zero): can any provider report citations at all, and
    was search on? Distinguishes 'no provider can report' (None) from 'searched, not cited'
    from 'search disabled'."""
    per: dict[str, dict] = {}
    for r in results:
        d = per.setdefault(r.provider, {"can_report_citations": False, "search_enabled": False})
        if r.citations is not None:            # None = cannot report; [] or list = can report
            d["can_report_citations"] = True
        if r.search_enabled:
            d["search_enabled"] = True
    reporting = [p for p, d in per.items() if d["can_report_citations"]]
    reporting_and_searching = [p for p in reporting if per[p]["search_enabled"]]
    if citation_count > 0:
        status = "has_citations"
    elif not per:
        status = "no_data"
    elif not reporting:
        status = "no_provider_reports_citations"   # every provider returned citations=None
    elif not reporting_and_searching:
        status = "search_disabled"                 # a capable provider ran, but search was off
    else:
        status = "searched_not_cited"              # capable + searched, brand genuinely not cited
    return {"status": status,
            "per_provider": [{"provider": p, **d} for p, d in sorted(per.items())]}


def _results_by_id(db: Session, run_id: str) -> dict[str, PromptResult]:
    return {r.id: r for r in db.query(PromptResult).filter(PromptResult.run_id == run_id).all()}


def _answer_model_signature(results: list[PromptResult]) -> list[str]:
    """Distinct provider:model strings used in the answer phase (sorted)."""
    return sorted({f"{r.provider}:{r.model}" for r in results})


def run_metrics(db: Session, run: PromptRun) -> dict:
    """Core Share-of-Voice metrics for one run. Shared by the summary and the trend."""
    results = _results_by_id(db, run.id)
    analyses = (db.query(PromptResultAnalysis)
                .filter(PromptResultAnalysis.run_id == run.id).all())

    successful = [a for a in analyses if not a.extraction_failed]
    excluded = [a for a in analyses if a.extraction_failed]
    denom = len(successful)
    mentioned = [a for a in successful if a.brand_mentioned]

    # per provider (from the linked result row)
    prov_total: Counter = Counter()
    prov_hit: Counter = Counter()
    for a in successful:
        r = results.get(a.result_id)
        prov = r.provider if r else "unknown"
        prov_total[prov] += 1
        if a.brand_mentioned:
            prov_hit[prov] += 1

    # per prompt (across ALL samples of that prompt)
    prompt_total: Counter = Counter()
    prompt_hit: Counter = Counter()
    for a in successful:
        r = results.get(a.result_id)
        pid = r.prompt_id if r else "unknown"
        prompt_total[pid] += 1
        if a.brand_mentioned:
            prompt_hit[pid] += 1

    # citations: brand URLs ranked by frequency
    url_freq: Counter = Counter()
    for a in successful:
        for u in (a.brand_urls_cited or []):
            url_freq[u] += 1
    citation_count = sum(url_freq.values())

    # competitor share of voice (each competitor counted once per sample — already deduped
    # at storage; dedupe again defensively). AI assistants / search engines named in the
    # prompts are EXCLUDED here at aggregation time (FIX1) — raw extractions keep them.
    excluded_entities = settings.answer_tracking_excluded_entity_set()
    comp_hit: Counter = Counter()
    for a in successful:
        seen = set()
        for c in (a.competitors_mentioned or []):
            if isinstance(c, dict):
                nm = (c.get("name") or "").strip()
                dom = c.get("domain_if_stated")
            else:
                nm, dom = str(c).strip(), None
            if not nm or _is_excluded_competitor(nm, dom, excluded_entities):
                continue
            if nm.lower() not in seen:
                seen.add(nm.lower())
                comp_hit[nm] += 1

    # sentiment distribution across mentions
    sentiment = Counter(a.sentiment for a in mentioned if a.sentiment)

    positions = [a.position for a in successful if a.position is not None]
    avg_position = round(sum(positions) / len(positions), 2) if positions else None

    def _rate(hit, total):
        return round(hit / total * 100, 1) if total else None

    return {
        "sample_count": len(analyses),
        "analyzed_count": denom,
        "excluded_extraction_failures": len(excluded),
        "mention_rate": _rate(len(mentioned), denom),
        "mentions": len(mentioned),
        "per_provider": [
            {"provider": p, "samples": prov_total[p], "mentions": prov_hit[p],
             "mention_rate": _rate(prov_hit[p], prov_total[p])}
            for p in sorted(prov_total)
        ],
        "per_prompt": _per_prompt(db, run, prompt_total, prompt_hit, _rate),
        "citation_count": citation_count,
        "citation_diagnosis": _citation_diagnosis(list(results.values()), citation_count),
        "cited_urls": [{"url": u, "count": n} for u, n in url_freq.most_common()],
        "competitors": [
            {"name": nm, "mentions": n, "mention_rate": _rate(n, denom)}
            for nm, n in comp_hit.most_common()
        ],
        "sentiment": dict(sentiment),
        "average_position": avg_position,
        "answer_models": _answer_model_signature(list(results.values())),
        "extraction_models": sorted({a.extraction_model for a in analyses if a.extraction_model}),
    }


def _per_prompt(db, run, prompt_total, prompt_hit, rate_fn) -> list[dict]:
    texts = {p.id: p.text for p in db.query(TrackedPrompt)
             .filter(TrackedPrompt.prompt_set_id == run.prompt_set_id).all()}
    rows = []
    for pid in prompt_total:
        rate = rate_fn(prompt_hit[pid], prompt_total[pid])
        rows.append({
            "prompt_id": pid, "text": texts.get(pid, ""),
            "samples": prompt_total[pid], "mentions": prompt_hit[pid],
            "mention_rate": rate,
            "is_gap": (rate == 0.0),            # 0% mentions => actionable gap
        })
    # gaps first (most actionable), then by ascending mention rate
    rows.sort(key=lambda r: (not r["is_gap"], r["mention_rate"] if r["mention_rate"] is not None else 0))
    return rows


def run_summary(db: Session, run: PromptRun) -> dict:
    """Full Share-of-Voice summary for one run (no analysis-model re-computation)."""
    m = run_metrics(db, run)
    return {
        "run_id": run.id,
        "prompt_set_id": run.prompt_set_id,
        "status": run.status,
        "extraction_status": run.extraction_status,
        "estimated_cost_usd": run.estimated_cost_usd,
        **m,
    }


def set_trend(db: Session, ps: PromptSet, *, n: int = 10) -> dict:
    """Mention rate, citation count, and competitor share across the last N terminal runs
    (oldest -> newest). Flags any run whose answer/extraction model strings changed vs the
    previous run — a model change can move results independently of the real world."""
    runs = (db.query(PromptRun)
            .filter(PromptRun.prompt_set_id == ps.id, PromptRun.status.in_(_RUN_TERMINAL))
            .order_by(PromptRun.created_at.desc())
            .limit(n).all())
    runs = list(reversed(runs))         # oldest -> newest

    points = []
    prev_sig = None
    for run in runs:
        m = run_metrics(db, run)
        sig = (tuple(m["answer_models"]), tuple(m["extraction_models"]))
        model_changed = prev_sig is not None and sig != prev_sig
        points.append({
            "run_id": run.id,
            "created_at": run.created_at,
            "mention_rate": m["mention_rate"],
            "citation_count": m["citation_count"],
            "competitors": m["competitors"],
            "answer_models": m["answer_models"],
            "extraction_models": m["extraction_models"],
            "model_changed": model_changed,   # vs the previous run in this series
        })
        prev_sig = sig
    return {"prompt_set_id": ps.id, "runs": points}
