"""Part B — aggregation: Share of Voice per run, and run-over-run trend.

Every prompt runs multiple times (runs_per_prompt + adaptive), so mention rate is a
PERCENTAGE ACROSS ALL SAMPLES — never a single-sample yes/no. Extraction failures are
EXCLUDED from the denominator and reported separately (a failure is not a negative).
"""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    _RUN_TERMINAL,
    Monitor, PromptGapAnalysis, PromptResult, PromptResultAnalysis, PromptRun, PromptSet, TrackedPrompt,
)

# mention_type values that mean "recommended/compared as a SOLUTION" (so it counts as a
# competitor). None = untyped legacy rows, treated as a solution for backward compatibility.
# "example"/"news"/"other" are NOT competitors — merely referenced, not recommended.
_SOLUTION_TYPES = ("recommendation", "comparison", None)


def _root(domain: str | None) -> str:
    d = (domain or "").strip().lower()
    return d.replace("www.", "").split("/")[0].split(".")[0] if d else ""


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


def _run_search_state(results: list[PromptResult]) -> str:
    """Whether this run's answer calls used web search: 'on' (all), 'off' (none), or
    'mixed'. Used to mark the boundary on the trend where search was turned on — results
    with and without search are NOT comparable."""
    flags = {bool(r.search_enabled) for r in results}
    if not flags:
        return "off"
    if flags == {True}:
        return "on"
    if flags == {False}:
        return "off"
    return "mixed"


# Entity-name suffixes stripped before merging surface forms. DELIBERATELY MINIMAL — these
# are the only normalisations applied (lowercase, collapse whitespace, strip these). We do
# NOT fuzzy-match: merging two genuinely different companies is a worse, invisible failure
# than listing one company twice.
_ENTITY_SUFFIX_WORDS = {"inc", "ai"}
_ENTITY_TLDS = (".com", ".io")


def _normalise_entity_key(name: str) -> str:
    """Deterministic merge key for a recommended entity: lowercase, collapse whitespace, and
    strip common suffixes (Inc, AI, .com, .io). Returns '' for an empty name. Never guesses —
    'Profound', 'Profound AI', 'Profound Inc', 'Profound.com' all map to 'profound', but a
    genuinely different surface form ('tryprofound.com' -> 'tryprofound') stays separate."""
    s = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not s:
        return ""
    for tld in _ENTITY_TLDS:
        if s.endswith(tld):
            s = s[: -len(tld)]
            break
    tokens = [t for t in s.split(" ") if t]
    while tokens and tokens[-1].strip(".,") in _ENTITY_SUFFIX_WORDS:
        tokens.pop()
    key = " ".join(tokens).strip(" .,-")
    return key or s


def _brand_fields(db: Session, ps: PromptSet | None) -> dict:
    """Brand identity for a set, resolved from the MONITOR (brand fields live on the site now);
    legacy set fields are a last-resort fallback for monitorless pre-migration sets."""
    empty = {"name": "", "domain": "", "aliases": [], "competitors": []}
    if not ps:
        return empty
    monitor = db.get(Monitor, ps.monitor_id) if ps.monitor_id else None
    if monitor is not None:
        return {
            "name": (monitor.brand_name or monitor.name or ""),
            "domain": (monitor.brand_domain or monitor.normalized_url or ""),
            "aliases": list(monitor.brand_aliases or []),
            "competitors": list(monitor.competitor_domains or []),
        }
    return {
        "name": (ps.brand_name or ""), "domain": (ps.brand_domain or ""),
        "aliases": list(ps.brand_aliases or []), "competitors": list(ps.competitor_domains or []),
    }


def _brand_keys(brand_fields: dict) -> set[str]:
    """Normalised keys that identify the tracked brand (name + aliases + domain root)."""
    keys: set[str] = set()
    for v in [brand_fields.get("name"), *(brand_fields.get("aliases") or [])]:
        k = _normalise_entity_key(v or "")
        if k:
            keys.add(k)
    dk = _normalise_entity_key((brand_fields.get("domain") or "").split("/")[0])
    if dk:
        keys.add(dk)
    return keys


def _leaderboard(successful, results, denom, prompt_hit, brand_fields, excluded, min_appearances):
    """Run-level competitive leaderboard built from stored `recommended_entities` (the ordered
    solutions each answer put forward). Merges surface forms per `_normalise_entity_key`, drops
    excluded AI platforms, applies the min-appearances threshold (the tracked brand is exempt so
    the user always sees their rank), and returns (entries_sorted, excluded_below_threshold).

    head_to_head is the actionable field: prompts where the entity appeared but the brand was
    NOT mentioned — answers it holds and you don't. average_position is the mean rank ACROSS
    SAMPLES WHOSE ANSWER WAS AN ORDERED LIST (signalled by a non-null brand position), null when
    no such sample recommended the entity."""
    brand_keys = _brand_keys(brand_fields)
    appearances: Counter = Counter()
    prompts_by_key: dict[str, set] = {}
    ranks: dict[str, list[int]] = {}
    surface: dict[str, Counter] = {}
    domain_of: dict[str, str | None] = {}

    for a in successful:
        r = results.get(a.result_id)
        pid = r.prompt_id if r else "unknown"
        ordered = a.position is not None          # answer was a ranked list (brand had a rank)
        seen_keys = set()                         # count an entity ONCE per sample
        for idx, e in enumerate(a.recommended_entities or []):
            if isinstance(e, dict):
                nm = (e.get("name") or "").strip()
                dom = e.get("domain_if_stated")
            else:
                nm, dom = str(e).strip(), None
            if not nm or _is_excluded_competitor(nm, dom, excluded):
                continue
            key = _normalise_entity_key(nm)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            appearances[key] += 1
            prompts_by_key.setdefault(key, set()).add(pid)
            if ordered:
                ranks.setdefault(key, []).append(idx + 1)
            surface.setdefault(key, Counter())[nm] += 1
            if key not in domain_of and dom:
                domain_of[key] = dom

    entries = []
    excluded_below = 0
    for key, n in appearances.items():
        is_you = key in brand_keys
        if n < min_appearances and not is_you:
            excluded_below += 1
            continue
        pset = prompts_by_key.get(key, set())
        rk = ranks.get(key, [])
        entries.append({
            "name": surface[key].most_common(1)[0][0],   # most frequent surface form for display
            "appearances": n,
            "prompt_coverage": len(pset),
            "appearance_rate": round(n / denom * 100, 1) if denom else None,
            "average_position": round(sum(rk) / len(rk), 2) if rk else None,
            "head_to_head": sum(1 for pid in pset if prompt_hit.get(pid, 0) == 0),
            "is_you": is_you,
            "domain": domain_of.get(key),
            "prompt_ids": sorted(pset),                  # for click-to-filter on the prompt list
        })
    entries.sort(key=lambda x: (-(x["appearance_rate"] or 0), -x["appearances"], x["name"].lower()))
    for i, e in enumerate(entries):
        e["rank"] = i + 1
    return entries, excluded_below


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

    # citations: brand URLs ranked by frequency. Results captured WITHOUT web search measure
    # training-data recall, not live citation behaviour — they are EXCLUDED from citation
    # metrics (never mixed with search-grounded results) and counted separately.
    url_freq: Counter = Counter()
    citation_search_samples = 0
    citation_excluded_no_search = 0
    for a in successful:
        r = results.get(a.result_id)
        if r is not None and not r.search_enabled:
            citation_excluded_no_search += 1
            continue
        citation_search_samples += 1
        for u in (a.brand_urls_cited or []):
            url_freq[u] += 1
    citation_count = sum(url_freq.values())

    # competitor share of voice. A competitor is counted ONCE per sample, ONLY when it is
    # recommended/compared as a SOLUTION (mention_type) — an entity named as an example /
    # case study / news subject is NOT a competitor (FIX: Air Canada / DPD false positives).
    # AI assistants / search engines stay excluded (FIX1). Entities whose domain/name match
    # the org's configured competitor_domains are flagged `tracked` (the trusted signal).
    ps = db.get(PromptSet, run.prompt_set_id)
    brand_fields = _brand_fields(db, ps)          # from the monitor (brand identity lives on the site)
    configured_roots = {_root(d) for d in brand_fields["competitors"]} - {""}
    excluded_entities = settings.answer_tracking_excluded_entity_set()
    comp_hit: Counter = Counter()
    comp_domain: dict[str, str | None] = {}
    prompt_has_solution: dict[str, bool] = {}
    for a in successful:
        r = results.get(a.result_id)
        pid = r.prompt_id if r else "unknown"
        seen = set()
        for c in (a.competitors_mentioned or []):
            if isinstance(c, dict):
                nm = (c.get("name") or "").strip()
                dom = c.get("domain_if_stated")
                mt = c.get("mention_type")
            else:
                nm, dom, mt = str(c).strip(), None, None
            if not nm or _is_excluded_competitor(nm, dom, excluded_entities):
                continue
            if mt not in _SOLUTION_TYPES:          # example / news / other -> not a competitor
                continue
            prompt_has_solution[pid] = True
            if nm.lower() not in seen:
                seen.add(nm.lower())
                comp_hit[nm] += 1
                if nm not in comp_domain:
                    comp_domain[nm] = dom

    def _tracked(nm: str, dom: str | None) -> bool:
        return bool(configured_roots) and (
            _root(dom) in configured_roots or nm.strip().lower() in configured_roots)

    # per-prompt ORDERED recommended entities (for "who was recommended instead").
    prompt_recommended: dict[str, list] = {}
    for a in successful:
        r = results.get(a.result_id)
        pid = r.prompt_id if r else "unknown"
        bucket = prompt_recommended.setdefault(pid, [])
        for e in (a.recommended_entities or []):
            nm = (e.get("name") if isinstance(e, dict) else str(e)).strip()
            if nm and nm not in bucket:
                bucket.append(nm)

    # sentiment distribution across mentions
    sentiment = Counter(a.sentiment for a in mentioned if a.sentiment)

    positions = [a.position for a in successful if a.position is not None]
    avg_position = round(sum(positions) / len(positions), 2) if positions else None

    # run-level competitive leaderboard (who is beating the brand, and where it is absent)
    leaderboard, leaderboard_excluded = _leaderboard(
        successful, results, denom, prompt_hit, brand_fields, excluded_entities,
        settings.answer_tracking_leaderboard_min_appearances,
    )

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
        "per_prompt": _per_prompt(db, run, prompt_total, prompt_hit, _rate,
                                  prompt_recommended, prompt_has_solution),
        "citation_count": citation_count,
        "citation_search_samples": citation_search_samples,
        "citation_excluded_no_search": citation_excluded_no_search,
        "search_enabled": _run_search_state(list(results.values())),
        "citation_diagnosis": _citation_diagnosis(list(results.values()), citation_count),
        "cited_urls": [{"url": u, "count": n} for u, n in url_freq.most_common()],
        "competitors": [
            {"name": nm, "mentions": n, "mention_rate": _rate(n, denom),
             "domain": comp_domain.get(nm), "tracked": _tracked(nm, comp_domain.get(nm))}
            for nm, n in comp_hit.most_common()
        ],
        "sentiment": dict(sentiment),
        "average_position": avg_position,
        "leaderboard": leaderboard,
        "leaderboard_excluded": leaderboard_excluded,
        "leaderboard_min_appearances": settings.answer_tracking_leaderboard_min_appearances,
        "answer_models": _answer_model_signature(list(results.values())),
        "extraction_models": sorted({a.extraction_model for a in analyses if a.extraction_model}),
    }


def _per_prompt(db, run, prompt_total, prompt_hit, rate_fn,
                prompt_recommended=None, prompt_has_solution=None) -> list[dict]:
    prompt_recommended = prompt_recommended or {}
    prompt_has_solution = prompt_has_solution or {}
    texts = {p.id: p.text for p in db.query(TrackedPrompt)
             .filter(TrackedPrompt.prompt_set_id == run.prompt_set_id).all()}
    rows = []
    for pid in prompt_total:
        rate = rate_fn(prompt_hit[pid], prompt_total[pid])
        recommended = prompt_recommended.get(pid, [])
        # Prompt-quality hint (Task 4): 0% mentions AND nothing recommended AND no solution
        # competitor in the category => the prompt may simply not be a category query. This
        # is a HINT only — the prompt is NEVER auto-disabled/deleted.
        irrelevant_hint = (rate == 0.0) and not recommended and not prompt_has_solution.get(pid)
        rows.append({
            "prompt_id": pid, "text": texts.get(pid, ""),
            "samples": prompt_total[pid], "mentions": prompt_hit[pid],
            "mention_rate": rate,
            "is_gap": (rate == 0.0),            # 0% mentions => actionable gap
            "recommended_entities": recommended,   # who was recommended instead (ordered)
            "irrelevant_hint": irrelevant_hint,
        })
    # gaps first (most actionable), then by ascending mention rate
    rows.sort(key=lambda r: (not r["is_gap"], r["mention_rate"] if r["mention_rate"] is not None else 0))
    return rows


def _previous_terminal_run(db: Session, run: PromptRun) -> PromptRun | None:
    """The most recent terminal run of the same set BEFORE this one (for run-over-run deltas)."""
    return (db.query(PromptRun)
            .filter(PromptRun.prompt_set_id == run.prompt_set_id,
                    PromptRun.status.in_(_RUN_TERMINAL),
                    PromptRun.created_at < run.created_at)
            .order_by(PromptRun.created_at.desc())
            .first())


def _leaderboard_for_run(db: Session, run: PromptRun) -> list[dict]:
    """The run-level leaderboard ONLY — the minimal subset of `run_metrics`'s work that
    `_attach_rank_delta` actually needs from a PREVIOUS run (just entity ranks), skipping
    the per-provider/per-prompt/citation/competitor/sentiment computation `run_metrics`
    also does. Deliberately mirrors run_metrics's own setup for `_leaderboard`'s inputs
    (a small, intentional duplication) rather than restructuring that larger, well-
    tested function — see run_metrics for the full Share-of-Voice computation."""
    results = _results_by_id(db, run.id)
    analyses = (db.query(PromptResultAnalysis)
                .filter(PromptResultAnalysis.run_id == run.id).all())
    successful = [a for a in analyses if not a.extraction_failed]
    denom = len(successful)

    prompt_hit: Counter = Counter()
    for a in successful:
        if a.brand_mentioned:
            r = results.get(a.result_id)
            prompt_hit[r.prompt_id if r else "unknown"] += 1

    ps = db.get(PromptSet, run.prompt_set_id)
    brand_fields = _brand_fields(db, ps)
    excluded_entities = settings.answer_tracking_excluded_entity_set()
    leaderboard, _excluded = _leaderboard(
        successful, results, denom, prompt_hit, brand_fields, excluded_entities,
        settings.answer_tracking_leaderboard_min_appearances,
    )
    return leaderboard


def _attach_rank_delta(db: Session, run: PromptRun, leaderboard: list[dict]) -> None:
    """Annotate each leaderboard entry with its rank in the previous run and the delta
    (positive = moved UP toward #1). null when there is no prior run or the entity is new."""
    prev = _previous_terminal_run(db, run)
    prev_rank = {}
    if prev is not None:
        prev_rank = {_normalise_entity_key(e["name"]): e["rank"]
                     for e in _leaderboard_for_run(db, prev)}
    for e in leaderboard:
        pr = prev_rank.get(_normalise_entity_key(e["name"]))
        e["prev_rank"] = pr
        e["rank_delta"] = (pr - e["rank"]) if pr is not None else None


def run_summary(db: Session, run: PromptRun) -> dict:
    """Full Share-of-Voice summary for one run, with each zero-mention prompt's gap-to-action
    attached (from stored gap analysis — no LLM call here)."""
    m = run_metrics(db, run)
    _attach_rank_delta(db, run, m["leaderboard"])
    gaps = {g.prompt_id: {"why": g.why, "actions": g.actions or [], "has_signal": g.has_signal}
            for g in db.query(PromptGapAnalysis).filter(PromptGapAnalysis.run_id == run.id).all()}
    for row in m["per_prompt"]:
        row["gap"] = gaps.get(row["prompt_id"])   # None when the prompt was not a gap
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
    prev_search = None
    for run in runs:
        m = run_metrics(db, run)
        sig = (tuple(m["answer_models"]), tuple(m["extraction_models"]))
        model_changed = prev_sig is not None and sig != prev_sig
        # Mark where web search was turned on/off — a search change makes citation counts
        # (and often mention rate) not comparable to the prior run.
        search_changed = prev_search is not None and m["search_enabled"] != prev_search
        brand_rank = next((e["rank"] for e in m["leaderboard"] if e["is_you"]), None)
        points.append({
            "run_id": run.id,
            "created_at": run.created_at,
            "mention_rate": m["mention_rate"],
            "citation_count": m["citation_count"],
            "competitors": m["competitors"],
            "brand_rank": brand_rank,           # the brand's leaderboard position this run
            "answer_models": m["answer_models"],
            "extraction_models": m["extraction_models"],
            "search_enabled": m["search_enabled"],
            "model_changed": model_changed,     # vs the previous run in this series
            "search_changed": search_changed,   # web search toggled vs the previous run
        })
        prev_sig = sig
        prev_search = m["search_enabled"]
    return {"prompt_set_id": ps.id, "runs": points}
