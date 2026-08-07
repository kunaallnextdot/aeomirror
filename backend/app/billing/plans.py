"""Plan definitions + entitlements matrix (Phase 9).

PLAN_DEFS seeds the `plans` table and drives the /plans API. ENTITLEMENTS is the
per-plan feature/limit matrix the gating layer reads. `report` is a one-time
purchase that unlocks a single scan's exports — it is NOT an org subscription plan.
"""
from __future__ import annotations

from app.config import settings

PLAN_FREE = "free"
PLAN_REPORT = "report"
PLAN_PRO = "pro"

PLAN_DEFS = [
    {
        "code": PLAN_FREE, "name": "Free", "sort_order": 0,
        "description": "Get started — an account is required to scan.",
        "price_cents": 0, "currency": settings.billing_currency, "interval": None,
        "features": [
            f"{settings.free_monthly_scan_jobs} scan job / month",
            f"{settings.free_monitor_limit} monitor",
            f"{settings.free_monthly_compares} comparison / month",
            "One-time bulk trial — 50 URLs, scores only (per-page detail locked)",
        ],
    },
    {
        "code": PLAN_REPORT, "name": "One-time Report", "sort_order": 1,
        "description": "Unlock the full report for a single scan.",
        "price_cents": settings.report_price_cents, "currency": settings.billing_currency,
        "interval": "one_time",
        "features": [
            "Full AI visibility report for this scan",
            "All recommendations & fixes",
            "AI-written report narrative",
            "PDF / CSV / JSON exports",
            "One report — doesn't change your plan",
        ],
    },
    {
        "code": PLAN_PRO, "name": "Pro", "sort_order": 2,
        "description": "More scans, monitoring and team access.",
        "price_cents": settings.pro_price_cents, "currency": settings.billing_currency,
        "interval": "month",
        "features": [
            f"{settings.pro_monthly_scan_jobs} scan jobs / month "
            "(each is a single page or a bulk of up to 50 URLs)",
            f"{settings.pro_monitor_limit} monitors",
            "Unlimited comparisons",
            "Full per-page detail on bulk scans",
            "PDF / CSV / JSON exports",
            "AI-written reports",
            "AI Content Insights",
            "Weekly report emails",
            "Team members & roles",
        ],
    },
]

# Feature/limit matrix per org plan. `downloads` = report exports (pdf/csv/json).
# A limit of None means "unlimited". Scan jobs and monitors are now METERED on BOTH
# plans (Pro is 15 scan jobs / 10 monitors, not unlimited); only history and
# comparisons are unlimited on Pro. `monitoring` True on Free means the feature is
# available up to `monitor_limit` (alerts/weekly emails stay Pro-only). `bulk_trial`
# grants the one-time-ever 50-URL trial (Free only; Pro already has bulk-50 jobs).
ENTITLEMENTS = {
    PLAN_FREE: {
        "plan": PLAN_FREE, "monitoring": True, "downloads": False, "team": False,
        "alerts": False, "weekly_reports": False, "unlimited_scans": False,
        "bulk_trial": True,
        "scan_limit": settings.free_monthly_scan_jobs, "history_limit": settings.free_history_limit,
        "monitor_limit": settings.free_monitor_limit,
        "compare_limit": settings.free_monthly_compares,
        "share_limit": settings.free_share_limit,   # concurrent active public share links
    },
    PLAN_PRO: {
        "plan": PLAN_PRO, "monitoring": True, "downloads": True, "team": True,
        "alerts": True, "weekly_reports": True, "unlimited_scans": False,
        "bulk_trial": False,
        "scan_limit": settings.pro_monthly_scan_jobs, "history_limit": None,
        "monitor_limit": settings.pro_monitor_limit, "compare_limit": None,
        "share_limit": None,                        # Pro: unlimited active share links
    },
}

# Access granted when billing enforcement is off (tests/dev without billing): every
# metered limit is lifted so gating never blocks. Must stay UNLIMITED even though Pro
# is now capped — otherwise the "billing off = full access" invariant would break.
ALL_ACCESS = {
    "plan": PLAN_PRO, "monitoring": True, "downloads": True, "team": True,
    "alerts": True, "weekly_reports": True, "unlimited_scans": True, "bulk_trial": True,
    "scan_limit": None, "history_limit": None, "monitor_limit": None, "compare_limit": None,
    "share_limit": None,
}


def plan_price_cents(code: str) -> int:
    for p in PLAN_DEFS:
        if p["code"] == code:
            return p["price_cents"]
    return 0


def seed_plans(db) -> None:
    """Idempotently ensure the plan rows exist (used by tests / create_all setups;
    real deployments seed via the migration)."""
    from app.db.models import Plan
    for p in PLAN_DEFS:
        if not db.get(Plan, p["code"]):
            db.add(Plan(code=p["code"], name=p["name"], description=p["description"],
                        price_cents=p["price_cents"], currency=p["currency"],
                        interval=p["interval"], features=p["features"], active=True,
                        sort_order=p["sort_order"]))
    db.commit()
