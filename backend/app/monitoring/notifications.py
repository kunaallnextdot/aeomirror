"""Monitoring notifications (Phase 7): immediate critical alerts + weekly/monthly
summaries, delivered via the existing Resend email provider and recorded in
notification_log. Best-effort — sending never raises into the scheduler.

Recipients are the organization owner (and the monitor's creator, if different).
When email isn't configured the send is skipped but still logged, and in dev the
body is logged so the flow is observable.
"""
from __future__ import annotations

import html
import logging
from datetime import timedelta

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import now_utc
from app.db.models import (
    Alert, Monitor, NotificationLog, Organization, User,
)

logger = logging.getLogger("aeomirror.monitor_notify")
RESEND_ENDPOINT = "https://api.resend.com/emails"


# ------------------------------- transport -------------------------------
def _send_email(to: str, subject: str, text: str, html_body: str, *, kind: str) -> str:
    """Send one email. Returns 'sent' | 'skipped' | 'failed'. Never raises."""
    if not settings.email_enabled:
        if not settings.is_production:
            logger.info("[monitor:%s] email not configured; would send to %s: %s",
                        kind, to, subject)
        return "skipped"
    try:
        resp = httpx.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"from": settings.email_from, "to": [to], "subject": subject,
                  "text": text, "html": html_body},
            timeout=10.0,
        )
        if resp.status_code // 100 == 2:
            return "sent"
        logger.warning("[monitor:%s] Resend failed: HTTP %s", kind, resp.status_code)
        return "failed"
    except Exception as e:
        logger.warning("[monitor:%s] Resend error: %s", kind, type(e).__name__)
        return "failed"


def _log(db: Session, *, org_id, user_id, monitor_id, kind, subject, status, meta=None):
    db.add(NotificationLog(
        organization_id=org_id, user_id=user_id, monitor_id=monitor_id,
        kind=kind, channel="email", subject=subject, status=status, meta=meta or {},
    ))
    db.commit()


def _recipients(db: Session, org_id: str, extra_user_id: str | None = None):
    """(user_id, email) list — org owner + optional creator, de-duplicated."""
    out: dict[str, str] = {}
    org = db.get(Organization, org_id) if org_id else None
    if org:
        owner = db.get(User, org.owner_id)
        if owner and owner.email:
            out[owner.id] = owner.email
    if extra_user_id and extra_user_id not in out:
        u = db.get(User, extra_user_id)
        if u and u.email:
            out[u.id] = u.email
    return list(out.items())


def _wrap(body: str) -> str:
    return (f"<div style=\"font-family:system-ui,sans-serif;line-height:1.6;color:#0B0F14\">"
            f"{body}</div>")


# ------------------------------- critical alerts -------------------------------
def notify_critical_alerts(db: Session, monitor: Monitor, alerts: list[Alert]) -> int:
    """Immediate email for the critical alerts from one scan. Returns emails sent."""
    from app.admin import flags
    crit = [a for a in alerts if a.severity == "critical"]
    if not crit or not flags.is_enabled(db, "email"):
        return 0
    domain = monitor.normalized_url or monitor.url
    subject = f"⚠ AEOMirror alert: {len(crit)} critical issue{'s' if len(crit) != 1 else ''} on {domain}"
    lines = [f"AEOMirror detected {len(crit)} critical AI-visibility issue(s) on {domain}:", ""]
    items_html = []
    for a in crit:
        lines.append(f"  • {a.title} — {a.message or ''}")
        items_html.append(f"<li><strong>{html.escape(a.title)}</strong>"
                          f"{(' — ' + html.escape(a.message)) if a.message else ''}</li>")
    lines += ["", f"View the monitor: {settings.app_base_url}"]
    text = "\n".join(lines)
    html_body = _wrap(
        f"<p>AEOMirror detected <strong>{len(crit)}</strong> critical AI-visibility "
        f"issue(s) on <strong>{html.escape(str(domain))}</strong>:</p>"
        f"<ul>{''.join(items_html)}</ul>"
        f"<p><a href=\"{html.escape(settings.app_base_url)}\">Open your monitoring dashboard</a></p>")

    sent = 0
    for user_id, email in _recipients(db, monitor.organization_id, monitor.user_id):
        status = _send_email(email, subject, text, html_body, kind="critical_alert")
        _log(db, org_id=monitor.organization_id, user_id=user_id, monitor_id=monitor.id,
             kind="critical_alert", subject=subject, status=status,
             meta={"alert_count": len(crit)})
        if status == "sent":
            sent += 1
    return sent


# ------------------------------- periodic summaries -------------------------------
def _summarize(db: Session, org_id: str, since):
    monitors = db.query(Monitor).filter(Monitor.organization_id == org_id).all()
    alerts = (db.query(Alert)
              .filter(Alert.organization_id == org_id, Alert.created_at >= since)
              .all())
    return monitors, alerts


def _summary_email(db: Session, org_id: str, kind: str, since, period_label: str) -> bool:
    monitors, alerts = _summarize(db, org_id, since)
    if not monitors:
        return False
    crit = sum(1 for a in alerts if a.severity == "critical")
    subject = f"AEOMirror {period_label} summary — {len(monitors)} monitor(s), {len(alerts)} alert(s)"
    rows = []
    for m in monitors:
        rows.append(f"  • {m.normalized_url or m.url}: score {m.latest_score if m.latest_score is not None else '—'} "
                    f"({m.status}, {m.frequency})")
    text = "\n".join([
        f"Your AEOMirror {period_label} monitoring summary:", "",
        f"Monitors: {len(monitors)} | Alerts this period: {len(alerts)} ({crit} critical)",
        "", *rows, "", f"Open the dashboard: {settings.app_base_url}"])
    rows_html = "".join(
        f"<li>{html.escape(str(m.normalized_url or m.url))}: "
        f"<strong>{m.latest_score if m.latest_score is not None else '—'}</strong> "
        f"<span style='color:#5A6772'>({html.escape(m.status)}, {html.escape(m.frequency)})</span></li>"
        for m in monitors)
    html_body = _wrap(
        f"<p>Your AEOMirror <strong>{period_label}</strong> monitoring summary:</p>"
        f"<p>{len(monitors)} monitor(s) · {len(alerts)} alert(s) this period "
        f"(<strong>{crit}</strong> critical)</p><ul>{rows_html}</ul>"
        f"<p><a href=\"{html.escape(settings.app_base_url)}\">Open your monitoring dashboard</a></p>")

    any_sent = False
    for user_id, email in _recipients(db, org_id):
        status = _send_email(email, subject, text, html_body, kind=kind)
        _log(db, org_id=org_id, user_id=user_id, monitor_id=None, kind=kind,
             subject=subject, status=status,
             meta={"monitors": len(monitors), "alerts": len(alerts), "critical": crit})
        any_sent = any_sent or status in ("sent", "skipped")
    return any_sent


def send_weekly_summary(db: Session, org_id: str) -> bool:
    return _summary_email(db, org_id, "weekly_summary", now_utc() - timedelta(days=7), "weekly")


def send_monthly_summary(db: Session, org_id: str) -> bool:
    return _summary_email(db, org_id, "monthly_summary", now_utc() - timedelta(days=30), "monthly")


def _last_sent(db: Session, org_id: str, kind: str):
    row = (db.query(NotificationLog)
           .filter(NotificationLog.organization_id == org_id,
                   NotificationLog.kind == kind,
                   NotificationLog.status.in_(("sent", "skipped")))
           .order_by(NotificationLog.created_at.desc())
           .first())
    return row.created_at if row else None


def _baseline(db: Session, org_id: str, kind: str) -> None:
    """Record a no-send baseline so the first real summary goes out one period
    after we first see the org (not the instant a monitor is created)."""
    _log(db, org_id=org_id, user_id=None, monitor_id=None, kind=kind,
         subject=None, status="skipped", meta={"baseline": True})


def maybe_send_summaries(db: Session, now=None) -> dict:
    """Send weekly/monthly summaries to any org that is due. Idempotent via
    notification_log (won't re-send within the period). The first encounter only
    records a baseline."""
    now = now or now_utc()
    org_ids = [r[0] for r in db.query(Monitor.organization_id).distinct().all() if r[0]]
    counts = {"weekly": 0, "monthly": 0}
    for org_id in org_ids:
        # C1: the richer weekly DIGEST supersedes the legacy weekly summary for any org
        # that has at least one digest-enabled monitor — users must never get both.
        digest_active = db.query(Monitor).filter(
            Monitor.organization_id == org_id, Monitor.digest_enabled.is_(True)).first() is not None
        for kind, key, days in (("weekly_summary", "weekly", 7),
                                ("monthly_summary", "monthly", 30)):
            if key == "weekly" and digest_active:
                continue
            last = _last_sent(db, org_id, kind)
            if last is None:
                _baseline(db, org_id, kind)
            elif (now - last) >= timedelta(days=days):
                sender = send_weekly_summary if key == "weekly" else send_monthly_summary
                if sender(db, org_id):
                    counts[key] += 1
    return counts
