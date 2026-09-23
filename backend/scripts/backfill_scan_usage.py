"""One-time, idempotent repair for the scan-count/quota mismatch bug.

Root cause (see app/api/routes_scan.py::run_scan): a scan job's usage event used to
be recorded by the *caller*, strictly after run_scan() returned. Any exception in a
later step inside run_scan() (report-payload building, snapshot/diff, etc.) could
leave a fully real, completed, org-owned scan row with no matching `scan_job`
UsageEvent — invisible to the sidebar's quota meter (entitlements.scans_this_month)
even though it's a real scan that shows up in "Recent Scans". That code path is now
fixed (usage is recorded the moment the row is persisted, before any later step can
raise) — this script only repairs usage events that went missing BEFORE the fix.

For each organization, for the CURRENT calendar month (matching
entitlements._month_start()'s own window — the only window scan_quota ever reads),
this compares:
  eligible = completed, org-owned scans this month, EXCLUDING monitor-triggered scans
             (via monitor_history.scan_id — monitor scans never consume quota, by
             existing, documented design; see entitlements.scans_this_month)
  recorded = scan_job UsageEvent rows for that org this month

and inserts the shortfall as new scan_job UsageEvent rows (timestamped now, so they
still land within the current month's window). It never removes anything and never
over-corrects (it only ever tops up recorded to eligible, never below).

Idempotent: running it twice makes no further changes the second time, since the
first run already brings recorded up to eligible.

Usage:
    cd backend && python -m scripts.backfill_scan_usage [--dry-run]
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import func

from app.billing.entitlements import _month_start
from app.db.models import SCAN_COMPLETED, USAGE_SCAN_JOB, MonitorHistory, Organization, Scan, UsageEvent
from app.db.session import SessionLocal


def eligible_scan_count(db, org_id: str, month_start) -> int:
    monitor_scan_ids = db.query(MonitorHistory.scan_id).subquery()
    return (
        db.query(func.count(Scan.id))
        .filter(
            Scan.organization_id == org_id,
            Scan.status == SCAN_COMPLETED,
            Scan.created_at >= month_start,
            Scan.id.notin_(db.query(monitor_scan_ids)),
        )
        .scalar()
        or 0
    )


def recorded_usage_count(db, org_id: str, month_start) -> int:
    return (
        db.query(func.count(UsageEvent.id))
        .filter(
            UsageEvent.organization_id == org_id,
            UsageEvent.kind == USAGE_SCAN_JOB,
            UsageEvent.created_at >= month_start,
        )
        .scalar()
        or 0
    )


def run(dry_run: bool = False) -> None:
    db = SessionLocal()
    month_start = _month_start()
    total_fixed = 0
    try:
        org_ids = [row[0] for row in db.query(Organization.id).all()]
        for org_id in org_ids:
            eligible = eligible_scan_count(db, org_id, month_start)
            recorded = recorded_usage_count(db, org_id, month_start)
            missing = eligible - recorded
            if missing <= 0:
                continue
            print(f"org {org_id}: eligible={eligible} recorded={recorded} -> backfilling {missing}")
            total_fixed += missing
            if not dry_run:
                for _ in range(missing):
                    db.add(UsageEvent(organization_id=org_id, kind=USAGE_SCAN_JOB))
                db.commit()
        if dry_run:
            print(f"DRY RUN: would backfill {total_fixed} missing scan_job usage event(s).")
        else:
            print(f"Backfilled {total_fixed} missing scan_job usage event(s).")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    args = parser.parse_args(sys.argv[1:])
    run(dry_run=args.dry_run)
