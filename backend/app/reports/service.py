"""Report service (Phase 6): ties the rule-based engine to persistence.

Builds a report from a stored scan, upserts the latest `reports` row, and records
each export in `report_exports` (the export history). Kept separate from the
engine/exporters so the generation logic stays pure and testable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.config import settings
from app.db.models import SCAN_COMPLETED, USAGE_AI_NARRATIVE, Report, ReportExport
from app.reports.engine import REPORT_VERSION, build_report


def _normalize(url: str) -> str:
    return (str(url or "").strip().lower()
            .replace("https://", "").replace("http://", "")
            .replace("www.", "").rstrip("/"))


def scan_to_input(scan_row) -> dict:
    """Shape a Scan ORM row into the dict the engine consumes."""
    r = scan_row.result or {}
    return {
        "scan_id": scan_row.id,
        "url": scan_row.url,
        "domain": _normalize(scan_row.url),
        "overall_score": r.get("overall_score"),
        "scanner_version": r.get("scanner_version"),
        "scanned_at": r.get("scanned_at"),
        "sections": r.get("sections", []),
        # Additive (Technical SEO): this page's raw HTTP/redirect facts, already
        # captured by `fetch()`/persisted by routes_scan — used only for a single-page
        # scan (a bulk scan reads the same facts per-page from `bulk_pages` below).
        "status_code": r.get("status_code"),
        "redirect_chain": r.get("redirect_chain") or [],
        "final_url": r.get("final_url"),
        # Additive (Phase 4): the bulk page list, when this scan is a bulk scan, so
        # Schema/Link Intelligence can summarize across pages using the already-stored
        # per-page `sections_summary` — no new persistence, no scanner change.
        "bulk_pages": ((r.get("bulk") or {}).get("pages")) or None,
        # Additive (Real Crawl Graph): the ORIGINAL user-submitted URL order (distinct
        # from `bulk_pages` above, which is stored in concurrent-fetch COMPLETION order
        # and is therefore not deterministic run-to-run). The crawl graph's "seed" is
        # defined as the first of these — the one deterministic, user-intended notion of
        # a primary URL this flat-list bulk scanner has (see reports/crawl_graph.py).
        "bulk_requested_urls": ((r.get("bulk") or {}).get("urls")) or [],
        # Additive (Phase 4 fix): the CENTRALIZED scan-readiness signal — reuses the
        # existing Scan.status lifecycle (pending/running/completed/failed) rather than
        # inventing a second "is this data real" concept. A single-page scan's row is
        # always created COMPLETED (this is always True for it); a bulk scan's status
        # only becomes COMPLETED once the whole job finishes, so a still-PENDING/RUNNING
        # bulk scan (or one that hit a fatal error) correctly reports as not ready.
        "scan_ready": scan_row.status == SCAN_COMPLETED,
    }


def _ai_narrative(db, report: dict, scan_row, *, tier: str, email_verified: bool) -> dict | None:
    """Generate a grounded AI narrative for this report subject to the cost/abuse
    guardrails, or None (→ rule-based fallback) if AI is off, a guardrail blocks it,
    or generation/validation fails. Records one usage event per SUCCESSFUL call.

    Guardrails: the global daily ceiling applies to every tier; the per-org monthly cap
    and the verified-email requirement apply to the Free tier only. Paid tiers (Pro org
    or a $9 unlock) bypass the per-org cap but still respect the global ceiling."""
    if not settings.ai_enabled:
        return None
    from app.billing import entitlements
    org_id = scan_row.organization_id

    if entitlements.ai_calls_today(db) >= settings.ai_global_daily_calls:
        return None
    if tier == "free":
        if settings.ai_require_verified_email_for_free and not email_verified:
            return None
        if entitlements.ai_narratives_this_month(db, org_id) >= settings.ai_free_monthly_narratives:
            return None

    from app.reports import ai_writer
    fresh = ai_writer.write_narrative(report, tier=tier)
    if not fresh:
        return None
    # One usage row per real Anthropic call (drives both caps); then stamp provenance.
    entitlements.record_usage(db, org_id, USAGE_AI_NARRATIVE)
    fresh["_meta"] = {"model": settings.ai_model, "tier": tier,
                      "generated_at": datetime.now(timezone.utc).isoformat()}
    return fresh


def _latest_report_row(db, scan_id: str) -> Report | None:
    return (db.query(Report)
            .filter(Report.scan_id == scan_id)
            .order_by(Report.generated_at.desc())
            .first())


def _persist_report(db, scan_row, report: dict, row: Report | None) -> Report:
    if row:
        row.version = report["report_version"]
        row.overall_score = report["scorecard"].get("overall_score")
        row.recommendation_count = report["recommendation_count"]
        row.data = report
        row.organization_id = scan_row.organization_id
    else:
        row = Report(
            scan_id=scan_row.id, organization_id=scan_row.organization_id,
            version=report["report_version"],
            overall_score=report["scorecard"].get("overall_score"),
            recommendation_count=report["recommendation_count"], data=report,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_or_build_report(db, scan_row) -> tuple[dict, Report | None]:
    """The single canonical deterministic report for a scan — reused by `generate_report`
    (get_report/exports) AND directly by the read-only insights/AI-visibility endpoints,
    so `build_report()` is never called more than once per fresh computation.

    Cache validity reuses the EXISTING report-version semantics (the same
    `report_version` check `generate_report` already used for the AI narrative) plus the
    scan's own lifecycle status — NOT a second cache/version system:
      - a stored row is reused as-is (no rebuild) only when the scan is COMPLETED and the
        stored `version` matches the current `REPORT_VERSION` engine code;
      - otherwise the deterministic report is rebuilt via `build_report`.
    A rebuilt report is persisted to `reports` ONLY when the scan is COMPLETED — a
    pending/running/failed scan's result can still change, so nothing is ever cached as
    if it were final for it (this is also what makes Phase 4's "scan_incomplete" state
    honest instead of getting silently baked into a stale row).

    Returns a report dict that is always safe to mutate (a fresh dict on every path,
    never the same object as `row.data`) and the `Report` row (`None` when the scan
    isn't ready and no row exists yet)."""
    row = _latest_report_row(db, scan_row.id)
    ready = scan_row.status == SCAN_COMPLETED
    if row and ready and row.version == REPORT_VERSION:
        return dict(row.data), row          # cache hit — build_report is NOT called
    scan_input = scan_to_input(scan_row)
    report = build_report(scan_input)
    if not ready:
        return report, row                  # honest, unpersisted (see docstring)
    row = _persist_report(db, scan_row, report, row)
    return dict(row.data), row


def generate_report(db, scan_row, *, user=None, refresh_ai: bool = False) -> dict:
    """The report for a scan, with the AI narrative merged in and persisted. Returns
    (report_dict, row).

    The deterministic part comes from `get_or_build_report` (the shared cache — see its
    docstring); this function's OWN job is layering the AI narrative on top and
    persisting that layer, which is why it stays separate from the plain read-only
    callers (`/insights`, `/ai-visibility`) that must never trigger an AI call.

    Feature A — AI narrative (ALL plans): an AI-written narrative is merged under
    report["ai"] and PERSISTED on the row. The tier is "paid" when the caller qualifies
    for exports (Pro org or a $9-unlocked report) — full insights + action plan — else
    "free" — executive summary + top-3 insights, subject to the free guardrails.

    Cache: keyed on (scan_id, report_version); a stored narrative is reused and NEVER
    re-calls the API on re-render. EXCEPTION: a cached FREE narrative is regenerated once
    at the paid tier when the caller now qualifies (they just upgraded / bought the $9
    unlock). `refresh_ai` (admin/dev) forces a fresh call regardless. A pending/running
    scan never reaches the AI narrative step at all — there is nothing real to narrate,
    and (per the cache contract above) nothing is persisted for it either."""
    report, row = get_or_build_report(db, scan_row)
    if scan_row.status != SCAN_COMPLETED:
        return report, row

    from app.billing import entitlements
    org_id = scan_row.organization_id
    tier = "paid" if entitlements.export_unlocked(db, org_id, scan_row.id) else "free"
    email_verified = bool(getattr(user, "email_verified", False))

    stored = row.data or {}
    cached_ai = stored.get("ai") if stored.get("report_version") == report["report_version"] else None
    # Regenerate when: forced, nothing cached, or a cached FREE narrative can be upgraded
    # to paid now that the caller qualifies. Otherwise reuse the cache — no API call.
    cached_tier = (cached_ai.get("_meta") or {}).get("tier") if cached_ai else None
    upgrade = bool(cached_ai) and tier == "paid" and cached_tier == "free"
    ai_block = cached_ai
    if settings.ai_enabled and (refresh_ai or not cached_ai or upgrade):
        fresh = _ai_narrative(db, report, scan_row, tier=tier, email_verified=email_verified)
        if fresh:                       # on failure keep whatever was cached (maybe None)
            ai_block = fresh
    if ai_block:
        report["ai"] = ai_block
    if ai_block is not cached_ai:
        # Only a NEW/changed narrative needs a write — `report` is already a fresh dict
        # (see get_or_build_report), so this reassignment is never an in-place mutation
        # of `row.data` (the canonical copy stays untouched until this explicit persist).
        # `row` is guaranteed non-None here: the scan is COMPLETED (checked above), and
        # get_or_build_report always persists a completed scan's deterministic report.
        row = _persist_report(db, scan_row, report, row)
    return report, row


def record_export(db, *, report_id, scan_row, user_id, fmt: str, size: int) -> None:
    db.add(ReportExport(
        report_id=report_id, scan_id=scan_row.id,
        organization_id=scan_row.organization_id, user_id=user_id,
        format=fmt, size_bytes=size,
    ))
    db.commit()


__all__ = ["REPORT_VERSION", "generate_report", "get_or_build_report",
          "record_export", "scan_to_input"]
