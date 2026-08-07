# Phase 6 — Actionable AI Visibility Reports — Completion Report

AEOMirror now turns a raw scan into an **actionable, exportable report**: for every
issue it explains *what's wrong, why it matters, how to fix it, the expected impact,
and its priority*. A website **scorecard** summarizes the whole picture, and reports
export as **PDF, JSON, and CSV**. Everything is **rule-based (no LLM / paid API)** —
deterministic, fast, and free to generate.

Monitoring, scheduled scans, billing, subscriptions, and production deployment were
intentionally **not** started (out of scope for Phase 6).

## 1. Files created

**Backend — `app/reports/` (services are cleanly separated)**
- `templates.py` — structured, rule-based copy per signal (category, business /
  AI-visibility impact, difficulty, fix time, explanation, implementation example,
  expected outcome) + the 9-category map.
- `engine.py` — the **recommendation engine**: severity + priority computation,
  per-issue recommendations, the **scorecard** (overall score, grade, category
  scores, strengths, weaknesses, quick wins, top-10 priorities), and `build_report`.
- `exporters.py` — **JSON** and **CSV** exporters (bytes).
- `pdf.py` — **PDF generator** (reportlab): cover page, executive summary, score
  donut, category bar chart, category table, strengths/weaknesses, top priorities,
  full recommendations, appendix, page furniture + branding.
- `service.py` — ties the engine to persistence (upsert `reports`, record exports).
- `__init__.py`.

**Backend — API / DB / tests**
- `app/api/routes_reports.py` — `GET /reports/{scan_id}` + `/pdf` `/csv` `/json`.
- `alembic/versions/9c2e6b41d7a8_phase6_reports.py` — `reports` + `report_exports`.
- `tests/test_reports.py` — 15 tests.

**Frontend**
- `src/dashboard/ReportView.jsx` — the report experience (scorecard + rich
  recommendation cards + PDF/JSON/CSV downloads + Share placeholder).

## 2. Files modified

- `backend/app/db/models.py` — `Report`, `ReportExport` models.
- `backend/app/main.py` — mounts the reports router; CORS `expose_headers`
  (`Content-Disposition`) so the browser can read download filenames.
- `backend/requirements.txt` — `reportlab==4.2.2`.
- `frontend/src/api.js` — `getReport()` + `downloadReport()` (authenticated
  blob download).
- `frontend/src/dashboard/Dashboard.jsx` — "AI Visibility Report" nav → `ReportView`
  (replaces the basic recommendations list); opens a specific scan's report.
- `frontend/src/dashboard/ScanDetails.jsx` — "View full report" button.
- `frontend/src/dashboard/dashboard.css` — report styles.

## 3. API endpoints

All authenticated and **org-scoped** (a scan outside your org → 404):
- `GET /reports/{scan_id}` — the full rule-based report (JSON).
- `GET /reports/{scan_id}/pdf` — branded PDF download.
- `GET /reports/{scan_id}/csv` — recommendations CSV (Excel-friendly, BOM).
- `GET /reports/{scan_id}/json` — report JSON download.

Each download is recorded in `report_exports` (export history). PDF rendering runs
in a **threadpool** (`run_in_threadpool`) so it never blocks the event loop / scans.

## 4. Report architecture

`scan.result.sections` (the Phase 3 signals) → **engine** builds recommendations +
scorecard → **service** upserts the latest `reports` row (JSON, version, timestamp)
→ **exporters / pdf** turn that report into JSON / CSV / PDF at download time. The
report is generated on demand and cached in the DB; exports never re-run the scan.
The PDF path is the seam where a **future background-job queue** slots in.

## 5. Recommendation engine design

Each issue (any signal that isn't a clean pass) becomes a recommendation with the
required fields: **Issue Title, Severity, Category, Description, Evidence, Business
Impact, AI Visibility Impact, Estimated Fix Time, Difficulty, Priority**, plus a fix
template (**Problem, Explanation, Recommended Fix, Implementation Example, Expected
Outcome**).
- **Categories** (9): Crawlability, Metadata, Schema, Content, Performance,
  Accessibility, Internal Linking, Indexability, AI Readiness.
- **Priority levels**: Critical / High / Medium / Low, computed as
  `weight × (100 − score) / 100` (impact × gap) — the same score ranks the Top-10.
- **Severity** reflects the signal's current state; the signal's own findings feed
  Description/Evidence, and its recommendations become the Recommended-Fix steps.
- Static copy (impact, difficulty, examples) comes from per-signal **templates** —
  no LLM anywhere.

## 6. Export implementation

- **JSON** — the full report, pretty-printed.
- **CSV** — one row per recommendation (priority, severity, category, issue, score,
  difficulty, fix time, impacts, fix, outcome); UTF-8 BOM for Excel.
- **PDF** — reportlab (pure Python, no system libraries): cover, executive summary,
  score donut, category bar chart + table, strengths/weaknesses, top priorities,
  full recommendations with code examples, appendix, footer branding + page numbers.
  Print-friendly (white pages, restrained ink).

## 7. Remaining work for Phase 7

- **Background jobs**: move PDF generation (and future heavy exports) to a worker
  queue; the `run_in_threadpool` seam and stored-report cache are already in place.
- **Share Report**: signed, expiring public links (button is present but disabled).
- Report **diffing** across scans ("what improved since last time").
- Branded PDF **theming / logo upload** per organization.
- Historical **export analytics** from `report_exports`.
- Deferred: monitoring, scheduled scans, billing/subscriptions.

## Verification

- Backend: **108 passed, 1 skipped** (`pytest`). `test_reports.py` covers the
  engine (all required fields, priority sorting, valid categories/levels), the
  scorecard, JSON/CSV/PDF exports, **large / small / missing-data** reports, PDF
  validity, the auth-required + org-scoped endpoints, and export-history recording.
- Live: a real scan of a fixture site produced a grade-F report (8 recommendations,
  9 category scores, 4 quick wins, full fix templates); PDF (28 KB, valid `%PDF-`),
  CSV, and JSON all downloaded with correct filenames. The cover page and report UI
  were visually verified.
- Migration applies and downgrades cleanly; dev DB at head `9c2e6b41d7a8`.
- No LLM/paid API in any path; no secrets logged or committed.
