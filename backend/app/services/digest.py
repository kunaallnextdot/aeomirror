"""Weekly digest email.

Per monitor, per week: current score + delta vs last week + direction, a change-set
summary grouped by severity (highest first), any blocked AI crawlers (called out
prominently), and the top-3 recommended fixes drawn from the scan's findings ranked by
severity. Orgs with no data are skipped — we never send an empty digest.

Delivery is a SIDE EFFECT: every path here is best-effort and NEVER raises into the
scheduler / scan pipeline. Sends are recorded in notification_log; opens/clicks come from
Resend's own analytics (no in-app tracking). Send window is fixed UTC (settings).
"""
from __future__ import annotations

import html as _html
import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import generate_token, now_utc
from app.db.models import (
    Monitor, MonitorHistory, NotificationLog, Organization, Scan, User,
)
from app.services import email_backend

log = logging.getLogger("aeomirror.digest")

DIGEST_KIND = "weekly_digest"
_SEV_ORDER = ("CRITICAL", "WARNING", "INFO")
_SEV_RANK = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
_BLOCKED = ("blocked_by_robots", "blocked_by_server")


# ------------------------------- opt-out token -------------------------------
def unsubscribe_token(db: Session, user: User) -> str:
    """The user's stable unsubscribe token (minted once, never expires)."""
    if not user.digest_unsubscribe_token:
        user.digest_unsubscribe_token = generate_token()
        db.commit()
    return user.digest_unsubscribe_token


def user_by_token(db: Session, token: str) -> User | None:
    if not token:
        return None
    return db.query(User).filter(User.digest_unsubscribe_token == token).first()


def _unsubscribe_url(token: str) -> str:
    return f"{settings.api_base_url.rstrip('/')}/digest/unsubscribe/{token}"


# ------------------------------- content assembly -------------------------------
def _blocked_crawlers(result: dict) -> list[dict]:
    findings = (result.get("crawler_access") or {}).get("findings") or []
    out = [{"name": f.get("bot_name"), "severity": f.get("severity"),
            "cause": f.get("cause"), "fix": f.get("remediation")}
           for f in findings if f.get("status") in _BLOCKED]
    out.sort(key=lambda x: _SEV_RANK.get(x["severity"], 9))
    return out


def _top_fixes(result: dict) -> list[dict]:
    """Top-3 fixes from existing findings, ranked by severity. Sources: blocked-crawler
    remediations, then failing/warning signal sections (their first recommendation)."""
    items: list[dict] = []
    for f in (result.get("crawler_access") or {}).get("findings") or []:
        if f.get("status") in _BLOCKED and f.get("remediation"):
            items.append({"severity": f.get("severity"),
                          "title": f"{f.get('bot_name')} blocked", "fix": f["remediation"]})
    for s in result.get("sections") or []:
        sev = {"fail": "CRITICAL", "warn": "WARNING"}.get(s.get("status"))
        recs = s.get("recommendations") or []
        if sev and recs:
            items.append({"severity": sev, "title": s.get("label"), "fix": recs[0]})
    items.sort(key=lambda x: _SEV_RANK.get(x["severity"], 9))
    return items[:3]


def _monitor_block(db: Session, m: Monitor, now) -> dict | None:
    """One monitor's digest block, or None when it has no scan data yet."""
    if not m.latest_scan_id:
        return None
    scan = db.get(Scan, m.latest_scan_id)
    if not scan:
        return None
    result = scan.result or {}
    week_ago = now - timedelta(days=7)

    prev = (db.query(MonitorHistory)
            .filter(MonitorHistory.monitor_id == m.id,
                    MonitorHistory.created_at <= week_ago)
            .order_by(MonitorHistory.created_at.desc()).first())
    current = m.latest_score
    prev_score = prev.overall_score if prev else None
    delta = (current - prev_score) if (current is not None and prev_score is not None) else None
    direction = "flat" if not delta else ("up" if delta > 0 else "down")

    # change-set summary aggregated over the week's scans
    week_hist = (db.query(MonitorHistory)
                 .filter(MonitorHistory.monitor_id == m.id,
                         MonitorHistory.created_at >= week_ago).all())
    scan_ids = [h.scan_id for h in week_hist if h.scan_id]
    scans = ({s.id: s for s in db.query(Scan).filter(Scan.id.in_(scan_ids)).all()}
             if scan_ids else {})
    summary = {sev: 0 for sev in _SEV_ORDER}
    for h in week_hist:
        s = scans.get(h.scan_id)
        for c in ((s.result or {}).get("change_set") if s else None) or []:
            if c.get("severity") in summary:
                summary[c["severity"]] += 1

    return {
        "monitor_id": m.id, "url": m.normalized_url or m.url, "name": m.name,
        "score": current, "delta": delta, "direction": direction,
        "change_summary": summary,
        "blocked_crawlers": _blocked_crawlers(result),
        "top_fixes": _top_fixes(result),
    }


def build_digest(db: Session, org_id: str, *, now=None) -> dict | None:
    """Assemble the org's weekly digest, or None when there's no data (→ skip send)."""
    now = now or now_utc()
    monitors = (db.query(Monitor)
                .filter(Monitor.organization_id == org_id,
                        Monitor.digest_enabled.is_(True)).all())
    blocks = [b for b in (_monitor_block(db, m, now) for m in monitors) if b]
    if not blocks:
        return None
    return {"org_id": org_id, "monitors": blocks, "generated_at": now.isoformat()}


def sample_digest() -> dict:
    """A synthetic digest for the admin preflight (deliverability check without data)."""
    return {"org_id": None, "monitors": [{
        "monitor_id": None, "url": "example.com", "name": "Example",
        "score": 82, "delta": -6, "direction": "down",
        "change_summary": {"CRITICAL": 1, "WARNING": 2, "INFO": 3},
        "blocked_crawlers": [{"name": "GPTBot", "severity": "CRITICAL",
                              "cause": "Disallowed in robots.txt.",
                              "fix": "Remove the Disallow rule for GPTBot."}],
        "top_fixes": [{"severity": "CRITICAL", "title": "GPTBot blocked",
                       "fix": "Remove the Disallow rule for GPTBot."}],
    }], "generated_at": now_utc().isoformat(), "sample": True}


# ------------------------------- render -------------------------------
_ARROW = {"up": "▲", "down": "▼", "flat": "▬"}


def _delta_str(d) -> str:
    return "no change" if d is None else (f"+{d}" if d > 0 else str(d))


def render_digest(digest: dict, *, unsubscribe_url: str) -> tuple[str, str, str]:
    """Return (subject, text, html) for a digest dict."""
    n = len(digest["monitors"])
    subject = f"AEOMirror weekly digest — {n} monitor{'s' if n != 1 else ''}"

    lines = ["Your AEOMirror weekly digest:", ""]
    html_blocks = ["<p>Your AEOMirror <strong>weekly digest</strong>:</p>"]
    for b in digest["monitors"]:
        lines.append(f"• {b['url']} — score {b['score']} "
                     f"({_ARROW.get(b['direction'], '')} {_delta_str(b['delta'])} vs last week)")
        cs = b["change_summary"]
        sev_bits = [f"{cs[sev]} {sev.lower()}" for sev in _SEV_ORDER if cs[sev]]
        if sev_bits:
            lines.append(f"    changes this week: {', '.join(sev_bits)}")
        if b["blocked_crawlers"]:
            lines.append(f"    ⚠ BLOCKED AI CRAWLERS: {', '.join(c['name'] for c in b['blocked_crawlers'])}")
        for fx in b["top_fixes"]:
            lines.append(f"    fix [{fx['severity']}] {fx['title']}: {fx['fix']}")
        lines.append("")

        sev_html = "".join(
            f"<span style='margin-right:10px'>{cs[sev]} {sev.lower()}</span>"
            for sev in _SEV_ORDER if cs[sev])
        blocked_html = ""
        if b["blocked_crawlers"]:
            names = ", ".join(_html.escape(c["name"] or "") for c in b["blocked_crawlers"])
            blocked_html = (f"<p style='color:#E5615B;font-weight:600'>⚠ Blocked AI crawlers: "
                            f"{names}</p>")
        fixes_html = "".join(
            f"<li><strong>[{_html.escape(fx['severity'])}]</strong> "
            f"{_html.escape(fx['title'] or '')}: {_html.escape(fx['fix'] or '')}</li>"
            for fx in b["top_fixes"])
        html_blocks.append(
            f"<div style='border:1px solid #1E2833;border-radius:10px;padding:12px;margin:10px 0'>"
            f"<p style='margin:0 0 4px'><strong>{_html.escape(b['url'])}</strong> — "
            f"score <strong>{b['score']}</strong> "
            f"({_ARROW.get(b['direction'], '')} {_html.escape(_delta_str(b['delta']))} vs last week)</p>"
            f"<p style='margin:0;color:#5A6772'>{sev_html or 'no changes this week'}</p>"
            f"{blocked_html}"
            f"{('<ul>' + fixes_html + '</ul>') if fixes_html else ''}</div>")

    lines += [f"Dashboard: {settings.app_base_url}", f"Unsubscribe: {unsubscribe_url}"]
    text = "\n".join(lines)
    html_body = (f"<div style=\"font-family:system-ui,sans-serif;line-height:1.6;color:#0B0F14\">"
                 f"{''.join(html_blocks)}"
                 f"<p><a href=\"{_html.escape(settings.app_base_url)}\">Open your dashboard</a> · "
                 f"<a href=\"{_html.escape(unsubscribe_url)}\">Unsubscribe</a></p></div>")
    return subject, text, html_body


# ------------------------------- send -------------------------------
def _recipients(db: Session, org_id: str) -> list[User]:
    """Digest recipients: the org owner, excluding anyone who opted out."""
    org = db.get(Organization, org_id)
    if not org:
        return []
    owner = db.get(User, org.owner_id)
    if owner and owner.email and not owner.digest_opt_out:
        return [owner]
    return []


def _record(db: Session, org_id, user_id, subject, res: dict) -> None:
    status = {"sent": "sent", "logged": "sent", "skipped": "skipped"}.get(res.get("status"), "failed")
    db.add(NotificationLog(
        organization_id=org_id, user_id=user_id, monitor_id=None, kind=DIGEST_KIND,
        channel="email", subject=subject, status=status,
        meta={k: v for k, v in res.items() if k in ("backend", "http_status", "reason", "error")}))
    db.commit()


def send_digest_for_org(db: Session, org_id: str, *, now=None) -> bool:
    """Build + send the org's digest. Returns True if a send was attempted (had data),
    False if skipped (no data / no recipients). NEVER raises — email is a side effect."""
    try:
        digest = build_digest(db, org_id, now=now)
        if digest is None:
            return False
        recipients = _recipients(db, org_id)
        if not recipients:
            return False
        for user in recipients:
            unsub = _unsubscribe_url(unsubscribe_token(db, user))
            subject, text, html_body = render_digest(digest, unsubscribe_url=unsub)
            headers = {"List-Unsubscribe": f"<{unsub}>",
                       "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
            res = email_backend.send(to=user.email, subject=subject, text=text,
                                     html=html_body, headers=headers)
            _record(db, org_id, user.id, subject, res)
        return True
    except Exception:   # noqa: BLE001 — a send failure must never break the caller
        log.exception("weekly digest failed for org %s", org_id)
        return False


# ------------------------------- scheduling -------------------------------
def org_has_digest_monitor(db: Session, org_id: str) -> bool:
    return db.query(Monitor).filter(
        Monitor.organization_id == org_id, Monitor.digest_enabled.is_(True)).first() is not None


def _sent_this_week(db: Session, org_id: str, now) -> bool:
    return (db.query(NotificationLog)
            .filter(NotificationLog.organization_id == org_id,
                    NotificationLog.kind == DIGEST_KIND,
                    NotificationLog.created_at >= now - timedelta(days=6))
            .first()) is not None


def maybe_send_digests(db: Session, now=None) -> dict:
    """Weekly, batched per org. Fires only within the configured UTC window and once per
    week per org. One org's failure is isolated — the batch continues."""
    now = now or now_utc()
    if not (now.weekday() == settings.digest_send_weekday
            and now.hour >= settings.digest_send_hour_utc):
        return {"digests": 0}

    org_ids = [r[0] for r in db.query(Monitor.organization_id).distinct().all() if r[0]]
    sent = 0
    for org_id in org_ids:
        try:
            if not org_has_digest_monitor(db, org_id):
                continue
            if _sent_this_week(db, org_id, now):
                continue
            if send_digest_for_org(db, org_id, now=now):
                sent += 1
        except Exception:   # noqa: BLE001 — per-org isolation
            log.exception("digest batch failed for org %s", org_id)
            continue
    return {"digests": sent}
