"""Platform analytics (Phase 8): aggregates across ALL organizations for the admin
dashboard and analytics views. Time-based counts and domain rollups use SQL; the
JSON-derived aggregates (score, issue distribution, categories, failures) run over a
bounded recent sample so they stay fast at scale."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    JOB_FAILED, MONITOR_ACTIVE, Monitor, Organization, Scan, ScheduledJob, User,
)

# Cap the JSON-parsing sample so analytics stay bounded on large datasets.
_SAMPLE = 2000


def _recent_scans(db: Session, limit: int = _SAMPLE) -> list[Scan]:
    return (db.query(Scan).order_by(Scan.created_at.desc()).limit(limit).all())


def _classify_industry(domain: str) -> str:
    d = (domain or "").lower()
    rules = [
        (("shop", "store", "cart", "buy", "commerce"), "E-commerce"),
        (("clinic", "health", "doctor", "med", "care", "dental", "hospital"), "Healthcare"),
        (("law", "legal", "attorney"), "Legal"),
        (("edu", "school", "academy", "university", "learn"), "Education"),
        (("bank", "finance", "capital", "invest", "pay"), "Finance"),
        (("news", "blog", "media", "press"), "Media"),
        (("agency", "studio", "design", "marketing"), "Agency"),
        (("app", "io", "ai", "tech", "software", "cloud", "dev"), "Technology"),
    ]
    for keys, label in rules:
        if any(k in d for k in keys):
            return label
    return "Other"


def _avg_overall(scans: list[Scan]) -> float | None:
    vals = [(s.result or {}).get("overall_score") for s in scans]
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 1) if vals else None


def _count_since(db: Session, since: datetime) -> int:
    return db.query(func.count(Scan.id)).filter(Scan.created_at >= since).scalar() or 0


def _daily_series(db: Session, days: int = 14) -> list[dict]:
    start = datetime.utcnow() - timedelta(days=days - 1)
    rows = (db.query(func.date(Scan.created_at), func.count(Scan.id))
            .filter(Scan.created_at >= start)
            .group_by(func.date(Scan.created_at))
            .all())
    counts = {str(d): int(c) for d, c in rows}
    series = []
    today = date.today()
    for i in range(days):
        day = str(today - timedelta(days=days - 1 - i))
        series.append({"date": day, "scans": counts.get(day, 0)})
    return series


def scan_windows(db: Session) -> dict:
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    return {
        "today": _count_since(db, today_start),
        "last_7d": _count_since(db, now - timedelta(days=7)),
        "last_30d": _count_since(db, now - timedelta(days=30)),
        "total": db.query(func.count(Scan.id)).scalar() or 0,
    }


def dashboard_stats(db: Session, *, api_requests: int, health_status: str) -> dict:
    windows = scan_windows(db)
    sample = _recent_scans(db, 1000)
    total_websites = (db.query(func.count(func.distinct(Scan.normalized_url))).scalar() or 0)
    return {
        "total_users": db.query(func.count(User.id)).scalar() or 0,
        "active_users": db.query(func.count(User.id)).filter(User.status == "active").scalar() or 0,
        "organizations": db.query(func.count(Organization.id)).scalar() or 0,
        "total_websites": total_websites,
        "total_scans": windows["total"],
        "todays_scans": windows["today"],
        "running_monitors": db.query(func.count(Monitor.id)).filter(Monitor.status == MONITOR_ACTIVE).scalar() or 0,
        "failed_jobs": db.query(func.count(ScheduledJob.id)).filter(ScheduledJob.status == JOB_FAILED).scalar() or 0,
        "average_ai_score": _avg_overall(sample),
        "api_requests": api_requests,
        "system_health": health_status,
    }


def analytics(db: Session) -> dict:
    windows = scan_windows(db)
    sample = _recent_scans(db, _SAMPLE)

    # issue distribution: signal statuses across the sample
    status_dist = {"pass": 0, "warn": 0, "fail": 0}
    fail_by_signal: dict = {}
    issue_counts: dict = {}
    score_buckets = {"0-20": 0, "20-40": 0, "40-60": 0, "60-80": 0, "80-100": 0}
    bucket_keys = list(score_buckets)
    for s in sample:
        r = s.result or {}
        sc = r.get("overall_score")
        if isinstance(sc, (int, float)):
            score_buckets[bucket_keys[min(int(sc // 20), 4)]] += 1
        for sec in r.get("sections", []):
            st = sec.get("status")
            if st in status_dist:
                status_dist[st] += 1
            if st == "fail":
                entry = fail_by_signal.setdefault(sec.get("id"), [sec.get("label"), 0])
                entry[1] += 1
            for iss in sec.get("issues", []):
                issue_counts[iss] = issue_counts.get(iss, 0) + 1

    top_categories = sorted(
        ({"signal": k, "label": v[0], "fail_count": v[1]} for k, v in fail_by_signal.items()),
        key=lambda x: -x["fail_count"])[:10]
    common_failures = sorted(
        ({"issue": k, "count": v} for k, v in issue_counts.items()),
        key=lambda x: -x["count"])[:10]

    # most scanned domains + inferred industries (SQL for domains)
    dom_rows = (db.query(Scan.normalized_url, func.count(Scan.id))
                .group_by(Scan.normalized_url)
                .order_by(func.count(Scan.id).desc())
                .limit(10).all())
    most_scanned_domains = [{"domain": d, "count": int(c)} for d, c in dom_rows]
    industry_counts: dict = {}
    all_dom = (db.query(Scan.normalized_url, func.count(Scan.id))
               .group_by(Scan.normalized_url).all())
    for d, c in all_dom:
        industry_counts[_classify_industry(d)] = industry_counts.get(_classify_industry(d), 0) + int(c)
    industries = sorted(({"industry": k, "count": v} for k, v in industry_counts.items()),
                        key=lambda x: -x["count"])

    return {
        "scans": {"daily": windows["today"], "weekly": windows["last_7d"],
                  "monthly": windows["last_30d"], "total": windows["total"]},
        "daily_series": _daily_series(db, 14),
        "average_score": _avg_overall(sample),
        "issue_distribution": status_dist,
        "score_distribution": [{"bucket": b, "count": score_buckets[b]} for b in bucket_keys],
        "top_issue_categories": top_categories,
        "most_scanned_industries": industries,
        "most_scanned_domains": most_scanned_domains,
        "most_common_failures": common_failures,
        "sample_size": len(sample),
    }
