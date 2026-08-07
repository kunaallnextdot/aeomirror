"""Report service (Phase 6): ties the rule-based engine to persistence.

Builds a report from a stored scan, upserts the latest `reports` row, and records
each export in `report_exports` (the export history). Kept separate from the
engine/exporters so the generation logic stays pure and testable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.config import settings
from app.db.models import USAGE_AI_NARRATIVE, Report, ReportExport
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


def generate_report(db, scan_row, *, user=None, refresh_ai: bool = False) -> dict:
    """Build the report for a scan and upsert the stored `reports` row (one latest per
    scan). Returns (report_dict, row).

    Feature A — AI narrative (ALL plans): an AI-written narrative is merged under
    report["ai"] and PERSISTED on the row. The tier is "paid" when the caller qualifies
    for exports (Pro org or a $9-unlocked report) — full insights + action plan — else
    "free" — executive summary + top-3 insights, subject to the free guardrails.

    Cache: keyed on (scan_id, report_version); a stored narrative is reused and NEVER
    re-calls the API on re-render. EXCEPTION: a cached FREE narrative is regenerated once
    at the paid tier when the caller now qualifies (they just upgraded / bought the $9
    unlock). `refresh_ai` (admin/dev) forces a fresh call regardless."""
    report = build_report(scan_to_input(scan_row))

    row = (db.query(Report)
           .filter(Report.scan_id == scan_row.id)
           .order_by(Report.generated_at.desc())
           .first())

    from app.billing import entitlements
    org_id = scan_row.organization_id
    tier = "paid" if entitlements.export_unlocked(db, org_id, scan_row.id) else "free"
    email_verified = bool(getattr(user, "email_verified", False))

    stored = (row.data or {}) if row else {}
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
    # Return the stored copy so generated_at reflects the row (stable ids downstream).
    return row.data, row


def record_export(db, *, report_id, scan_row, user_id, fmt: str, size: int) -> None:
    db.add(ReportExport(
        report_id=report_id, scan_id=scan_row.id,
        organization_id=scan_row.organization_id, user_id=user_id,
        format=fmt, size_bytes=size,
    ))
    db.commit()


__all__ = ["REPORT_VERSION", "generate_report", "record_export", "scan_to_input"]
