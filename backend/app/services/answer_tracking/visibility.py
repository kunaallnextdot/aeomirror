"""Phase 3 — the "AI Visibility" product layer.

Pure, deterministic read-side builders over the EXISTING Answer Tracking aggregation
(`aggregation.run_summary`) and, where a linked scan exists, the Phase 1/2 scan report.
No LLM calls, no provider calls, no new scanner logic — this module only reshapes and
frames data that already exists. Nothing is invented: every metric, prompt, competitor,
URL and gap reason is copied verbatim from the aggregation / grounded gap analysis, and a
dimension with no data is OMITTED rather than fabricated.

Metric vocabulary is kept distinct on purpose (never conflated):
  • brand mention   — an answer names the brand           (summary.mentions / mention_rate)
  • brand citation  — an answer cites a brand URL          (summary.citation_count)
  • competitor mention — an answer recommends a competitor (summary.competitors)
"""
from __future__ import annotations

from app.reports.phase4 import classify_question, normalize_question_key

# --- Answerability thresholds (deterministic; documented once, used everywhere) ---
# Per-prompt bucketing, based on the ACTUAL per-prompt mention_rate (% across all samples
# of a prompt):
#   VISIBLE           mention_rate >= VISIBLE_MIN
#   PARTIALLY VISIBLE 0 < mention_rate < VISIBLE_MIN
#   NOT VISIBLE       mention_rate == 0                (an is_gap prompt)
# A prompt with no analysable samples (mention_rate is None) is reported separately as
# "unknown" — never silently bucketed as a failure.
VISIBLE_MIN = 60.0

# Overall site-level answerability verdict (Feature 2). Deterministic, LLM-free. Computed
# over prompts that HAVE data (mention_rate not None): `coverage` = share of those prompts
# where the brand appears at all (mention_rate > 0).
#   INSUFFICIENT DATA  fewer than ANSWERABILITY_MIN_PROMPTS analyzed prompts with data
#   NOT VISIBLE        coverage == 0            (brand absent from every analyzed prompt)
#   VISIBLE            coverage >= VISIBLE_MIN/100
#   PARTIALLY VISIBLE  0 < coverage < VISIBLE_MIN/100
ANSWERABILITY_MIN_PROMPTS = 3

# --- Opportunity priority thresholds (applied to the 0–100 Opportunity Impact) ---
IMPACT_CRITICAL, IMPACT_HIGH, IMPACT_MEDIUM = 75.0, 50.0, 25.0

_PRIORITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

# --- Root-cause grouping (redundancy cleanup) ---
# Deterministic ONLY: opportunities are grouped exclusively by an already-computed,
# real `related_recommendation_id` (set by the scanner-signal-linked builders below —
# _score_loss_opps, _schema_opps, _link_opps, _entity_opp all point back at the SAME
# scanner recommendation when they describe the same underlying deficiency). This is
# never a fuzzy/semantic/LLM match — two opportunities group only when the existing
# architecture already says they share one root cause.
_ROOT_CAUSE_LABEL = {
    "schema": "Organization/entity structured data is incomplete",
    "links": "Internal-link structure needs improvement",
    "content": "Content structure and differentiation need improvement",
    "metadata": "Page metadata (titles/descriptions) needs improvement",
}

# Within a group, the PRIMARY (most-actionable, shown-by-default) item is picked by
# impact first, then this fixed type order (the umbrella score-loss card outranks a
# single missing-type/entity-signal card when impacts tie), then id — fully
# deterministic, no randomness.
_PRIMARY_TYPE_RANK = {"score_loss": 0, "schema_opportunity": 1,
                      "internal_link_opportunity": 1, "crawl_graph_opportunity": 1,
                      "content_opportunity": 1, "entity_opportunity": 2}


def _primary_sort_key(o: dict):
    impact = o["impact"] if o["impact"] is not None else -1
    return (-impact, _PRIMARY_TYPE_RANK.get(o["type"], 9), o["id"])


def _compute_opportunity_groups(items: list[dict]) -> tuple[list[dict], dict[str, bool]]:
    """Group opportunities that share a real `related_recommendation_id` into a
    root-cause group. NEVER removes or edits an item — every piece of evidence stays in
    `items` unchanged; this only adds a presentation-layer relationship on top, so a
    single primary recommendation can be shown with the rest as supporting evidence
    instead of N redundant cards for one deficiency.

    Returns (groups, is_primary_by_id). A group is only formed for 2+ items sharing a
    recommendation id — a lone item needs no "group" framing and is always primary."""
    by_rec: dict[str, list[dict]] = {}
    for o in items:
        rec_id = o.get("related_recommendation_id")
        if rec_id:
            by_rec.setdefault(rec_id, []).append(o)

    groups: list[dict] = []
    is_primary: dict[str, bool] = {o["id"]: True for o in items}
    for rec_id, members in by_rec.items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=_primary_sort_key)
        primary = ordered[0]
        for m in ordered[1:]:
            is_primary[m["id"]] = False
        impacts = [m["impact"] for m in ordered if m["impact"] is not None]
        groups.append({
            "root_cause_id": rec_id,
            "label": _ROOT_CAUSE_LABEL.get(rec_id, f"Related to your {rec_id} recommendation"),
            "primary_id": primary["id"],
            "opportunity_ids": [m["id"] for m in ordered],
            "combined_impact": max(impacts) if impacts else None,
        })
    groups.sort(key=lambda g: g["root_cause_id"])
    return groups, is_primary


def _select_preview_items(items: list[dict], limit: int) -> list[dict]:
    """Free-preview selection prefers PRIMARY (non-redundant) opportunities first, so a
    free caller's limited preview isn't spent on 3 restatements of one root cause, then
    backfills with the next-highest remaining items so the preview is still exactly
    `limit` long whenever there are enough real opportunities to fill it. Relative order
    within each bucket is preserved from the incoming priority/impact sort."""
    primaries = [o for o in items if o.get("is_primary", True)]
    if len(primaries) >= limit:
        return primaries[:limit]
    rest = [o for o in items if not o.get("is_primary", True)]
    return (primaries + rest)[:limit]

# Trend classification threshold (percentage points of mention-rate change run-over-run).
TREND_EPS = 2.0


# ============================== 1. Visibility summary ==============================
def build_visibility_summary(summary: dict) -> dict:
    """Negative-first coverage overview from a run summary. Keeps brand-mention,
    brand-citation and competitor-mention as three DISTINCT metrics."""
    analyzed = summary.get("analyzed_count") or 0
    mentions = summary.get("mentions") or 0
    rate = summary.get("mention_rate")
    competitors = summary.get("competitors") or []
    return {
        "mention_rate": rate,                                   # brand mention rate
        "missed_rate": (round(100.0 - rate, 1) if rate is not None else None),
        "analyzed_count": analyzed,
        "sample_count": summary.get("sample_count") or 0,
        "mentions": mentions,                                   # brand mentions
        "missed": max(0, analyzed - mentions),                  # answers with no brand mention
        "citation_count": summary.get("citation_count") or 0,   # brand citations (distinct)
        "citation_diagnosis": summary.get("citation_diagnosis"),
        "citation_search_samples": summary.get("citation_search_samples") or 0,
        "average_position": summary.get("average_position"),
        "per_provider": summary.get("per_provider") or [],      # provider/samples/mentions/rate
        "competitor_mentions": sum((c.get("mentions") or 0) for c in competitors),
        "excluded_extraction_failures": summary.get("excluded_extraction_failures") or 0,
    }


# ============================== 2. AI Answerability ==============================
def build_answerability(summary: dict) -> dict:
    """Group every tracked prompt into VISIBLE / PARTIALLY VISIBLE / NOT VISIBLE by its
    real mention_rate (thresholds above). The primary metric shown is the actual
    mention_rate — no opaque composite 'answerability score' is invented."""
    visible, partial, not_visible, unknown = [], [], [], []
    for row in summary.get("per_prompt") or []:
        mr = row.get("mention_rate")
        entry = {
            "prompt_id": row.get("prompt_id"),
            "prompt": row.get("text"),
            "samples": row.get("samples"),
            "mentions": row.get("mentions"),
            "mention_rate": mr,
            "is_gap": bool(row.get("is_gap")),
            "recommended_entities": row.get("recommended_entities") or [],
            "gap": row.get("gap"),                              # grounded {why,actions,has_signal} or None
        }
        if mr is None:
            unknown.append(entry)
        elif mr == 0:
            not_visible.append(entry)
        elif mr >= VISIBLE_MIN:
            visible.append(entry)
        else:
            partial.append(entry)

    # Overall verdict (deterministic; see ANSWERABILITY_MIN_PROMPTS docs above).
    analyzed_prompts = len(visible) + len(partial) + len(not_visible)   # prompts WITH data
    prompts_with_mention = len(visible) + len(partial)                  # brand appeared at all
    if analyzed_prompts < ANSWERABILITY_MIN_PROMPTS:
        verdict, label = "insufficient_data", "Insufficient data"
        evidence = ("Not enough Answer Tracking data yet to judge AI visibility — "
                    f"only {analyzed_prompts} prompt(s) analyzed.")
    else:
        coverage = prompts_with_mention / analyzed_prompts
        if coverage == 0:
            verdict, label = "not_visible", "Not visible"
        elif coverage >= VISIBLE_MIN / 100.0:
            verdict, label = "visible", "Visible"
        else:
            verdict, label = "partially_visible", "Partially visible"
        evidence = (f"Your brand appeared in {prompts_with_mention} of {analyzed_prompts} "
                    f"analyzed prompt(s).")

    return {
        "verdict": verdict,               # visible | partially_visible | not_visible | insufficient_data
        "verdict_label": label,
        "evidence": evidence,
        "analyzed_prompts": analyzed_prompts,
        "prompts_with_mention": prompts_with_mention,
        "thresholds": {"visible": f">= {VISIBLE_MIN:g}% mention rate",
                       "partially_visible": f"0% < rate < {VISIBLE_MIN:g}%",
                       "not_visible": "0% mention rate",
                       "insufficient_data": f"< {ANSWERABILITY_MIN_PROMPTS} analyzed prompts"},
        "primary_metric": "mention_rate",
        "counts": {"visible": len(visible), "partially_visible": len(partial),
                   "not_visible": len(not_visible), "unknown": len(unknown)},
        "visible": visible,
        "partially_visible": partial,
        "not_visible": not_visible,
        "unknown": unknown,
    }


# ============================== 3. Content gaps ==============================
def build_content_gaps(summary: dict) -> list[dict]:
    """Zero-mention prompts with their EXISTING grounded gap analysis surfaced verbatim.
    When no grounded gap exists (or it reported no signal), the entry is flagged
    `insufficient_evidence` and carries no invented reason — the UI must say so plainly."""
    gaps = []
    for row in summary.get("per_prompt") or []:
        if not row.get("is_gap"):
            continue
        gap = row.get("gap") or {}
        grounded = bool(gap.get("has_signal"))
        gaps.append({
            "prompt_id": row.get("prompt_id"),
            "prompt": row.get("text"),
            "samples": row.get("samples"),
            "mention_rate": row.get("mention_rate"),
            "recommended_entities": row.get("recommended_entities") or [],
            "why": gap.get("why") if grounded else None,
            "actions": (gap.get("actions") or []) if grounded else [],
            "has_signal": grounded,
            "insufficient_evidence": not grounded,
        })
    return gaps


# ============================== 4. Competitor intelligence ==============================
def build_competitor_intelligence(summary: dict) -> dict:
    """Your brand vs competitors from the existing leaderboard. Head-to-head is stated as
    evidence ("competitor appeared while your brand was absent"), never as "competitor won"."""
    board = summary.get("leaderboard") or []
    tracked_flag = {(_lc(c.get("name"))): bool(c.get("tracked")) for c in (summary.get("competitors") or [])}
    text_by_id = {r.get("prompt_id"): r.get("text") for r in (summary.get("per_prompt") or [])}
    gap_ids = {r.get("prompt_id") for r in (summary.get("per_prompt") or []) if r.get("is_gap")}

    you = next((e for e in board if e.get("is_you")), None)
    brand = None
    if you is not None:
        brand = {
            "name": you.get("name"), "rank": you.get("rank"),
            "appearance_rate": you.get("appearance_rate"),
            "average_position": you.get("average_position"),
            "prev_rank": you.get("prev_rank"), "rank_delta": you.get("rank_delta"),
            "mention_rate": summary.get("mention_rate"),
        }

    competitors, head_to_head = [], []
    for e in board:
        if e.get("is_you"):
            continue
        competitors.append({
            "name": e.get("name"), "domain": e.get("domain"),
            "appearances": e.get("appearances"), "prompt_coverage": e.get("prompt_coverage"),
            "appearance_rate": e.get("appearance_rate"), "average_position": e.get("average_position"),
            "head_to_head": e.get("head_to_head"), "rank": e.get("rank"),
            "prev_rank": e.get("prev_rank"), "rank_delta": e.get("rank_delta"),
            "tracked": tracked_flag.get(_lc(e.get("name")), False),
        })
        if (e.get("head_to_head") or 0) > 0:
            # The actual prompts where this competitor appeared AND the brand was absent.
            prompts = [{"prompt_id": pid, "prompt": text_by_id.get(pid)}
                       for pid in (e.get("prompt_ids") or []) if pid in gap_ids]
            head_to_head.append({
                "competitor": e.get("name"),
                "count": e.get("head_to_head"),
                "statement": (f"{e.get('name')} appeared in {e.get('head_to_head')} tracked "
                              f"answer{'' if e.get('head_to_head') == 1 else 's'} while your "
                              f"brand was absent."),
                "prompts": prompts,
            })
    return {
        "brand": brand,
        "competitors": competitors,
        "head_to_head": head_to_head,
        "leaderboard_excluded": summary.get("leaderboard_excluded") or 0,
        "leaderboard_min_appearances": summary.get("leaderboard_min_appearances"),
    }


def _lc(s):
    return (s or "").strip().lower()


# ============================== 5+6. Opportunity finder ==============================
def _priority_from_impact(impact: float) -> str:
    if impact >= IMPACT_CRITICAL:
        return "Critical"
    if impact >= IMPACT_HIGH:
        return "High"
    if impact >= IMPACT_MEDIUM:
        return "Medium"
    return "Low"


def build_opportunities(summary: dict, scan_report: dict | None = None,
                        phase4: dict | None = None) -> dict:
    """Deterministic AEO Opportunity Finder aggregating independent sources. Each
    opportunity's numeric "Opportunity Impact" (0–100) is normalised WITHIN ITS OWN source
    scale, so no single source can dominate by accident, and a source with no data simply
    produces no opportunities (never a fabricated value). See _impact_* for each formula.

    `phase4` (Phase 4, optional) is the SAME `report["phase4"]` block `/reports/{id}`
    embeds — Schema/Internal-Link/Entity intelligence + scan-only Question Mining. Its
    opportunities link back to the EXISTING `schema`/`links` recommendation ids rather
    than inventing new ones (see `related_recommendation_id` below).

    Opportunities are de-duplicated by stable id and sorted by priority
    (Critical→Low) then Opportunity Impact descending. Items with no measurable impact
    (`impact is None`) sort last within their priority tier rather than being fabricated
    a number."""
    opps: dict[str, dict] = {}

    def add(o):
        if o["id"] not in opps:            # stable id → duplicate prevention
            opps[o["id"]] = o

    for o in _score_loss_opps(scan_report):
        add(o)
    for o in _answer_gap_opps(summary):
        add(o)
    for o in _competitor_opps(summary):
        add(o)
    o = _citation_opp(summary)
    if o:
        add(o)
    breakdown_by_id = {r.get("signal_id"): r for r in
                       (((scan_report or {}).get("insights") or {}).get("score_breakdown") or [])}
    for o in _schema_opps(phase4, breakdown_by_id):
        add(o)
    for o in _link_opps(phase4, breakdown_by_id):
        add(o)
    o = _entity_opp(phase4)
    if o:
        add(o)
    for o in _crawl_graph_opps(scan_report):
        add(o)
    for o in _content_intelligence_opps(scan_report):
        add(o)
    for o in _question_mining_opps(summary, phase4):
        add(o)

    items = sorted(opps.values(),
                   key=lambda x: (_PRIORITY_RANK.get(x["priority"], 9),
                                  -(x["impact"] if x["impact"] is not None else -1), x["id"]))

    # Root-cause grouping (redundancy cleanup): additive only — every item above is
    # returned unchanged in `items`; this only annotates which ones share a real,
    # already-computed recommendation link and marks one PRIMARY per group so the UI
    # can show one actionable card with the rest as supporting evidence, never fewer
    # opportunities or less evidence than before.
    groups, is_primary = _compute_opportunity_groups(items)
    root_cause_by_id = {oid: g["root_cause_id"] for g in groups for oid in g["opportunity_ids"]}
    for o in items:
        o["is_primary"] = is_primary.get(o["id"], True)
        o["root_cause_id"] = root_cause_by_id.get(o["id"])

    return {"impact_label": "Opportunity Impact", "count": len(items), "items": items,
           "groups": groups}


# --- A. score loss (from the linked scan report) ---
def _impact_score_loss(points_lost: float) -> float:
    # A signal's max points_lost is its weight (heaviest signal = 15) → normalise to 100.
    return round(min(100.0, (points_lost or 0) / 15.0 * 100.0), 1)


def _score_loss_opps(scan_report: dict | None) -> list[dict]:
    if not scan_report:
        return []
    breakdown = ((scan_report.get("insights") or {}).get("score_breakdown")) or []
    recs = {r.get("id"): r for r in (scan_report.get("recommendations") or [])}
    url = scan_report.get("url")
    out = []
    for row in breakdown:
        if (row.get("points_lost") or 0) <= 0:
            continue
        sid = row.get("signal_id")
        rec = recs.get(sid)
        impact = _impact_score_loss(row.get("points_lost"))
        priority = (rec.get("priority") if rec else None) or _priority_from_impact(impact)
        action = None
        if rec:
            fix = (rec.get("fix_template") or {}).get("recommended_fix") or []
            action = fix[0] if fix else rec.get("description")
        out.append({
            "id": f"score_loss:{sid}",
            "type": "score_loss",
            "source": "scanner",
            "title": f"Improve {row.get('label')}",
            "description": (rec.get("description") if rec
                            else f"{row.get('label')} scored {row.get('score')}/100."),
            "priority": priority,
            "impact": impact,
            "evidence": {"signal_score": row.get("score"), "weight": row.get("weight"),
                         "points_lost": row.get("points_lost"),
                         "issues": (row.get("issues") or [])[:3]},
            "affected_prompts": [],
            "affected_urls": [url] if url else [],       # the audited page — not invented
            "recommended_action": action,
            "related_recommendation_id": sid if rec else None,
        })
    return out


# --- B. answer gaps (zero-mention prompts) ---
def _answer_gap_opps(summary: dict) -> list[dict]:
    out = []
    for row in summary.get("per_prompt") or []:
        if not row.get("is_gap"):
            continue
        mr = row.get("mention_rate")
        impact = round(100.0 - (mr or 0.0), 1)          # mention gap: 0% mentions → 100 impact
        gap = row.get("gap") or {}
        grounded = bool(gap.get("has_signal"))
        actions = gap.get("actions") or []
        out.append({
            "id": f"answer_gap:{row.get('prompt_id')}",
            "type": "answer_gap",
            "source": "answer_tracking",
            "title": "Not mentioned in AI answers for a tracked query",
            "description": (gap.get("why") if grounded
                            else "Your brand was not mentioned in any answer for this query."),
            "priority": _priority_from_impact(impact),
            "impact": impact,
            "evidence": {"samples": row.get("samples"), "mention_rate": mr,
                         "recommended_entities": row.get("recommended_entities") or [],
                         "has_grounded_analysis": grounded},
            "affected_prompts": [{"prompt_id": row.get("prompt_id"), "prompt": row.get("text")}],
            "affected_urls": [],
            "recommended_action": actions[0] if (grounded and actions) else None,
        })
    return out


# --- C. competitor head-to-head (competitor appeared, brand absent) ---
def _competitor_opps(summary: dict) -> list[dict]:
    board = summary.get("leaderboard") or []
    text_by_id = {r.get("prompt_id"): r.get("text") for r in (summary.get("per_prompt") or [])}
    gap_ids = {r.get("prompt_id") for r in (summary.get("per_prompt") or []) if r.get("is_gap")}
    total_prompts = len({r.get("prompt_id") for r in (summary.get("per_prompt") or [])}) or 1
    out = []
    for e in board:
        h2h = e.get("head_to_head") or 0
        if e.get("is_you") or h2h <= 0:
            continue
        impact = round(min(100.0, h2h / total_prompts * 100.0), 1)   # share of prompts you're absent & they appear
        prompts = [{"prompt_id": pid, "prompt": text_by_id.get(pid)}
                   for pid in (e.get("prompt_ids") or []) if pid in gap_ids]
        out.append({
            "id": f"competitor_gap:{_lc(e.get('name'))}",
            "type": "competitor_gap",
            "source": "answer_tracking",
            "title": f"{e.get('name')} appears where your brand doesn't",
            "description": (f"{e.get('name')} appeared in {h2h} tracked answer"
                            f"{'' if h2h == 1 else 's'} while your brand was absent."),
            "priority": _priority_from_impact(impact),
            "impact": impact,
            "evidence": {"head_to_head": h2h, "appearance_rate": e.get("appearance_rate"),
                         "rank": e.get("rank"), "average_position": e.get("average_position")},
            "affected_prompts": prompts,
            "affected_urls": [],
            "recommended_action": None,
        })
    return out


# --- D. citation gap (only when citation data is actually available) ---
def _citation_opp(summary: dict) -> dict | None:
    diag = summary.get("citation_diagnosis") or {}
    # Only a genuine gap: a capable provider searched, but the brand was NOT cited.
    if diag.get("status") != "searched_not_cited":
        return None
    search_samples = summary.get("citation_search_samples") or 0
    if search_samples <= 0:
        return None
    # Every searched sample missed a brand citation (citation_count is 0 in this status).
    impact = round(min(100.0, search_samples / search_samples * 100.0), 1)   # = 100.0
    return {
        "id": "citation_gap:run",
        "type": "citation_gap",
        "source": "answer_tracking",
        "title": "AI answers search the web but don't cite your site",
        "description": ("A search-capable assistant answered with web search on, but did not "
                        "cite any of your URLs."),
        "priority": _priority_from_impact(impact),
        "impact": impact,
        "evidence": {"citation_search_samples": search_samples,
                     "citation_count": summary.get("citation_count") or 0,
                     "status": diag.get("status")},
        "affected_prompts": [],
        "affected_urls": [],
        "recommended_action": None,
    }


# --- E. Phase 4: schema gaps (linked to the EXISTING `schema` recommendation) ---
def _schema_opps(phase4: dict | None, breakdown_by_id: dict | None = None) -> list[dict]:
    """One opportunity per missing schema type. Reuses the SAME weighted points_lost
    already computed for `score_loss:schema` (`insights.score_loss_breakdown` — real
    weight × score-gap math) via `_impact_score_loss` — the scanner scores schema as
    ONE signal, so it cannot truthfully attribute a DIFFERENT impact number to each
    missing type; every missing-type item here shares that one real, evidence-backed
    impact rather than inventing a per-type split or a raw (unweighted) score gap."""
    schema = (phase4 or {}).get("schema") or {}
    missing = schema.get("missing_types") or []
    if not missing:
        return []
    points_lost = ((breakdown_by_id or {}).get("schema") or {}).get("points_lost", 0)
    impact = _impact_score_loss(points_lost)
    priority = "High" if any(m["type"] == "Organization" for m in missing) else _priority_from_impact(impact)
    out = []
    for m in missing:
        out.append({
            "id": f"schema_gap:{m['type'].lower()}",
            "type": "schema_opportunity",
            "source": "scanner",
            "title": f"Missing {m['type']} structured data",
            "description": m["why_it_matters"],
            "priority": priority,
            "impact": impact,
            "evidence": {"missing_type": m["type"], "signal_score": schema.get("signal_score")},
            "affected_prompts": [],
            "affected_urls": m.get("affected_urls") or [],
            "recommended_action": m["recommended_action"],
            "related_recommendation_id": schema.get("related_recommendation_id"),
        })
    return out


# --- F. Phase 4: internal-link opportunities (linked to the EXISTING `links` rec) ---
def _link_opps(phase4: dict | None, breakdown_by_id: dict | None = None) -> list[dict]:
    links = (phase4 or {}).get("links") or {}
    issues = links.get("issues") or []
    if not issues:
        return []
    points_lost = ((breakdown_by_id or {}).get("links") or {}).get("points_lost", 0)
    impact = _impact_score_loss(points_lost)
    out = []
    for iss in issues:
        out.append({
            "id": f"link_opportunity:{iss['type']}",
            "type": "internal_link_opportunity",
            "source": "scanner",
            "title": iss["label"],
            "description": iss["detail"],
            "priority": "High" if iss["type"] == "potentially_isolated_page" else _priority_from_impact(impact),
            "impact": impact,
            "evidence": {"issue_type": iss["type"], "signal_score": links.get("signal_score")},
            "affected_prompts": [],
            "affected_urls": iss.get("affected_urls") or [],
            "recommended_action": iss["recommended_action"],
            "related_recommendation_id": links.get("related_recommendation_id"),
        })
    return out


# --- G. Phase 4: entity signal completeness (linked to the EXISTING `schema` rec) ---
def _entity_opp(phase4: dict | None) -> dict | None:
    """ONE opportunity summarising missing entity signals (not one per signal — there
    is no scanner score to sub-divide). Impact is a deterministic coverage metric over
    a fixed checklist (see reports.phase4._ENTITY_CHECKS), NOT a Knowledge-Graph or
    ranking claim."""
    entity = (phase4 or {}).get("entity") or {}
    missing = entity.get("missing_signals") or []
    if not missing:
        return None
    impact = round(100.0 - (entity.get("completeness_pct") or 100.0), 1)
    if impact <= 0:
        return None
    action = {
        "has_entity_schema": "Add Organization or LocalBusiness JSON-LD.",
        "has_name": "Add a `name` field to your entity JSON-LD.",
        "has_url": "Add a `url` field to your entity JSON-LD.",
        "has_logo": "Add a `logo` field to your entity JSON-LD.",
        "has_sameas": "Add `sameAs` links to your own verified profiles, if any exist.",
        "has_website_schema": "Add WebSite JSON-LD.",
    }.get(missing[0])
    total_checks = len(entity.get("checks") or {}) or len(missing)
    return {
        "id": "entity_gap:primary",
        "type": "entity_opportunity",
        "source": "scanner",
        "title": "Incomplete entity signals",
        "description": (f"Your site's structured-data entity information is missing "
                        f"{len(missing)} of {total_checks} checked signal(s)."),
        "priority": _priority_from_impact(impact),
        "impact": impact,
        "evidence": {"missing_signals": missing, "completeness_pct": entity.get("completeness_pct")},
        "affected_prompts": [],
        "affected_urls": entity.get("affected_urls") or [],
        "recommended_action": action,
        "related_recommendation_id": entity.get("related_recommendation_id"),
    }


# --- G2. Real Crawl Graph: orphan / disconnected / deep pages (linked to the
# EXISTING `links` rec, same root cause bucket as the Internal-Link opportunities
# above — deliberately reused rather than inventing a new root-cause label) ---
_CRAWL_GRAPH_ISSUE_META = {
    "ORPHAN_PAGE": ("Page has no inbound internal links",
                    "Add contextual internal links from other pages to bring this page "
                    "into your site's link structure."),
    "DISCONNECTED_PAGE": ("Content is disconnected from your crawl seed",
                          "Link to this page (directly or indirectly) from your homepage "
                          "or main navigation so it's part of your connected site structure."),
    "DEEP_PAGE": ("Deep page has weak internal discoverability",
                 "Add a link to this page from a page closer to your homepage."),
}


def _crawl_graph_opps(scan_report: dict | None) -> list[dict]:
    """One opportunity per real, non-empty crawl-graph issue category (orphan pages /
    disconnected pages / deep pages) — never one per URL, mirroring `_link_opps`'s
    one-per-issue-type shape. Impact is the share of the crawl's OWN pages affected
    (grounded in the real graph, like `_competitor_opps`/`_citation_opp`'s ratio-based
    impact) — never a fabricated authority/ranking number. HIGH_OUTBOUND_PAGE /
    WEAK_INBOUND_COVERAGE / anchor-pattern issues are informational graph metrics, not
    surfaced as opportunities here (see reports/crawl_graph.py's own issue list for
    those)."""
    graph = (scan_report or {}).get("crawl_graph") or {}
    if graph.get("available") is not True:
        return []
    total_pages = (graph.get("summary") or {}).get("pages") or 0
    if total_pages <= 0:
        return []
    by_code = {i["code"]: i for i in (graph.get("issues") or [])}
    out = []
    for code, (title, action) in _CRAWL_GRAPH_ISSUE_META.items():
        iss = by_code.get(code)
        if not iss:
            continue
        impact = round(min(100.0, iss["count"] / total_pages * 100.0), 1)
        if impact <= 0:
            continue
        out.append({
            "id": f"crawl_graph:{code.lower()}",
            "type": "crawl_graph_opportunity",
            "source": "scanner",
            "title": title,
            "description": iss["label"],
            "priority": _priority_from_impact(impact),
            "impact": impact,
            "evidence": {"issue_code": code, "affected_page_count": iss["count"],
                        "total_crawled_pages": total_pages},
            "affected_prompts": [],
            "affected_urls": iss.get("affected_urls") or [],
            "recommended_action": action,
            "related_recommendation_id": "links",
        })
    return out


# --- G3. Content Cannibalization & Duplicate Content Intelligence (linked to the
# EXISTING `content`/`metadata` recs — same grouping infrastructure reused) ---
_CLUSTER_TITLE = {
    "near_duplicate": "Near-duplicate content across multiple pages",
    "potential_cannibalization": "Potential cannibalization — overlapping topic/intent",
    "content_overlap": "Content overlap between related pages",
}

# Deterministic impact per (cluster type, confidence) — grounded in the SAME
# type/confidence the cluster's own evidence already computed (reports/
# content_intelligence.py), never a fabricated traffic/ranking estimate.
_CLUSTER_IMPACT = {
    ("near_duplicate", "high"): 70.0, ("near_duplicate", "medium"): 55.0,
    ("potential_cannibalization", "high"): 65.0, ("potential_cannibalization", "medium"): 45.0,
    ("content_overlap", "high"): 35.0, ("content_overlap", "medium"): 25.0,
}


def _content_intelligence_opps(scan_report: dict | None) -> list[dict]:
    """One opportunity per real cluster (never per evidence line — a cluster IS
    already the grouped unit), plus ONE aggregate opportunity per duplicate-element
    type and one for thin-content candidates. Reuses the EXISTING `content`/
    `metadata` recommendation ids so these group with any existing score-loss
    opportunity for those same signals (see _ROOT_CAUSE_LABEL) — never a new root
    cause. Never claims confirmed Google search-result cannibalization."""
    ci = (scan_report or {}).get("content_intelligence") or {}
    if ci.get("available") is not True:
        return []
    total_pages = (ci.get("summary") or {}).get("pages_analyzed") or 0
    out: list[dict] = []

    for cluster in ci.get("clusters") or []:
        impact = _CLUSTER_IMPACT.get((cluster["type"], cluster["confidence"]), 30.0)
        out.append({
            "id": cluster["related_opportunity_id"],
            "type": "content_opportunity",
            "source": "scanner",
            "title": _CLUSTER_TITLE.get(cluster["type"], cluster["label"]),
            "description": cluster["recommendation"],
            "priority": _priority_from_impact(impact),
            "impact": impact,
            "evidence": {"cluster_type": cluster["type"], "confidence": cluster["confidence"],
                        "evidence": cluster["evidence"], "canonical_situation": cluster["canonical_situation"]},
            "affected_prompts": [],
            "affected_urls": [p["url"] for p in cluster["pages"]],
            "recommended_action": cluster["recommendation"],
            "related_recommendation_id": "content",
        })

    if total_pages > 0:
        for etype, rec_id, title in (
            ("duplicate_title", "metadata", "Duplicate page titles"),
            ("duplicate_h1", "content", "Duplicate H1 headings"),
            ("duplicate_meta_description", "metadata", "Duplicate meta descriptions"),
        ):
            groups = [e for e in (ci.get("duplicate_elements") or []) if e["type"] == etype]
            if not groups:
                continue
            urls = sorted({u for g in groups for u in g["urls"]})
            impact = round(min(100.0, len(urls) / total_pages * 100.0), 1)
            noun = etype.replace("duplicate_", "").replace("_", " ")
            out.append({
                "id": f"content_duplicate_element:{etype}",
                "type": "content_opportunity",
                "source": "scanner",
                "title": title,
                "description": f"{len(urls)} page(s) share {len(groups)} duplicate {noun} value(s).",
                "priority": _priority_from_impact(impact),
                "impact": impact,
                "evidence": {"groups": groups},
                "affected_prompts": [],
                "affected_urls": urls,
                "recommended_action": f"Give each page its own distinct {noun}.",
                "related_recommendation_id": rec_id,
            })

        thin = ci.get("thin_pages") or []
        if thin:
            impact = round(min(100.0, len(thin) / total_pages * 100.0), 1)
            out.append({
                "id": "content_thin_pages",
                "type": "content_opportunity",
                "source": "scanner",
                "title": "Thin-content pages relative to the rest of the site",
                "description": (f"{len(thin)} page(s) have unusually low word count compared to "
                                "this site's own median."),
                "priority": _priority_from_impact(impact),
                "impact": impact,
                "evidence": {"pages": thin},
                "affected_prompts": [],
                "affected_urls": [p["url"] for p in thin],
                "recommended_action": ("Expand these pages with substantive, unique content, or "
                                       "consolidate them into a more complete page."),
                "related_recommendation_id": "content",
            })
    return out


# --- H. Phase 4: question mining — real content questions not yet tracked ---
def _question_mining_opps(summary: dict, phase4: dict | None) -> list[dict]:
    """Cross-references Phase 4's scan-only questions (FAQ schema / headings) against
    the Answer Tracking prompts ALREADY being run (`summary.per_prompt`). A zero-mention
    TRACKED prompt is already an `answer_gap` opportunity above — this only covers real
    questions found in the page's own content that aren't tracked at all yet, so there
    is no mention-rate to measure. Impact is deliberately left unavailable (None) rather
    than fabricated; these sort last within their priority tier."""
    questions = ((phase4 or {}).get("questions") or {}).get("questions") or []
    if not questions:
        return []
    tracked = {(r.get("text") or "").strip().lower() for r in (summary.get("per_prompt") or [])}
    out = []
    for q in questions:
        key = (q.get("text") or "").strip().lower()
        if not key or key in tracked:
            continue
        out.append({
            "id": f"question_opportunity:{key}",
            "type": "question_opportunity",
            "source": "question_mining",
            "title": "Real question not yet tracked in Answer Tracking",
            "description": q["text"],
            "priority": "Low",
            "impact": None,        # no mention-rate data exists for an untracked question
            "evidence": {"question_source": q.get("source"), "category": q.get("category")},
            "affected_prompts": [],
            "affected_urls": [q["affected_url"]] if q.get("affected_url") else [],
            "recommended_action": ("Track this question in Answer Tracking to measure "
                                   "whether AI assistants mention you for it."),
            "related_recommendation_id": None,
        })
    return out


# ============================== 11. Trend signal ==============================
def build_trend_signal(trend: dict | None) -> dict:
    """Classify the mention-rate trend across the existing trend series as improving /
    declining / stable (±TREND_EPS points). Never claims causation, and flags when a
    model/search configuration change makes the last two runs not directly comparable."""
    runs = (trend or {}).get("runs") or []
    if len(runs) < 2:
        return {"direction": "insufficient_history", "runs": runs}
    cur, prev = runs[-1], runs[-2]
    cr, pr = cur.get("mention_rate"), prev.get("mention_rate")
    config_changed = bool(cur.get("model_changed") or cur.get("search_changed"))
    if cr is None or pr is None:
        direction, delta = "unknown", None
    else:
        delta = round(cr - pr, 1)
        direction = ("improving" if delta > TREND_EPS
                     else "declining" if delta < -TREND_EPS else "stable")
    return {"direction": direction, "delta": delta, "config_changed": config_changed,
            "runs": runs}


# ============================== compose ==============================
def build_ai_visibility(summary: dict, *, scan_report: dict | None = None,
                        trend: dict | None = None) -> dict:
    """Compose the full (ungated) AI Visibility payload. Gating/trimming is applied by the
    endpoint per entitlement — this builder always returns the complete picture."""
    return {
        "run_id": summary.get("run_id"),
        "prompt_set_id": summary.get("prompt_set_id"),
        "visibility": build_visibility_summary(summary),
        "answerability": build_answerability(summary),
        "content_gaps": build_content_gaps(summary),
        "competitor_intelligence": build_competitor_intelligence(summary),
        "opportunities": build_opportunities(summary, scan_report,
                                            (scan_report or {}).get("phase4")),
        "trend": build_trend_signal(trend) if trend is not None else None,
    }


# ============================== entitlement gating (pure) ==============================
def gate_visibility(payload: dict, access: dict) -> dict:
    """Trim the full AI Visibility payload to the caller's entitlements (Phase 3). Free
    (all flags False) gets a negative-first preview with locked counts; Pro gets the full
    picture. No paid-only bodies are sent to a free caller — only counts + previews.
    Pure/deterministic so both the run and report endpoints share one gating rule."""
    full_av = access["ai_visibility"]
    full_ci = access["competitor_intelligence"]
    full_of = access["opportunity_finder"]
    out = {**payload, "entitlements": access,
           "unlocked": full_av and full_ci and full_of}
    if out["unlocked"]:
        return out

    if not full_av:                        # limited overview + answerability + content gaps
        vis = dict(payload["visibility"])
        pp = vis.get("per_provider") or []
        vis["per_provider"], vis["locked_provider_count"] = pp[:2], max(0, len(pp) - 2)
        out["visibility"] = vis

        ans = dict(payload["answerability"]); ans["preview"] = True
        for k in ("visible", "partially_visible", "not_visible", "unknown"):
            ans[k] = (ans.get(k) or [])[:3]
        out["answerability"] = ans

        cg = payload.get("content_gaps") or []
        out["content_gaps"], out["locked_content_gap_count"] = cg[:2], max(0, len(cg) - 2)

        tr = out.get("trend")
        if tr:                             # trend depth is Pro; free keeps direction + 2 points
            tr = dict(tr); tr["runs"] = (tr.get("runs") or [])[-2:]
            out["trend"] = tr

    if not full_ci:                        # limited competitor preview; head-to-head is Pro
        ci = dict(payload["competitor_intelligence"]); ci["preview"] = True
        comps = ci.get("competitors") or []
        ci["competitors"], ci["locked_competitor_count"] = comps[:2], max(0, len(comps) - 2)
        ci["head_to_head"], ci["head_to_head_locked"] = [], True
        out["competitor_intelligence"] = ci

    if not full_of:                        # top 3 opportunities + locked count
        op = dict(payload["opportunities"]); op["preview"] = True
        items = op.get("items") or []
        # Prefer PRIMARY (non-redundant) opportunities for the free preview — a locked
        # caller's 3 slots shouldn't all be spent restating one root cause — then
        # re-derive groups/primary flags over ONLY the visible subset, so a free
        # response's `groups` never references a locked opportunity id.
        shown = _select_preview_items(items, 3)
        sub_groups, sub_primary = _compute_opportunity_groups(shown)
        shown = [{**o, "is_primary": sub_primary.get(o["id"], True)} for o in shown]
        op["items"], op["locked_count"] = shown, max(0, len(items) - len(shown))
        op["groups"] = sub_groups
        out["opportunities"] = op

    return out


# ------------------------- entitlement gating: Answer Tracking summary -------------------------
def gate_run_summary(summary: dict, access: dict) -> dict:
    """Trim the raw Answer Tracking run summary (`aggregation.run_summary`) for the
    Answer Tracking operational page, reusing the SAME granular Phase 3 flags as
    `gate_visibility` — no second paywall. FREE keeps every OPERATIONAL field in full
    for EVERY prompt (mention_rate/is_gap/samples — "did my brand appear" is never
    gated, per-prompt); only the deeper INTELLIGENCE this run summary also carries is
    trimmed, consolidated view of which now lives in AI Visibility:
      - ai_visibility           gates provider-breakdown depth, the per-prompt grounded
                                gap-to-action text, and the cited-URL list
      - competitor_intelligence gates the competitor list and the run-level leaderboard
    Never mutates `summary` — always returns a fresh dict."""
    full_av = bool(access.get("ai_visibility"))
    full_ci = bool(access.get("competitor_intelligence"))
    out = {**summary, "entitlements": access, "unlocked": full_av and full_ci}
    if out["unlocked"]:
        return out

    if not full_av:
        pv = summary.get("per_provider") or []
        out["per_provider"] = pv[:2]
        out["locked_provider_count"] = max(0, len(pv) - 2)

        src_prompts = summary.get("per_prompt") or []
        total_gaps = sum(1 for r in src_prompts if r.get("gap") is not None)
        seen, per_prompt = 0, []
        for row in src_prompts:
            row = dict(row)
            if row.get("gap") is not None:
                seen += 1
                if seen > 2:
                    row["gap"] = {"locked": True}
            per_prompt.append(row)
        out["per_prompt"] = per_prompt
        out["locked_gap_count"] = max(0, total_gaps - 2)

        cu = summary.get("cited_urls") or []
        out["cited_urls"], out["locked_cited_url_count"] = [], len(cu)

    if not full_ci:
        comps = summary.get("competitors") or []
        out["competitors"] = comps[:1]
        out["locked_competitor_count"] = max(0, len(comps) - 1)
        out["leaderboard"], out["leaderboard_locked"] = [], True

    return out


# ============================== Question Bank ==============================
# Consolidates every REAL question AEOMirror already knows about into one grounded
# view — no new data is derived, this only merges what Question Mining (scan FAQ +
# headings) and Answer Tracking (actually-tracked prompts) already computed, plus
# cross-links to opportunities that ALREADY reference the same question. Reuses
# `classify_question`/`normalize_question_key` from reports.phase4 so a question gets
# the identical category regardless of which source(s) it came from.
_SOURCE_LABEL = {"schema_faq": "scan_faq", "page_heading": "scan_heading"}

# A per-question `average_position` is NOT computed anywhere in this codebase today —
# aggregation.run_summary's `average_position` is a RUN-LEVEL average across every
# mentioned sample, not specific to one prompt. Presenting that number as if it were
# this question's own would misrepresent run-level data as question-level evidence,
# so it stays None until a genuine per-prompt aggregation exists.
_NO_ANSWER_TRACKING = {"tracked": False, "mention_rate": None, "is_gap": None,
                       "average_position": None}


def _qb_group(groups: dict, order: list, key: str, text: str) -> dict:
    if key not in groups:
        groups[key] = {
            "question": text, "sources": [], "source_urls": [], "category": None,
            "answer_tracking": dict(_NO_ANSWER_TRACKING),
            "evidence_count": 0, "related_opportunity_ids": [],
        }
        order.append(key)
    return groups[key]


def _opportunity_question_texts(opp: dict) -> list[str]:
    """Every real question TEXT an opportunity already references — never a new one."""
    texts = [p.get("prompt") for p in (opp.get("affected_prompts") or []) if p.get("prompt")]
    if opp.get("type") == "question_opportunity" and opp.get("description"):
        texts.append(opp["description"])   # _question_mining_opps sets description = the question
    return texts


def build_question_bank(phase4_questions: dict | None, summary: dict | None = None,
                        opportunities: dict | None = None, *,
                        max_source_urls: int = 10, max_questions: int = 200) -> dict:
    """Merge Question Mining's scan-derived questions (FAQ schema + headings) with
    Answer Tracking's ACTUALLY-TRACKED prompts into one deduplicated list, then link
    (never duplicate) any opportunity that already references the same question.

    Every item is traceable to at least one real source: `source_urls` are copied
    verbatim from the scan, `answer_tracking` fields are copied verbatim from the run
    summary. A question found in NO source never exists here — there is nothing to
    invent. Deduplication is via `normalize_question_key` (whitespace/case/terminal-
    punctuation only), so genuinely different questions are never merged."""
    groups: dict[str, dict] = {}
    order: list[str] = []

    for q in ((phase4_questions or {}).get("questions") or [])[:max_questions]:
        key = normalize_question_key(q.get("text"))
        if not key:
            continue
        g = _qb_group(groups, order, key, q["text"])
        label = _SOURCE_LABEL.get(q.get("source"), q.get("source"))
        if label and label not in g["sources"]:
            g["sources"].append(label)
        url = q.get("affected_url")
        if url and url not in g["source_urls"] and len(g["source_urls"]) < max_source_urls:
            g["source_urls"].append(url)
        g["category"] = g["category"] or q.get("category")
        g["evidence_count"] += 1

    for row in ((summary or {}).get("per_prompt") or [])[:max_questions]:
        key = normalize_question_key(row.get("text"))
        if not key:
            continue
        g = _qb_group(groups, order, key, row["text"])
        if "answer_tracking" not in g["sources"]:
            g["sources"].append("answer_tracking")
        g["answer_tracking"] = {
            "tracked": True, "mention_rate": row.get("mention_rate"),
            "is_gap": bool(row.get("is_gap")), "average_position": None,
        }
        g["category"] = g["category"] or classify_question(row["text"])
        g["evidence_count"] += 1

    for opp in ((opportunities or {}).get("items") or []):
        for text in _opportunity_question_texts(opp):
            g = groups.get(normalize_question_key(text))
            if g and opp.get("id") and opp["id"] not in g["related_opportunity_ids"]:
                g["related_opportunity_ids"].append(opp["id"])

    items = [groups[k] for k in order]
    # Most-evidenced first (tracked + found-on-page beats found-in-only-one-source),
    # then alphabetical — deterministic, no randomness, stable across identical input.
    items.sort(key=lambda g: (-g["evidence_count"], g["question"].lower()))
    return {"questions": items, "total_count": len(items)}


def gate_question_bank(bank: dict | None, unlocked: bool, *, free_limit: int = 3) -> dict | None:
    """Same server-side-trim model as `insights.gate_recommendations` /
    `phase4.gate_phase4`: a free caller's `questions` list already IS the free preview
    (a few REAL, fully-evidenced questions) plus a locked count — never the full
    dataset hidden behind client-side blur."""
    if not bank or unlocked:
        return bank
    questions = bank.get("questions") or []
    return {"questions": questions[:free_limit], "preview": True,
            "locked_question_count": max(0, len(questions) - free_limit),
            "total_count": bank.get("total_count", len(questions))}


__all__ = [
    "build_visibility_summary", "build_answerability", "build_content_gaps",
    "build_competitor_intelligence", "build_opportunities", "build_trend_signal",
    "build_ai_visibility", "gate_visibility", "gate_run_summary",
    "build_question_bank", "gate_question_bank",
    "VISIBLE_MIN", "ANSWERABILITY_MIN_PROMPTS",
]
