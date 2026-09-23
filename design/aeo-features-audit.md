# AEOMirror — Negative-First AEO Platform: Implementation Audit

**Status:** inspection only — no code changed. Maps the 15 requested product features onto the
existing implementation (present / partial / missing), with exact files, reuse strategy,
architectural conflicts, and a build order.

**Product principles this audit is written against:** never invent/exaggerate problems; every
negative finding must be backed by real scanner evidence; free tier shows enough to build trust;
deeper diagnosis / affected URLs / complete recommendations / competitor intel / implementation
detail stay behind paid gates; never artificially lower scores; never break scoring silently.

---

## A. Existing architecture in one screen

**Two parallel scoring systems run on every scan** (`api/routes_scan.py:229,237`):
1. **Legacy 6-family rubric** → `ars` (kept "for reference" only), DB-versioned weights (`scanner/engine.py`, `families.py`, `rubric_provider.py`).
2. **10 modular signals → `overall_score`** — THE headline AI-Readiness Score (`scanner/signals/aggregate.py:35`, `SCANNER_VERSION 3.0.0`). **All new features must build on this one.**

**The 10 signals** (each already emits `score`, `weight`, `issues[]`, `recommendations[]`, `evidence{}` — never "just a number"; `signals/base.py:37`): robots(10) · sitemap(7) · metadata(12) · schema(15) · content(12) · links(8) · performance(10) · accessibility(9) · freshness(7) · ai_readiness/"AI Extractability"(10). Weights sum to 100. `overall = Σ(score×weight)/Σweight` (`aggregate.py:35`).

**Report engine** (rule-based, deterministic, no LLM: `reports/engine.py:247 build_report`): returns `scorecard` (overall/grade/status/`category_scores`/`strengths`/`weaknesses`/`quick_wins`/`top_priorities`/`issue_counts`/`summary`) + `recommendations[]`. Each recommendation already carries `severity`, `priority`, `priority_score`, `evidence`, `business_impact`, `ai_visibility_impact`, `estimated_fix_time`, `difficulty`, and a full `fix_template{problem,explanation,recommended_fix[],implementation_example,expected_outcome}` (`engine.py:106`, templates in `reports/templates.py`). AI narrative is additive + grounded/validated (`reports/ai_writer.py`, tiered free/paid in `reports/service.py:90`).

**Answer Tracking** (`services/answer_tracking/*`): queries openai/anthropic/perplexity (**gemini is a stub**), LLM-extracts brand mention / sentiment / citations / competitors / recommended-entities / position per answer (`extraction.py`), aggregates mention-rate + per-provider + citation diagnosis + **competitor leaderboard w/ head-to-head + rank deltas + trend** (`aggregation.py:222 run_summary`), and runs **grounded gap-to-action** for zero-mention prompts (`gap_analysis.py`).

**Gating** (`billing/entitlements.py`, `plans.py`): free/pro plans + one-time `$9` per-scan `report` unlock. Enforced as **HTTP 402 + human message** (no server-side reason enum); the frontend picks the reason at the callsite and shows `UpgradeModal` copy. Existing reasons: `scans, monitors, compares, report, page_details, ai_content, share`. Extending it = add a COPY key (`UpgradeModal.jsx:17`) + raise a 402 (backend) — exact seam documented in §D.

**Frontend** = the Aurora design system (`dashboard/aurora.jsx` + `aurora.css`): `au-panel/au-cell/au-bar/Ring/Tag/Button/Metric/ProgressBar`, plus migrated `ReportView.jsx`, `ScanDetails.jsx`, `AnswerTracking.jsx`, `Compare.jsx`, `ContentInsights.jsx`, and `UpgradeModal` (`handleGated`/`openUpgrade`). All new UI reuses these.

---

## B. Feature matrix (present / partial / missing)

Legend: ✅ exists · 🟡 partial · ⛔ missing · *(derive)* = buildable read-side from data that already exists, **no scoring change**.

| # | Feature | Status | Where it already lives / what's missing |
|---|---|---|---|
| 1 | **Negative-first audit** | 🟡 *(framing)* | Data 100% present — `scorecard.weaknesses` (score<45), `issue_counts`, worst-first `sections`, per-signal `evidence`+`issues`. Missing = the **presentation/ordering** (lead with losses+evidence) in the free scanner result + `ReportView`/`ScanDetails`. Pure frontend + thin read-side. |
| 2 | **Why is my score low?** | 🟡 *(derive)* | All inputs exist: worst signals (`sections` sorted by score), each with `issues`/`evidence`/`recommendations`, plus `scorecard.weaknesses`. Missing = a named endpoint/UI. No new scanner logic. |
| 3 | **Score-loss breakdown** | 🟡 *(derive)* | `points_lost = weight×(100−score)/100` per section — every section already carries `weight`+`score` (`aggregate.py`). Confirmed: **no scoring change needed**; a pure read-side ranking. Missing = the computed object + a bar/waterfall UI (reuse `au-bar`). |
| 4 | **Page-level AEO scores** | ✅ (bulk) | Bulk scans already store per-page `overall_score` + `status_label` + `top_issue` + full `sections_summary` in `Scan.result["bulk"]["pages"]` (`routes_scan.py:314`). Free = scores + one-liner; full per-page detail is Pro (`entitlements.can_view_page_details`). Surfaced in `ScanDetails` bulk view. |
| 5 | **Recommendation Center** | ✅ data / 🟡 UI+gate | Full priority-sorted `recommendations[]` with `fix_template`/`severity`/`priority`/`business_impact`/`estimated_fix_time`/`difficulty` already produced (`engine.build_recommendations`), + `scorecard.top_priorities`/`quick_wins`/`issue_counts`. Missing = a dedicated aggregated "center" UI. **Conflict:** today these are FREE on-screen — see §C-2. |
| 6 | **AEO Opportunity Finder** | ⛔ (assemble) | No dedicated opportunity object/endpoint ("effectively absent"). But the ingredients exist: score-loss (high-weight low-score signals), Answer-Tracking `is_gap` prompts + gap `actions`, and leaderboard `head_to_head` (prompts where a competitor appears but you don't). Build = a read-side ranked aggregation over these. |
| 7 | **Content Gap Analyzer** | ✅ (Answer Tracking) | `gap_analysis.py` — for zero-mention prompts, ONE grounded LLM call using the site's own scan findings → `{why, actions[0-4], has_signal}` (`PromptGapAnalysis`), + `is_gap` per prompt. Already grounded in real evidence. Surfaced in `AnswerTracking`. Mostly re-brand/surface. |
| 8 | **Schema Intelligence** | 🟡 | `schema` signal + `scanner/schema_extract.py` detect JSON-LD (state absent/malformed/present, `entity_types`, `detected_types`, `content` flags: Article/FAQ/Product/Breadcrumb/WebSite/LocalBusiness/…) + fix template. Missing = a schema **generator/validator** and entity graph. Diagnostic reuse now; generator net-new. |
| 9 | **Internal Link Intelligence** | 🟡 | `links` signal evidence (internal/external counts, `has_nav`, anchor diversity, generic/empty anchors) + fix template. Missing = **link graph / orphan detection** — needs a multi-page crawl (explicitly not implemented, `links.py:4`). Single-page reuse now; graph net-new (crawl infra). |
| 10 | **AI Answerability** | ✅ (two layers) | (a) heuristic `ai_readiness` signal (JS-shell, thin text, Q&A, llms.txt, JSON-LD types) + `evidence`; (b) real AI-assistant mention/citation rates across providers in Answer Tracking (`aggregation.run_summary`). Both exist. |
| 11 | **Entity Intelligence** | 🟡 | Answer-Tracking `recommended_entities` + `competitors_mentioned` + surface-form normalization (`aggregation.py:102`); schema `entity_types`. Missing = knowledge-graph / `sameAs` linking (explicitly refused). Reuse entities+schema now; KG net-new. |
| 12 | **Question Mining** | 🟡 (scaffold only) | Prompt/PromptSet infra + 3 static brand/domain-seeded starters (`service.suggest_prompts:77`). Missing = real harvesting/clustering/volume/category detection (explicitly absent). Reuse the prompt seam; real mining needs external sources (PAA/keyword APIs) — net-new. |
| 13 | **Competitor Intelligence** | ✅ (Answer Tracking) | `competitors_mentioned` + Share-of-Voice + `tracked` flag + full leaderboard (`head_to_head`, `average_position`, `rank_delta`, `prev_rank`, trend) (`aggregation.py:154,270,404`). Plus scan-to-scan `Compare`. **Conflict:** Answer Tracking is currently NOT plan-gated — §C-3. |
| 14 | **Before/After Score Impact Simulator** | 🟡 *(derive)* | Content-only before/after exists (`ai_content.rewrite_example`). No score projection. But deterministic projection is trivial + grounded: fixing a signal to healthy recovers its `points_lost` (from `weight`). Build = read-side projection labeled "estimated," reusing §2/§3 data. No re-scoring. |
| 15 | **30-day AEO Action Plan** | 🟡 | `ai_writer` already emits an ordered `action_plan` (5–7 steps, **paid-only**, `ai_writer.py:70`); per-rec `estimated_fix_time`+`difficulty`+`quick_wins` give phasing ingredients. Missing = **time-phasing/calendar** (30-day buckets, sequencing). Reuse + a rule-based phaser. |
| — | **Smart free/paid gating** | ✅ mechanism / 🟡 policy | The 402+reason+`UpgradeModal` seam + entitlement matrix + one-time `$9` report are solid and extensible (§D). Missing = the **new free/paid line** for the negative-first strategy (which currently-free things move behind gates) — a product decision, §C-2 + §F. |

**Net:** genuinely *missing as named features* = Opportunity Finder (assemble-able), score-simulator (derive-able), 30-day phasing, schema generator, link graph, real question mining, entity KG. Everything else is **present or a read-side derivation** — the platform's data model is already rich; most of this work is **surfacing + framing + gating**, not new analysis.

---

## C. Conflicts with the current scoring / report architecture

1. **Two scoring systems coexist** (legacy `ars` 6-family vs headline 10-signal `overall_score`). Build every new feature ONLY on the 10-signal `sections`; do not read/echo `ars`, or the numbers will diverge in the UI. (No change to either system.)

2. **⚠️ Biggest tension — recommendations are currently FREE on-screen.** `GET /reports/{id}` returns the full scorecard + all recommendations + `fix_templates` with only `report:view` (paywall today is **exports** + one-time `$9`, not the recommendations themselves). The negative-first strategy ("gate complete recommendations / implementation detail") means **moving currently-free value behind a gate.** This must be an explicit, documented product decision (principle: don't silently reduce free value) — teaser free, full behind paid — not a silent change. See §F.

3. **Answer Tracking is NOT plan-gated** (only operational guards: dedup interval + monthly run cap → HTTP 429). Competitor Intelligence / Content Gap / Opportunity Finder / Question Mining built on it will need **new plan gates** to be paid features. Decision required.

4. **AI features are capped + grounded** (daily ceiling, per-org monthly free cap, verified-email; `ai_writer._validate` drops any unverifiable claim). Any *new* AI-driven feature (opportunity finder, richer action plan, content simulator) MUST route through the same FACTS-grounding + validation + caps to honor "never invent problems." Do not add ungrounded LLM calls.

5. **The report object is a raw dict, not a Pydantic model**, cached in `Report.data`; `report_version=1.0.0`, `SCANNER_VERSION=3.0.0`; `snapshot_diff` refuses cross-version diffs. → New report fields must be **additive only** (never rename/reshape existing keys) to avoid breaking cached reports, exports, public-share whitelist (`shares.py:126`), and snapshot diffs.

6. **Score-loss + simulator must be presented as derivations/estimates, not re-scores** — compute from existing `weight`/`score`, label "projected/estimated," and never write back into `overall_score`. This satisfies "don't artificially lower scores / don't break scoring."

---

## D. Recommended reuse strategy

- **A read-side "analysis" layer** over the persisted `Scan.result["sections"]` (+ `["bulk"]["pages"]`) powers features 1,2,3,4,14 with **zero scanner/scoring change**: score-loss ranking, why-low, negative-first ordering, page scores, deterministic simulator. Add as a small pure module (e.g. `reports/insights.py`) or compute in `engine.build_report` as additive scorecard fields.
- **Recommendation Center (5) + Action Plan (15)**: reuse `engine.build_recommendations` + `scorecard.top_priorities/quick_wins` + per-rec `estimated_fix_time`/`difficulty`; add a rule-based 30-day phaser and (paid) reuse `ai_writer.action_plan`.
- **Answer-Tracking features (6,7,10,11,12,13)**: reuse `aggregation.run_summary` (mention-rate, per-provider, citations, leaderboard) + `gap_analysis` + `recommended_entities`; extend `service.suggest_prompts` for question mining; add a read-side opportunity aggregator combining score-loss + `is_gap` + `head_to_head`.
- **Schema (8) / Link (9) Intelligence**: reuse `schema_extract`/`links` evidence for diagnostic views now; a schema generator + a link-graph (multi-page crawl) are net-new and larger.
- **Gating (16)**: reuse the seam — **frontend** add a `COPY` key in `UpgradeModal.jsx:17` (+ optional `PRO_FEATURES` bullet) and call `handleGated(err, "<reason>")` / `openUpgrade("<reason>", {scanId})`; **backend** raise `HTTPException(402, detail=…)` (mirror `routes_dashboard.py:172` for a Pro-only feature, or the `compare_quota` metered pattern), adding an entitlement flag to `plans.py:66` + `ALL_ACCESS` when needed. One-time `$9` unlock uses reason `report` + `{scanId}`.
- **Frontend**: every surface is a new Aurora section/screen built from `au-panel/au-cell/au-bar/Ring/Tag/Button/Metric` + the gated-teaser pattern already in `ContentInsights`/`ReportView` (blurred/locked preview → `openUpgrade`).

---

## E. Recommended implementation order

**Phase 0 — Decide the free/paid line (§F).** Prerequisite for anything gated; document it. No code.

**Phase 1 — Negative-first core (read-side only, highest trust value, zero scoring risk).**
1. Score-loss breakdown + "Why is my score low?" — additive `scorecard` fields (`points_lost` per section, worst-first) via the read-side layer.
2. Negative-first presentation — reorder the free scanner result + `ReportView`/`ScanDetails` to lead with losses + evidence (frontend).
3. Page-level AEO scores — surface existing `bulk.pages` cleanly + confirm the free/Pro detail split.
4. Recommendation Center — aggregate existing `recommendations[]` into one view + apply the Phase-0 teaser/gate.

**Phase 2 — Deterministic projections (still no scoring change).**
5. Before/After Score Impact Simulator — rule-based points-recovered projection (+ optional content rewrite reuse).
6. 30-day Action Plan — phase `quick_wins`/recs by effort into 30-day buckets; paid tier layers in `ai_writer.action_plan`.

**Phase 3 — Answer-Tracking surfacing + gating (reuse existing data).**
7. AI Answerability + Competitor Intelligence + Content Gap Analyzer as first-class (gated) features from `run_summary`/leaderboard/gap.
8. AEO Opportunity Finder — ranked aggregation of score-loss + `is_gap` + `head_to_head`.

**Phase 4 — Deeper intelligence (some net-new infra; largest).**
9. Schema Intelligence (diagnostic now → generator/validator later).
10. Internal Link Intelligence (evidence now → link-graph/orphan via multi-page crawl).
11. Entity Intelligence (AT entities + schema now → KG/`sameAs` later).
12. Question Mining (real harvesting/clustering — needs external question/keyword sources).

**Cross-cutting:** Smart free/paid gating is implemented incrementally with each feature via the §D seam — not as a separate phase.

---

## F. The free/paid decision to make first (Phase 0)

Because on-screen recommendations are free today and Answer Tracking isn't plan-gated, the negative-first strategy needs an explicit line. Proposed default (to confirm), honoring "show enough to build trust":

- **Free (trust builders):** overall score + grade; the **score-loss breakdown** (which signals cost you the most) with evidence; count of critical/high issues; the **top 2–3** recommendations in full; per-*site* single-page detail.
- **Paid (Pro or one-time `$9` report):** the **complete** recommendation set + `fix_template` implementation detail; **affected-URL lists** (bulk per-page detail); Before/After simulator; 30-day action plan; exports.
- **Pro-only (subscription):** Competitor Intelligence / leaderboard, Content Gap Analyzer, Opportunity Finder, Answer-Tracking depth, monitoring alerts.

This keeps every free negative finding **evidence-backed and real** (never invented, never score-lowered) while reserving depth, URLs, competitor intel, and implementation detail for paid — exactly the requested strategy.
