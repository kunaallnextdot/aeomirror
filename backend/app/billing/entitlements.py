"""Entitlements (Phase 9): resolve an organization's effective plan and what it may
do. When billing enforcement is off (settings.billing_enforced=False) every org gets
full access — subscriptions/payments still record, only gating is bypassed.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.billing.plans import ALL_ACCESS, ENTITLEMENTS, PLAN_FREE, PLAN_PRO
from app.config import settings
from app.db.models import (
    KIND_ONE_TIME_REPORT, PAY_SUCCEEDED, SUB_ACTIVE, SUB_PAST_DUE, USAGE_AI_NARRATIVE,
    USAGE_COMPARE, USAGE_SCAN_JOB, Monitor, Organization, Payment, Subscription,
    UsageEvent,
)


def _month_start() -> datetime:
    now = datetime.utcnow()
    return datetime(now.year, now.month, 1)


def _day_start() -> datetime:
    now = datetime.utcnow()
    return datetime(now.year, now.month, now.day)


def active_subscription(db: Session, org_id: str) -> Subscription | None:
    if not org_id:
        return None
    return (db.query(Subscription)
            .filter(Subscription.organization_id == org_id,
                    Subscription.status.in_((SUB_ACTIVE, SUB_PAST_DUE)))
            .order_by(Subscription.created_at.desc())
            .first())


def current_plan(db: Session, org_id: str) -> str:
    sub = active_subscription(db, org_id)
    if sub and sub.status == SUB_ACTIVE:
        return sub.plan_code
    return PLAN_FREE


def entitlements(db: Session, org_id: str) -> dict:
    """The effective feature/limit set for an org (plan reflects the real plan even
    when enforcement is off, so the UI shows the truth)."""
    plan = current_plan(db, org_id)
    if not settings.billing_enforced:
        return {**ALL_ACCESS, "plan": plan, "enforced": False}
    base = ENTITLEMENTS.get(plan, ENTITLEMENTS[PLAN_FREE])
    return {**base, "plan": plan, "enforced": True}


def is_pro(db: Session, org_id: str) -> bool:
    return entitlements(db, org_id).get("plan") == PLAN_PRO or not settings.billing_enforced


def scan_is_unlocked(db: Session, org_id: str, scan_id: str) -> bool:
    """True if a one-time report purchase succeeded for this scan."""
    return db.query(Payment).filter(
        Payment.organization_id == org_id, Payment.scan_id == scan_id,
        Payment.kind == KIND_ONE_TIME_REPORT, Payment.status == PAY_SUCCEEDED,
    ).first() is not None


def export_unlocked(db: Session, org_id: str, scan_id: str) -> bool:
    """Whether this org may download the PDF/JSON/CSV exports for a scan. True if the
    org is Pro (the `downloads` entitlement — also granted when billing is off), OR a
    one-time $9 report purchase succeeded for this exact scan. One payment unlocks all
    three formats for that scan permanently."""
    if entitlements(db, org_id).get("downloads"):
        return True
    return scan_is_unlocked(db, org_id, scan_id)


# Backward-compatible alias (older callers).
can_download_report = export_unlocked


def ai_visibility_access(db: Session, org_id: str | None) -> dict:
    """The three granular AI-Visibility capability flags for an org (Phase 3). Free is all
    False (limited preview); Pro (and billing-off) is all True (full depth). Used by the
    visibility endpoint to trim each section without a new billing mechanism."""
    ent = entitlements(db, org_id)
    return {
        "ai_visibility": bool(ent.get("ai_visibility")),
        "competitor_intelligence": bool(ent.get("competitor_intelligence")),
        "opportunity_finder": bool(ent.get("opportunity_finder")),
    }


def answer_simulator_access(db: Session, org_id: str | None) -> dict:
    """{"batch_limit": int|None, "llm_step": bool, "full_evidence": bool} for the
    AEO Answer Simulator — same ENTITLEMENTS.get(plan, ...) lookup
    ai_visibility_access already uses."""
    ent = entitlements(db, org_id)
    return {
        "batch_limit": ent.get("answer_simulator_batch_limit", 5),
        "llm_step": bool(ent.get("answer_simulator_llm_step")),
        "full_evidence": bool(ent.get("answer_simulator_full_evidence")),
    }


def _quota(limit: int | None, used: int) -> dict:
    """Shape a {limit, used, remaining, unlimited} quota. limit None => unlimited."""
    if limit is None:
        return {"limit": None, "used": used, "remaining": None, "unlimited": True}
    return {"limit": limit, "used": used,
            "remaining": max(0, limit - used), "unlimited": False}


def scans_this_month(db: Session, org_id: str) -> int:
    """Scan JOBS used this calendar month — counted from usage_events (kind
    scan_job), NOT from the scans table. This is deliberate: monitor-triggered scans
    create scans rows but never record a scan_job event, so they don't consume quota.
    Only user-initiated scans (the API path) record a scan_job event."""
    return (db.query(func.count(UsageEvent.id))
            .filter(UsageEvent.organization_id == org_id,
                    UsageEvent.kind == USAGE_SCAN_JOB,
                    UsageEvent.created_at >= _month_start())
            .scalar() or 0)


def scan_quota(db: Session, org_id: str) -> dict:
    """{limit, used, remaining, unlimited} scan JOBS for the org's current month.
    Metered on BOTH plans now (Free 1, Pro 15); Pro is no longer unlimited."""
    return _quota(entitlements(db, org_id).get("scan_limit"), scans_this_month(db, org_id))


def monitors_count(db: Session, org_id: str) -> int:
    """Live monitors for the org. Monitors are hard-deleted, so every row counts
    (there is no soft 'deleted' status to exclude)."""
    return (db.query(func.count(Monitor.id))
            .filter(Monitor.organization_id == org_id)
            .scalar() or 0)


def monitor_quota(db: Session, org_id: str) -> dict:
    """{limit, used, remaining, unlimited} concurrent monitors. Capped on BOTH plans
    (Free 1, Pro 10); Pro is not unlimited. Mirrors scan_quota so the UI renders one meter."""
    return _quota(entitlements(db, org_id).get("monitor_limit"), monitors_count(db, org_id))


def active_shares_count(db: Session, org_id: str) -> int:
    """Live public share links for the org: unrevoked and not yet expired. Drives the
    active-share cap (Free 3, Pro unlimited)."""
    from app.core.security import now_utc
    from app.db.models import ReportShare
    return (db.query(func.count(ReportShare.id))
            .filter(ReportShare.organization_id == org_id,
                    ReportShare.revoked_at.is_(None),
                    ReportShare.expires_at > now_utc())
            .scalar() or 0)


def share_quota(db: Session, org_id: str) -> dict:
    """{limit, used, remaining, unlimited} concurrent public share links. Capped on Free
    (3), unlimited on Pro. Mirrors monitor_quota so the gating path is identical."""
    return _quota(entitlements(db, org_id).get("share_limit"), active_shares_count(db, org_id))


def compares_this_month(db: Session, org_id: str) -> int:
    return (db.query(func.count(UsageEvent.id))
            .filter(UsageEvent.organization_id == org_id,
                    UsageEvent.kind == USAGE_COMPARE,
                    UsageEvent.created_at >= _month_start())
            .scalar() or 0)


def compare_quota(db: Session, org_id: str) -> dict:
    """{limit, used, remaining, unlimited} scan comparisons for the current month.
    Counted from usage_events, so the count resets at the start of each calendar
    month. Free is capped; Pro is unlimited."""
    return _quota(entitlements(db, org_id).get("compare_limit"),
                  compares_this_month(db, org_id))


def record_usage(db: Session, org_id: str, kind: str) -> None:
    """Append one metered usage event (e.g. a comparison). Committed immediately so
    the count reflects it right away; a no-op when there is no org."""
    if not org_id:
        return
    db.add(UsageEvent(organization_id=org_id, kind=kind))
    db.commit()


def ai_narratives_this_month(db: Session, org_id: str) -> int:
    """AI report narratives generated for this org in the current calendar month
    (one event per successful Anthropic call). Drives the Free-tier monthly cap."""
    if not org_id:
        return 0
    return (db.query(func.count(UsageEvent.id))
            .filter(UsageEvent.organization_id == org_id,
                    UsageEvent.kind == USAGE_AI_NARRATIVE,
                    UsageEvent.created_at >= _month_start())
            .scalar() or 0)


def ai_calls_today(db: Session) -> int:
    """AI report narratives generated across ALL orgs today (UTC). Drives the global
    daily ceiling that protects total Anthropic spend."""
    return (db.query(func.count(UsageEvent.id))
            .filter(UsageEvent.kind == USAGE_AI_NARRATIVE,
                    UsageEvent.created_at >= _day_start())
            .scalar() or 0)


def history_limit(db: Session, org_id: str) -> int | None:
    return entitlements(db, org_id).get("history_limit")


def bulk_trial_available(db: Session, org_id: str) -> bool:
    """True if this org may still use its one-time-ever bulk trial: the plan grants a
    trial (Free — Pro already has bulk-50 jobs) and it hasn't been consumed. When
    billing enforcement is off the trial is treated as available (until used)."""
    if not org_id:
        return False
    if not entitlements(db, org_id).get("bulk_trial"):
        return False
    org = db.get(Organization, org_id)
    return bool(org) and not bool(org.used_bulk_trial)


def can_view_page_details(db: Session, org_id: str | None, scan_row) -> bool:
    """Whether the caller may see FULL per-page signal breakdowns for a scan. Scores +
    top-issue one-liners are always visible; the detailed sections (issues /
    recommendations / evidence) are Pro-only for MULTI-page (bulk) scans. Free users
    still get full details for their own single-page scans."""
    if is_pro(db, org_id):
        return True
    return (getattr(scan_row, "result", None) or {}).get("bulk") is None


def mark_bulk_trial_used(db: Session, org_id: str) -> None:
    """Consume the one-time bulk trial (idempotent). Called when a bulk trial scan is
    accepted; the flag never resets. Implemented here so the later bulk-scan task just
    calls this."""
    if not org_id:
        return
    org = db.get(Organization, org_id)
    if org and not org.used_bulk_trial:
        org.used_bulk_trial = True
        db.commit()
