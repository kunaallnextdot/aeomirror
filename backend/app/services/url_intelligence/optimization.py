"""URL-wise optimization and continuous monitoring.

One record per important URL that connects what already exists, without copying it:
the Business Value Score (PageValueScore), the Page Intelligence analysis and its
recommendations (PageAnalysis), the Action Center finding each crawl issue is tracked
as (UrlFinding), GA4 / Search Console / Answer Tracking numbers, and, new here, the
fix lifecycle of each recommendation (UrlAction), its timeline (UrlActionEvent) and
before/after performance (fix_impact).

Rules this module keeps:
- A recommendation nobody has acted on has no row. Its status is derived from the page's
  analysis history: "new" in the latest crawl, "needs attention" if an earlier crawl
  already had it.
- A user can say a fix is ready; only a check can say it is verified. A check re-runs the
  same deterministic rules on fresh evidence (verification.evaluate_codes), either from a
  single-page re-check or from the next full crawl. No score is ever read.
- Comparisons across a change of rules or crawl-engine version re-baseline instead of
  claiming an improvement or a regression.
- Alerts are raised only for a verified fix that came back, or a new confirmed
  high/critical issue on a high-value page, once per issue while the alert is open.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from app.db.models import (
    ACTION_IGNORED, ACTION_IN_PROGRESS, ACTION_NEEDS_ATTENTION, ACTION_NEW, ACTION_NOT_FIXED, ACTION_PARTIAL,
    ACTION_READY, ACTION_REOPENED, ACTION_VERIFIED, ALERT_OPEN, PV_COMPLETED, RESULT_FIXED, RESULT_NOT_FIXED,
    RESULT_PARTIAL, RESULT_UNABLE, URL_CRAWLED, Alert, CrawlJob, CrawlUrl, PageAnalysis, PageValueRun,
    PageValueScore, UrlAction, UrlActionEvent,
)
from app.services.url_intelligence import fix_impact
from app.services.url_intelligence import page_intelligence as pi
from app.services.url_intelligence import page_value as pv
from app.services.url_intelligence import verification as vf
from app.services.url_intelligence.evidence import content_fingerprint
from app.services.url_intelligence.issues import CATALOG

logger = logging.getLogger("app.optimization")

OPEN_STATUSES = (ACTION_NEW, ACTION_NEEDS_ATTENTION, ACTION_IN_PROGRESS, ACTION_READY, ACTION_PARTIAL,
                 ACTION_NOT_FIXED, ACTION_REOPENED)
TO_DO_STATUSES = (ACTION_NEW, ACTION_NEEDS_ATTENTION, ACTION_PARTIAL, ACTION_NOT_FIXED, ACTION_REOPENED)
IMPORTANT_PRIORITIES = ("high", "medium")     # Business Value priorities that make a URL "important"
SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.8, "low": 0.6}
VALUE_INTERVAL = {"high": 0.5, "medium": 1.0}  # re-check interval multiplier by page value (else 2x)
BLOCKING_CODES = ("http_5xx", "http_4xx", "noindex", "canonical_elsewhere")
RECHECK_BATCH_PAGES = 5        # pages fetched per job run (one fetch per page, all its due checks)
RECHECK_DAILY_LIMIT = 200      # single-page checks per organization per day
SNAPSHOT_BATCH = 50
UNABLE_BACKOFF_HOURS = (1, 6, 24)
SUPPRESS_AFTER = 2             # consecutive "not fixed" results before an action is held back
NEXT_ACTION_PAGES = 100        # highest-value pages considered for next best actions
MAX_NEW_ISSUE_ALERTS = 5       # per crawl

STATUS_TEXT = {ACTION_NEW: "New", ACTION_NEEDS_ATTENTION: "Needs attention", ACTION_IN_PROGRESS: "In progress",
               ACTION_READY: "Ready for verification", ACTION_VERIFIED: "Verified", ACTION_PARTIAL: "Partially fixed",
               ACTION_NOT_FIXED: "Not fixed", ACTION_REOPENED: "Reopened", ACTION_IGNORED: "Ignored"}
RESULT_TEXT = {RESULT_FIXED: "fixed", RESULT_PARTIAL: "partially fixed", RESULT_NOT_FIXED: "not fixed",
               RESULT_UNABLE: "couldn't verify"}
METHOD_TEXT = {"recrawl": "page re-check", "crawl": "site crawl"}


class ActionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def code_meta(code: str) -> dict:
    spec = CATALOG.get(code) or pi.REC.get(code) or {}
    return {"title": spec.get("title", code.replace("_", " ")), "severity": spec.get("severity", "low"),
            "kind": spec.get("kind", "potential")}


def verification_method(code: str) -> str:
    if code in vf.PERFORMANCE_CODES:
        return "performance"
    if code in vf.SITE_CRAWL_CODES:
        return "site_crawl"
    return "page_check"


# --------------------------------------------------------------------------- lookups

def _analysis_index(db: Session, monitor_id: str, keys: set[str] | None = None) -> dict[str, dict]:
    """Per page: the latest analysis's codes, and the codes of the previous analysis from
    a DIFFERENT crawl under the same rules (None when there isn't one)."""
    q = db.query(PageAnalysis.normalized_key, PageAnalysis.id, PageAnalysis.crawl_job_id,
                 PageAnalysis.recommendation_codes, PageAnalysis.rules_version, PageAnalysis.created_at,
                 PageAnalysis.crawl_url_id).filter(PageAnalysis.monitor_id == monitor_id)
    if keys is not None and len(keys) <= 500:
        q = q.filter(PageAnalysis.normalized_key.in_(list(keys)))
    out: dict[str, dict] = {}
    for key, aid, job, codes, rv, at, cu in q.order_by(PageAnalysis.created_at.desc(), PageAnalysis.id.desc()):
        if keys is not None and key not in keys:
            continue
        e = out.get(key)
        if e is None:
            out[key] = {"id": aid, "crawl_job_id": job, "crawl_url_id": cu, "codes": list(codes or []),
                        "rules_version": rv, "at": at, "prev_codes": None, "prev_crawl_job_id": None}
        elif e["prev_codes"] is None and job != e["crawl_job_id"] and rv == e["rules_version"]:
            e["prev_codes"], e["prev_crawl_job_id"] = set(codes or []), job
    return out


def derived_status(code: str, entry: dict | None) -> str:
    prev = (entry or {}).get("prev_codes")
    return ACTION_NEEDS_ATTENTION if prev and code in prev else ACTION_NEW


def _rows_by_key(db: Session, monitor_id: str) -> dict[str, dict[str, UrlAction]]:
    out: dict[str, dict[str, UrlAction]] = {}
    for a in db.query(UrlAction).filter(UrlAction.monitor_id == monitor_id).all():
        out.setdefault(a.normalized_key, {})[a.code] = a
    return out


def _site_ctx(db: Session, run: PageValueRun | None, crawl_job_id: str | None, key: str,
              title: str | None, fingerprint: str | None) -> dict:
    """The site context a page's checks use: the homepage's business name and how many
    OTHER crawled pages share this title / content."""
    site = ((run.inputs or {}).get("page_intelligence") or {}) if run else {}
    ctx = {"homepage_entity_name": site.get("homepage_entity_name"), "duplicate_titles": 0, "duplicate_content": 0}
    if crawl_job_id:
        base = db.query(func.count(CrawlUrl.id)).filter(CrawlUrl.crawl_job_id == crawl_job_id,
                                                         CrawlUrl.status == URL_CRAWLED, CrawlUrl.normalized_key != key)
        if title:
            ctx["duplicate_titles"] = base.filter(CrawlUrl.page_title == title).scalar() or 0
        if fingerprint:
            ctx["duplicate_content"] = base.filter(CrawlUrl.content_fingerprint == fingerprint).scalar() or 0
    return ctx


def _page_refs(db: Session, run: PageValueRun | None, key: str):
    if run is None:
        return None, None
    row = (db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == run.crawl_job_id, CrawlUrl.normalized_key == key)
           .first())
    score = (db.query(PageValueScore).filter(PageValueScore.run_id == run.id, PageValueScore.crawl_url_id == row.id)
             .first()) if row else None
    return row, score


def resolve_page(db: Session, org_id: str, monitor_id: str, crawl_url_id: str):
    """(run, score, row) for a page of the latest completed scoring run, or ActionError 404."""
    run = pv.latest_run(db, monitor_id, PV_COMPLETED)
    score = (db.query(PageValueScore)
             .filter(PageValueScore.run_id == run.id, PageValueScore.crawl_url_id == crawl_url_id,
                     PageValueScore.organization_id == org_id).first()) if run else None
    row = db.get(CrawlUrl, crawl_url_id) if score else None
    if score is None or row is None or row.organization_id != org_id:
        raise ActionError(404, "Page not found.")
    return run, score, row


def _event(db: Session, a: UrlAction, kind: str, *, user_id=None, from_status=None, to_status=None, note=None,
           detail=None, at=None):
    db.add(UrlActionEvent(organization_id=a.organization_id, monitor_id=a.monitor_id,
                          normalized_key=a.normalized_key, action_id=a.id, code=a.code, kind=kind,
                          from_status=from_status, to_status=to_status, note=note, detail=detail, user_id=user_id,
                          created_at=at or datetime.utcnow()))


# --------------------------------------------------------------------------- scheduling

def check_priority(score: PageValueScore | None, priority: str | None, requested: bool) -> float:
    """Order of due checks: high-value commercial pages with severe issues first; a
    user's explicit request before anything automatic."""
    value = (score.score if score else 0) + 10
    p = value * SEVERITY_WEIGHT.get(priority or "low", 1) * (1.5 if score is not None and score.is_commercial else 1.0)
    return p + (100000.0 if requested else 0.0)


def _interval(days: int, value_priority: str | None) -> timedelta:
    return timedelta(days=days * VALUE_INTERVAL.get(value_priority or "", 2.0))


def schedule_after(a: UrlAction, result: str, value_priority: str | None, now: datetime) -> None:
    """When this action's page is checked again on its own (None = not before the next
    full crawl). Only issues a page check can decide are ever scheduled."""
    if verification_method(a.code) != "page_check" or a.status == ACTION_IGNORED:
        a.next_check_at = None
    elif result == RESULT_UNABLE:
        n = a.unable_count
        a.next_check_at = now + timedelta(hours=UNABLE_BACKOFF_HOURS[n - 1]) if 1 <= n <= len(UNABLE_BACKOFF_HOURS) else None
    elif result == RESULT_FIXED:
        # Watch for a regression only where it would matter most; the next crawl covers the rest.
        a.next_check_at = (now + _interval(30, value_priority)
                           if value_priority == "high" and a.priority in ("critical", "high") else None)
    elif result == RESULT_PARTIAL:
        a.next_check_at = now + _interval(14, value_priority)
    else:   # not fixed: check again later, then stop until the page changes or a new fix is reported
        a.next_check_at = None if a.not_fixed_count >= SUPPRESS_AFTER else now + _interval(14, value_priority)


# --------------------------------------------------------------------------- lifecycle

def get_or_create_action(db: Session, *, run: PageValueRun, score: PageValueScore, row: CrawlUrl, code: str,
                         user_id: str | None = None) -> UrlAction:
    """The action row for one recommendation of one page, created from the page's latest
    analysis the first time anyone acts on it. The relevant evidence of the crawl the
    issue was found in is kept as its "before"."""
    existing = (db.query(UrlAction).filter(UrlAction.monitor_id == run.monitor_id,
                                           UrlAction.normalized_key == row.normalized_key, UrlAction.code == code)
                .first())
    if existing is not None:
        return existing
    analysis = pi.latest_analysis(db, run.monitor_id, row.normalized_key)
    rec = next((r for r in ((analysis.result or {}).get("recommendations") or []) if r["code"] == code),
               None) if analysis else None
    if rec is None:
        raise ActionError(404, "This page has no such recommendation.")
    entry = _analysis_index(db, run.monitor_id, {row.normalized_key}).get(row.normalized_key)
    base = db.get(CrawlUrl, analysis.crawl_url_id) or row
    job = db.get(CrawlJob, analysis.crawl_job_id)
    ctx = _site_ctx(db, run, base.crawl_job_id, base.normalized_key, base.page_title, base.content_fingerprint)
    now = datetime.utcnow()
    a = UrlAction(organization_id=run.organization_id, monitor_id=run.monitor_id, normalized_key=row.normalized_key,
                  url=row.url, code=code, issue=rec["issue"], priority=rec.get("priority"),
                  page_type=score.business_page_type, status=derived_status(code, entry),
                  baseline_crawl_job_id=analysis.crawl_job_id,
                  baseline_evidence=vf.relevant_evidence(code, base.evidence, ctx, base.inbound_link_count),
                  baseline_observed=(rec.get("evidence") or [None])[0], rules_version=analysis.rules_version,
                  engine_version=job.engine_version if job else None, created_by_user_id=user_id,
                  check_priority=check_priority(score, rec.get("priority"), False), created_at=now, updated_at=now)
    db.add(a)
    try:
        db.commit()
    except IntegrityError:   # created concurrently
        db.rollback()
        return (db.query(UrlAction).filter(UrlAction.monitor_id == run.monitor_id,
                                           UrlAction.normalized_key == row.normalized_key, UrlAction.code == code)
                .one())
    return a


OPS = ("start", "ready", "recheck", "cancel_recheck", "ignore", "reopen", "note")


def apply_op(db: Session, a: UrlAction, op: str, *, user_id: str | None, score: PageValueScore | None = None,
             note: str | None = None, reason: str | None = None, now: datetime | None = None) -> UrlAction:
    """A user's change to one action. Marking a fix ready never verifies it; it queues a
    check (when a page check can decide the issue)."""
    now = now or datetime.utcnow()
    st = a.status
    note = (note or "").strip()[:2000] or None

    def bad(allowed):
        if st not in allowed:
            raise ActionError(409, f"This action is {STATUS_TEXT[st].lower()}, so that change isn't possible.")

    def move(to):
        _event(db, a, "status", user_id=user_id, from_status=a.status, to_status=to, note=note, at=now)
        a.status = to

    if op == "start":
        bad((ACTION_NEW, ACTION_NEEDS_ATTENTION, ACTION_NOT_FIXED, ACTION_PARTIAL, ACTION_REOPENED, ACTION_READY))
        move(ACTION_IN_PROGRESS)
        a.work_started_at = a.work_started_at or now
    elif op == "ready":
        bad((ACTION_NEW, ACTION_NEEDS_ATTENTION, ACTION_IN_PROGRESS, ACTION_NOT_FIXED, ACTION_PARTIAL,
             ACTION_REOPENED))
        move(ACTION_READY)
        a.ready_at = now
        a.work_started_at = a.work_started_at or now
        a.unable_count, a.not_fixed_count, a.check_error = 0, 0, None   # a new fix is new evidence
        a.next_check_at = now if verification_method(a.code) == "page_check" else None
    elif op == "recheck":
        if st == ACTION_IGNORED:
            raise ActionError(409, "This action is ignored. Reopen it before asking for a re-check.")
        method = verification_method(a.code)
        if method == "performance":
            raise ActionError(409, "This is a performance pattern, so a page check can't confirm it. "
                                   "Its before-and-after performance is tracked instead.")
        if method == "site_crawl":
            raise ActionError(409, "This depends on links from other pages, so it is checked at the next full site crawl.")
        a.recheck_requested_at, a.next_check_at = now, now
        _event(db, a, "recheck_requested", user_id=user_id, note=note, at=now)
    elif op == "cancel_recheck":
        if a.next_check_at is None and a.recheck_requested_at is None:
            raise ActionError(409, "No re-check is queued for this action.")
        a.next_check_at, a.recheck_requested_at = None, None
        _event(db, a, "recheck_cancelled", user_id=user_id, at=now)
    elif op == "ignore":
        reason = (reason or "").strip()
        if len(reason) < 3:
            raise ActionError(422, "Give a short reason for ignoring this action.")
        bad(tuple(s for s in STATUS_TEXT if s != ACTION_IGNORED))
        note = reason[:500]
        move(ACTION_IGNORED)
        a.ignored_reason = note
        a.next_check_at = a.recheck_requested_at = a.next_snapshot_at = None
    elif op == "reopen":
        bad((ACTION_VERIFIED, ACTION_IGNORED))
        move(ACTION_REOPENED)
        a.ignored_reason = None
        a.not_fixed_count = 0
    elif op == "note":
        if not note:
            raise ActionError(422, "The note is empty.")
        _event(db, a, "note", user_id=user_id, note=note, at=now)
    else:
        raise ActionError(422, f"Unknown change. Use one of: {', '.join(OPS)}.")
    a.check_priority = check_priority(score, a.priority, a.recheck_requested_at is not None)
    a.updated_at = now
    db.commit()
    if op in ("start", "ready"):
        try:   # the "before" window ends the day before work started: capture it while it can be
            fix_impact.capture(db, a, periods=("before",))
        except Exception:   # noqa: BLE001 — performance snapshots never block the action itself
            db.rollback()
            logger.warning("before snapshot failed for action %s", a.id)
    return a


def apply_result(db: Session, a: UrlAction, check: dict, *, method: str, fingerprint: str | None,
                 score: PageValueScore | None, now: datetime | None = None) -> str:
    """Record one check of one action (before/after evidence kept on the event) and move
    the action accordingly. Only "fixed" from a real check verifies; a verified fix found
    again is reopened and alerted once."""
    now = now or datetime.utcnow()
    result = vf.outcome(a.code, a.baseline_evidence, check)
    before = a.status
    new = before
    regressed = newly_verified = False
    if result == RESULT_FIXED:
        a.not_fixed_count = a.unable_count = 0
        a.check_error = None
        if before != ACTION_VERIFIED:
            new, a.verified_at, newly_verified = ACTION_VERIFIED, now, True
    elif result in (RESULT_NOT_FIXED, RESULT_PARTIAL):
        a.unable_count, a.check_error = 0, None
        if result == RESULT_NOT_FIXED:
            a.not_fixed_count = (a.not_fixed_count or 0) + 1
            a.not_fixed_fingerprint = fingerprint
        else:
            a.not_fixed_count = 0
        if before == ACTION_VERIFIED:
            new, regressed = ACTION_REOPENED, True
        elif before in (ACTION_READY, ACTION_PARTIAL, ACTION_NOT_FIXED):
            new = result
        # in progress / new / needs attention / reopened: the work isn't claimed done yet — keep it
    else:
        a.unable_count = (a.unable_count or 0) + 1
        a.check_error = check.get("reason")
    _event(db, a, "verification", from_status=before, to_status=new if new != before else None, at=now, detail={
        "result": result, "method": method, "rules_version": pi.RULES_VERSION,
        "before": a.baseline_evidence, "before_observed": a.baseline_observed,
        "after": check.get("evidence"), "after_observed": check.get("observed"), "reason": check.get("reason")})
    a.status = new
    a.last_result, a.last_checked_at, a.last_check_method = result, now, method
    a.recheck_requested_at = None
    schedule_after(a, result, score.priority if score else None, now)
    a.check_priority = check_priority(score, a.priority, False)
    a.updated_at = now
    db.commit()
    if regressed:
        _raise_alert(db, a.monitor_id, a.organization_id, "url_fix_regressed",
                     "critical" if a.priority == "critical" else "warning",
                     f"A verified fix came back: {a.issue}",
                     f"{a.url}: the issue verified as fixed on {a.verified_at:%Y-%m-%d} was found again by a "
                     f"{METHOD_TEXT[method]}.", {"key": f"{a.normalized_key}:{a.code}", "url": a.url,
                                                   "code": a.code, "action_id": a.id})
    if newly_verified:
        try:
            fix_impact.capture(db, a)
        except Exception:   # noqa: BLE001
            db.rollback()
            a.next_snapshot_at = now + timedelta(days=1)
            db.commit()
    return result


def _raise_alert(db: Session, monitor_id: str, org_id: str, atype: str, severity: str, title: str, message: str,
                 detail: dict) -> bool:
    """One open alert per page+issue: a repeat finding while it is open adds nothing."""
    for al in (db.query(Alert).filter(Alert.monitor_id == monitor_id, Alert.type == atype,
                                      Alert.status == ALERT_OPEN).all()):
        if (al.detail or {}).get("key") == detail["key"]:
            return False
    db.add(Alert(monitor_id=monitor_id, organization_id=org_id, type=atype, severity=severity, title=title[:200],
                 message=message[:500], detail=detail))
    db.commit()
    return True


# --------------------------------------------------------------------------- checks

async def check_page(db: Session, run: PageValueRun | None, actions: list[UrlAction],
                     now: datetime | None = None) -> list[str]:
    """Fetch one page once and decide every due action on it."""
    now = now or datetime.utcnow()
    a0 = actions[0]
    row, score = _page_refs(db, run, a0.normalized_key)
    evidence, err = await vf.fetch_page_evidence(a0.url, crawl_depth=row.discovery_depth if row else 0,
                                                 expected_key=a0.normalized_key)
    fp = content_fingerprint(evidence) if evidence else None
    checks = {}
    if evidence is not None:
        ctx = _site_ctx(db, run, row.crawl_job_id if row else None, a0.normalized_key, evidence.get("title"), fp)
        checks = vf.evaluate_codes([a.code for a in actions], url=a0.url, page_type=a0.page_type or "other",
                                   classification_status=score.classification_status if score else "likely",
                                   evidence=evidence, crawl_page_type=row.page_type if row else None, ctx=ctx)
    out = []
    for a in actions:
        check = checks.get(a.code) or {"present": None, "reason": err, "observed": None, "evidence": {}}
        out.append(apply_result(db, a, check, method="recrawl", fingerprint=fp, score=score, now=now))
    return out


def checks_today(db: Session, org_id: str, now: datetime) -> int:
    midnight = datetime.combine(now.date(), datetime.min.time())
    return (db.query(func.count(UrlAction.id))
            .filter(UrlAction.organization_id == org_id, UrlAction.last_check_method == "recrawl",
                    UrlAction.last_checked_at >= midnight).scalar() or 0)


async def run_due_checks(db: Session, monitor, now: datetime | None = None) -> dict:
    """One bounded unit of the url_recheck job: the highest-priority due pages (one fetch
    each), then due performance snapshots. Each page is isolated: a failure is recorded on
    its actions and retried with backoff, never stopping the others. A check cleared by
    the user while the job runs is skipped (cooperative cancellation)."""
    now = now or datetime.utcnow()
    run = pv.latest_run(db, monitor.id, PV_COMPLETED)
    counts = {"pages": 0, "checked": 0, "failed_pages": 0, "snapshots": 0, "more": False, "limited": False}
    due = (db.query(UrlAction)
           .filter(UrlAction.monitor_id == monitor.id, UrlAction.status != ACTION_IGNORED,
                   UrlAction.next_check_at.isnot(None), UrlAction.next_check_at <= now)
           .order_by(UrlAction.check_priority.desc(), UrlAction.next_check_at.asc()).limit(500).all())
    order: list[str] = []
    by_key: dict[str, list[UrlAction]] = {}
    for a in due:
        if a.normalized_key not in by_key:
            order.append(a.normalized_key)
        by_key.setdefault(a.normalized_key, []).append(a)
    budget = RECHECK_DAILY_LIMIT - checks_today(db, monitor.organization_id, now)
    if due and budget <= 0:
        tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
        for a in due:
            a.next_check_at = tomorrow
        db.commit()
        counts["limited"] = True
    else:
        for key in order[:RECHECK_BATCH_PAGES]:
            if budget <= 0:
                break
            acts = []
            for a in by_key[key]:
                db.refresh(a)
                if a.next_check_at is not None and a.next_check_at <= now and a.status != ACTION_IGNORED:
                    acts.append(a)
            acts = acts[:budget]
            if not acts:
                continue
            counts["pages"] += 1
            try:
                await check_page(db, run, acts, now)
                counts["checked"] += len(acts)
                budget -= len(acts)
            except Exception:   # noqa: BLE001 — one page never stops the batch
                db.rollback()
                logger.warning("re-check failed for a page of monitor %s", monitor.id)
                counts["failed_pages"] += 1
                for a in acts:
                    db.refresh(a)
                    a.unable_count = (a.unable_count or 0) + 1
                    a.check_error = "The check failed unexpectedly. It will be retried."
                    a.next_check_at = (now + timedelta(hours=UNABLE_BACKOFF_HOURS[min(a.unable_count, 3) - 1])
                                       if a.unable_count <= len(UNABLE_BACKOFF_HOURS) else None)
                db.commit()
        counts["more"] = len(order) > RECHECK_BATCH_PAGES and budget > 0
    snaps = (db.query(UrlAction).filter(UrlAction.monitor_id == monitor.id, UrlAction.next_snapshot_at.isnot(None),
                                        UrlAction.next_snapshot_at <= now).limit(SNAPSHOT_BATCH + 1).all())
    for a in snaps[:SNAPSHOT_BATCH]:
        try:
            fix_impact.capture(db, a, today=now.date())
            counts["snapshots"] += 1
        except Exception:   # noqa: BLE001
            db.rollback()
            a.next_snapshot_at = now + timedelta(days=1)
            db.commit()
    counts["more"] = counts["more"] or len(snaps) > SNAPSHOT_BATCH
    return counts


def sync_after_analysis(db: Session, run: PageValueRun) -> dict:
    """After a scoring run's page analysis completes: re-run every tracked action's check
    on the crawl's evidence (the crawl is a verification too), re-baseline across rule or
    engine changes, and alert on new serious issues of high-value pages."""
    job = db.get(CrawlJob, run.crawl_job_id)
    counts = {"checked": 0, "rebaselined": 0, "alerts": 0}
    actions = (db.query(UrlAction).filter(UrlAction.monitor_id == run.monitor_id,
                                          UrlAction.status != ACTION_IGNORED).all())
    keys = {a.normalized_key for a in actions}
    rows = {}
    if keys:
        for i in range(0, len(keys), 500):
            part = list(keys)[i:i + 500]
            rows.update({r.normalized_key: r for r in db.query(CrawlUrl).filter(
                CrawlUrl.crawl_job_id == run.crawl_job_id, CrawlUrl.normalized_key.in_(part)).all()})
    scores = {s.crawl_url_id: s for s in db.query(PageValueScore).filter(
        PageValueScore.run_id == run.id, PageValueScore.crawl_url_id.in_([r.id for r in rows.values()])).all()} if rows else {}
    for a in actions:
        row = rows.get(a.normalized_key)
        if (row is None or row.status != URL_CRAWLED or not row.evidence or a.code in vf.PERFORMANCE_CODES
                or a.baseline_crawl_job_id == run.crawl_job_id
                or (a.last_checked_at and row.crawled_at and row.crawled_at <= a.last_checked_at)):
            continue
        try:
            s = scores.get(row.id)
            ctx = _site_ctx(db, run, row.crawl_job_id, row.normalized_key, row.page_title, row.content_fingerprint)
            engine = job.engine_version if job else None
            if a.rules_version != pi.RULES_VERSION or (a.engine_version or None) != (engine or None):
                _event(db, a, "rules_changed", detail={
                    "from": {"rules_version": a.rules_version, "engine_version": a.engine_version},
                    "to": {"rules_version": pi.RULES_VERSION, "engine_version": engine}})
                a.rules_version, a.engine_version = pi.RULES_VERSION, engine
                a.baseline_crawl_job_id = run.crawl_job_id
                a.baseline_evidence = vf.relevant_evidence(a.code, row.evidence, ctx, row.inbound_link_count)
                a.updated_at = datetime.utcnow()
                db.commit()
                counts["rebaselined"] += 1
                continue
            check = vf.evaluate_codes([a.code], url=row.url, page_type=a.page_type or "other",
                                      classification_status=s.classification_status if s else "likely",
                                      evidence=row.evidence, crawl_page_type=row.page_type, ctx=ctx,
                                      inbound_links=row.inbound_link_count)[a.code]
            apply_result(db, a, check, method="crawl", fingerprint=row.content_fingerprint, score=s)
            counts["checked"] += 1
        except Exception:   # noqa: BLE001 — one action never stops the sync
            db.rollback()
            logger.warning("crawl verification failed for action %s", a.id)
    counts["alerts"] = _new_issue_alerts(db, run, job)
    return counts


def _new_issue_alerts(db: Session, run: PageValueRun, job: CrawlJob | None) -> int:
    """A confirmed high/critical issue that a high-value page didn't have on its previous
    comparable crawl. Potential issues, performance patterns, rule changes and lower
    severities never alert."""
    pages = (db.query(CrawlUrl.normalized_key, CrawlUrl.url)
             .join(PageValueScore, PageValueScore.crawl_url_id == CrawlUrl.id)
             .filter(PageValueScore.run_id == run.id, PageValueScore.priority == "high").limit(1000).all())
    if not pages:
        return 0
    urls = dict(pages)
    idx = _analysis_index(db, run.monitor_id, set(urls))
    engines: dict[str, str | None] = {}

    def engine(jid):
        if jid not in engines:
            j = db.get(CrawlJob, jid)
            engines[jid] = j.engine_version if j else None
        return engines[jid]

    raised = 0
    for key, e in idx.items():
        if raised >= MAX_NEW_ISSUE_ALERTS:
            break
        if e["crawl_job_id"] != run.crawl_job_id or e["prev_codes"] is None:
            continue
        if engine(e["prev_crawl_job_id"]) != (job.engine_version if job else None):
            continue
        for code in e["codes"]:
            meta = code_meta(code)
            if (code in e["prev_codes"] or code in vf.PERFORMANCE_CODES or meta["kind"] != "confirmed"
                    or meta["severity"] not in ("critical", "high")):
                continue
            if _raise_alert(db, run.monitor_id, run.organization_id, "url_new_issue",
                            "critical" if meta["severity"] == "critical" else "warning",
                            f"New issue on a high-value page: {meta['title']}",
                            f"{urls[key]}: found in the latest crawl, not in the previous one.",
                            {"key": f"{key}:{code}", "url": urls[key], "code": code}):
                raised += 1
            if raised >= MAX_NEW_ISSUE_ALERTS:
                break
    return raised


# --------------------------------------------------------------------------- change detection

def _changed(field, label, before, after):
    return {"field": field, "label": label, "before": before, "after": after}


def evidence_changes(prev: dict | None, cur: dict | None, prev_links: int | None, cur_links: int | None) -> list[dict]:
    """Meaningful differences between two crawls of one page. Small word-count and
    link-count movements are ignored."""
    p, c = prev or {}, cur or {}
    out = []
    for field, label in (("http_status", "HTTP status"), ("title", "Title"), ("meta_description", "Meta description"),
                         ("h1_texts", "H1 heading"), ("subheadings", "Section headings"),
                         ("canonical_url", "Canonical link"), ("noindex", "noindex directive")):
        if field in p and field in c and p.get(field) != c.get(field):
            b, a = p.get(field), c.get(field)
            if field == "subheadings":
                b, a = f"{len(b or [])} heading(s)", f"{len(a or [])} heading(s)"
            out.append(_changed(field, label, b, a))
    ps, cs = p.get("schema") or {}, c.get("schema") or {}
    pt, ct = sorted(ps.get("types") or []), sorted(cs.get("types") or [])
    if pt != ct or (ps.get("malformed") or 0) != (cs.get("malformed") or 0):
        out.append(_changed("schema", "Structured data", ", ".join(pt) or "none", ", ".join(ct) or "none"))
    pw, cw = p.get("word_count"), c.get("word_count")
    if pw is not None and cw is not None and abs(cw - pw) >= max(50, 0.2 * max(pw, 1)):
        out.append(_changed("word_count", "Amount of text", f"{pw} words", f"{cw} words"))
    if prev_links is not None and cur_links is not None and (
            (prev_links == 0) != (cur_links == 0) or abs(cur_links - prev_links) >= max(3, 0.5 * max(prev_links, 1))):
        out.append(_changed("inbound_links", "Internal links to the page", prev_links, cur_links))
    return out


def page_changes(db: Session, monitor_id: str, key: str) -> dict | None:
    """What changed on this page between its two latest analysed crawls, classified as an
    improvement, a regression, an unrelated change, no change, or not comparable."""
    analyses = (db.query(PageAnalysis)
                .options(load_only(PageAnalysis.id, PageAnalysis.crawl_job_id, PageAnalysis.crawl_url_id,
                                   PageAnalysis.recommendation_codes, PageAnalysis.rules_version,
                                   PageAnalysis.page_type, PageAnalysis.created_at))
                .filter(PageAnalysis.monitor_id == monitor_id, PageAnalysis.normalized_key == key)
                .order_by(PageAnalysis.created_at.desc(), PageAnalysis.id.desc()).limit(20).all())
    if not analyses:
        return None
    cur = analyses[0]
    prev = next((x for x in analyses[1:] if x.crawl_job_id != cur.crawl_job_id), None)
    if prev is None:
        return {"classification": "first_crawl", "changes": [], "new_issues": [], "resolved_issues": [],
                "text": "This is the first analysed crawl of this page, so there is nothing to compare yet."}
    crow, prow = db.get(CrawlUrl, cur.crawl_url_id), db.get(CrawlUrl, prev.crawl_url_id)
    cj, pj = db.get(CrawlJob, cur.crawl_job_id), db.get(CrawlJob, prev.crawl_job_id)
    changes = evidence_changes(prow.evidence if prow else None, crow.evidence if crow else None,
                               prow.inbound_link_count if prow else None, crow.inbound_link_count if crow else None)
    if cur.page_type != prev.page_type:
        changes.append(_changed("page_type", "Page type", prev.page_type, cur.page_type))
    if prow and crow and prow.content_fingerprint != crow.content_fingerprint and not changes:
        changes.append(_changed("content", "Page content", "earlier version", "changed"))
    base = {"changes": changes, "previous_crawl_at": pj.finished_at if pj else None,
            "latest_crawl_at": cj.finished_at if cj else None}
    comparable = (cur.rules_version == prev.rules_version
                  and (cj.engine_version if cj else None) == (pj.engine_version if pj else None))
    if not comparable:
        return {**base, "classification": "cannot_evaluate", "new_issues": [], "resolved_issues": [],
                "text": "The checks changed between these two crawls, so issue differences aren't compared."}
    content = lambda codes: {x for x in (codes or []) if x not in vf.PERFORMANCE_CODES}  # noqa: E731
    new = sorted(content(cur.recommendation_codes) - content(prev.recommendation_codes))
    resolved = sorted(content(prev.recommendation_codes) - content(cur.recommendation_codes))
    if new:
        cls, text = "regression", "New issues appeared since the previous crawl."
    elif resolved:
        cls, text = "improvement", "Issues found on the previous crawl are no longer found."
    elif changes:
        cls, text = "unrelated_change", "The page changed, but no checked issue appeared or disappeared."
    else:
        cls, text = "no_change", "No meaningful change since the previous crawl."
    return {**base, "classification": cls, "text": text,
            "new_issues": [{"code": x, "issue": code_meta(x)["title"]} for x in new],
            "resolved_issues": [{"code": x, "issue": code_meta(x)["title"]} for x in resolved]}


# --------------------------------------------------------------------------- read models

def _rank_page(page: dict, recs: list[dict], entry: dict | None, rows: dict[str, UrlAction],
               page_fp: str | None) -> tuple[list[dict], list[dict]]:
    """Candidate next actions on one page and the ones held back (not fixed twice with no
    new evidence since). Blocking issues (error status, noindex, canonical elsewhere)
    come before anything else on the page."""
    status = {r["code"]: (rows[r["code"]].status if r["code"] in rows else derived_status(r["code"], entry))
              for r in recs}
    open_recs = [r for r in recs if status[r["code"]] in TO_DO_STATUSES]
    blocking = {r["code"] for r in open_recs if r["code"] in BLOCKING_CODES}
    picks, held = [], []
    for r in open_recs:
        row = rows.get(r["code"])
        item = {"crawl_url_id": page["crawl_url_id"], "url": page["url"], "page_title": page.get("page_title"),
                "code": r["code"], "issue": r["issue"], "recommended_change": r.get("recommended_change"),
                "priority": r["priority"], "confidence": r["confidence"], "status": status[r["code"]],
                "score": page["score"]}
        if (row is not None and (row.not_fixed_count or 0) >= SUPPRESS_AFTER and row.not_fixed_fingerprint
                and row.not_fixed_fingerprint == page_fp):
            held.append({**item, "reason": (f"Found not fixed {row.not_fixed_count} times with no change to the page "
                                            "since. Review the approach before trying again.")})
            continue
        if blocking and r["code"] not in blocking:
            continue
        reasons = [f"Business Value Score {round(page['score'])} ({page['priority']} value page).",
                   f"{r['priority'].capitalize()} priority, {r['confidence']} confidence."]
        boost = 1.0
        if status[r["code"]] == ACTION_REOPENED:
            boost, _ = 1.3, reasons.append("It was fixed before and has come back.")
        elif status[r["code"]] == ACTION_PARTIAL:
            boost, _ = 1.2, reasons.append("Partly fixed at the last check.")
        if r["code"] in blocking and len(open_recs) > 1:
            reasons.append("Fix this first: other changes to this page depend on it.")
        if r.get("dependencies"):
            reasons.append("Needs: " + "; ".join(r["dependencies"]))
        weight = ((page["score"] or 0) + 10) * SEVERITY_WEIGHT.get(r["priority"], 1) \
            * CONFIDENCE_WEIGHT.get(r["confidence"], 0.6) * boost
        picks.append({**item, "reasons": reasons, "weight": round(weight, 2)})
    return picks, held


def _load(db: Session, monitor_id: str):
    run = pv.latest_run(db, monitor_id, PV_COMPLETED)
    if run is None:
        return None
    rows = _rows_by_key(db, monitor_id)
    q = (db.query(PageValueScore.crawl_url_id, PageValueScore.url, PageValueScore.page_title, PageValueScore.score,
                  PageValueScore.priority, PageValueScore.is_commercial, PageValueScore.business_page_type,
                  CrawlUrl.normalized_key, CrawlUrl.crawled_at, CrawlUrl.content_fingerprint)
         .join(CrawlUrl, CrawlUrl.id == PageValueScore.crawl_url_id).filter(PageValueScore.run_id == run.id))
    tracked = list(rows)
    cond = PageValueScore.priority.in_(IMPORTANT_PRIORITIES)
    if tracked and len(tracked) <= 500:
        cond = or_(cond, CrawlUrl.normalized_key.in_(tracked))
    elif tracked:
        cond = None
    pages = [p._asdict() for p in (q.filter(cond) if cond is not None else q).all()]
    if cond is None:
        pages = [p for p in pages if p["priority"] in IMPORTANT_PRIORITIES or p["normalized_key"] in rows]
    pages.sort(key=lambda p: (-(p["score"] or 0), p["crawl_url_id"]))
    idx = _analysis_index(db, monitor_id, {p["normalized_key"] for p in pages})
    return run, pages, rows, idx


def _page_summary(p: dict, entry: dict | None, rows: dict[str, UrlAction]) -> dict:
    codes = (entry or {}).get("codes") or []
    status = {c: (rows[c].status if c in rows else derived_status(c, entry)) for c in codes}
    open_codes = [c for c in codes if status[c] in OPEN_STATUSES]
    open_codes += [c for c, r in rows.items() if c not in status and r.status in OPEN_STATUSES]
    for c, r in rows.items():
        status.setdefault(c, r.status)
    main = open_codes[0] if open_codes else None
    checked = [r for r in rows.values() if r.last_checked_at]
    latest = max(checked, key=lambda r: r.last_checked_at) if checked else None
    nexts = [r.next_check_at for r in rows.values() if r.next_check_at and r.status != ACTION_IGNORED]
    last_checked = max([d for d in [p.get("crawled_at"), latest.last_checked_at if latest else None] if d],
                       default=None)
    return {
        "crawl_url_id": p["crawl_url_id"], "url": p["url"], "page_title": p["page_title"],
        "page_type": p["business_page_type"], "score": p["score"], "priority": p["priority"],
        "main_issue": None if main is None else {
            "code": main, "issue": rows[main].issue if main in rows else code_meta(main)["title"],
            "status": status[main]},
        "open_issues": len(open_codes), "statuses": sorted(set(status.values())),
        "verification": None if latest is None else {"result": latest.last_result, "at": latest.last_checked_at,
                                                     "method": latest.last_check_method, "code": latest.code},
        "last_checked_at": last_checked, "next_check_at": min(nexts) if nexts else None,
    }


def overview(db: Session, monitor_id: str, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    loaded = _load(db, monitor_id)
    if loaded is None:
        return {"available": False,
                "reason": "Pages haven't been scored yet. Run a site crawl and Page Opportunities first."}
    run, pages, rows, idx = loaded
    summaries = [_page_summary(p, idx.get(p["normalized_key"]), rows.get(p["normalized_key"], {})) for p in pages]
    all_rows = [r for d in rows.values() for r in d.values()]
    by_status = {s: 0 for s in STATUS_TEXT}
    for r in all_rows:
        by_status[r.status] += 1
    org_id = run.organization_id
    nxt, held = next_actions(db, run, pages, rows, idx)
    recent = sorted((r for r in all_rows if r.verified_at), key=lambda r: r.verified_at, reverse=True)[:10]
    impact = []
    for r in recent:
        cmp = fix_impact.comparison(db, r)
        if cmp:
            impact.append({"action_id": r.id, "url": r.url, "code": r.code, "issue": r.issue,
                           "verified_at": r.verified_at, "status": r.status, "comparison": cmp})
    queued = [r for r in all_rows if r.next_check_at and r.status != ACTION_IGNORED]
    return {
        "available": True,
        "run": {"id": run.id, "crawl_job_id": run.crawl_job_id, "analysis_status": run.analysis_status,
                "scored_at": run.finished_at},
        "counts": {"important_urls": len(pages), "open_issues": sum(s["open_issues"] for s in summaries),
                   "in_progress": by_status[ACTION_IN_PROGRESS], "awaiting_verification": by_status[ACTION_READY],
                   "verified": by_status[ACTION_VERIFIED], "reopened": by_status[ACTION_REOPENED],
                   "not_fixed": by_status[ACTION_NOT_FIXED], "partially_fixed": by_status[ACTION_PARTIAL],
                   "ignored": by_status[ACTION_IGNORED]},
        "rechecks": {"scheduled": len(queued), "due_now": sum(1 for r in queued if r.next_check_at <= now),
                     "requested": sum(1 for r in queued if r.recheck_requested_at),
                     "next_at": min((r.next_check_at for r in queued), default=None),
                     "used_today": checks_today(db, org_id, now), "daily_limit": RECHECK_DAILY_LIMIT},
        "next_actions": nxt, "held_back": held, "impact": impact,
    }


def next_actions(db: Session, run: PageValueRun, pages: list[dict], rows: dict, idx: dict,
                 limit: int = 5) -> tuple[list[dict], list[dict]]:
    """The next best actions across the website's most valuable pages."""
    cands = [p for p in pages if (idx.get(p["normalized_key"]) or {}).get("codes")][:NEXT_ACTION_PAGES]
    if not cands:
        return [], []
    ids = [idx[p["normalized_key"]]["id"] for p in cands]
    results = dict(db.query(PageAnalysis.id, PageAnalysis.result).filter(PageAnalysis.id.in_(ids)).all())
    picks, held = [], []
    for p in cands:
        e = idx[p["normalized_key"]]
        recs = (results.get(e["id"]) or {}).get("recommendations") or []
        a, h = _rank_page(p, recs, e, rows.get(p["normalized_key"], {}), p.get("content_fingerprint"))
        picks += a
        held += h
    picks.sort(key=lambda x: (-x["weight"], x["crawl_url_id"], x["code"]))
    return picks[:limit], held[:limit]


def url_list(db: Session, monitor_id: str, *, status: str | None = None, q: str | None = None,
             priority: str | None = None, page: int = 1, page_size: int = 25) -> dict:
    loaded = _load(db, monitor_id)
    if loaded is None:
        return {"total": 0, "page": page, "page_size": page_size, "urls": []}
    _, pages, rows, idx = loaded
    out = []
    needle = (q or "").strip().lower()
    for p in pages:
        if priority and p["priority"] != priority:
            continue
        if needle and needle not in (p["url"] or "").lower() and needle not in (p["page_title"] or "").lower():
            continue
        s = _page_summary(p, idx.get(p["normalized_key"]), rows.get(p["normalized_key"], {}))
        if status == "open" and not s["open_issues"]:
            continue
        if status and status != "open" and status not in s["statuses"]:
            continue
        out.append(s)
    start = (page - 1) * page_size
    return {"total": len(out), "page": page, "page_size": page_size, "urls": out[start:start + page_size]}


def _action_dict(code: str, rec: dict | None, row: UrlAction | None, entry: dict | None) -> dict:
    meta = code_meta(code)
    return {
        "code": code, "issue": (rec or {}).get("issue") or (row.issue if row else meta["title"]),
        "priority": (rec or {}).get("priority") or (row.priority if row else meta["severity"]),
        "confidence": (rec or {}).get("confidence"), "section": (rec or {}).get("section"),
        "recommended_change": (rec or {}).get("recommended_change"),
        "action_center_finding": (rec or {}).get("action_center_finding"),
        "still_recommended": rec is not None,
        "status": row.status if row else derived_status(code, entry), "tracked": row is not None,
        "action_id": row.id if row else None, "ignored_reason": row.ignored_reason if row else None,
        "verification_method": verification_method(code),
        "last_result": row.last_result if row else None, "last_checked_at": row.last_checked_at if row else None,
        "last_check_method": row.last_check_method if row else None,
        "check_error": row.check_error if row else None, "next_check_at": row.next_check_at if row else None,
        "recheck_requested": bool(row and row.recheck_requested_at), "verified_at": row.verified_at if row else None,
        "not_fixed_count": row.not_fixed_count if row else 0,
    }


def _timeline(db: Session, monitor_id: str, key: str, titles: dict[str, str]) -> list[dict]:
    """Stored events, plus when each issue was first detected (from the analysis history)."""
    out = []
    first_seen: dict[str, tuple] = {}
    for a in (db.query(PageAnalysis).options(load_only(PageAnalysis.recommendation_codes, PageAnalysis.created_at,
                                                       PageAnalysis.crawl_job_id))
              .filter(PageAnalysis.monitor_id == monitor_id, PageAnalysis.normalized_key == key)
              .order_by(PageAnalysis.created_at.asc()).limit(50)):
        for c in a.recommendation_codes or []:
            first_seen.setdefault(c, (a.created_at, a.crawl_job_id))
    for c, (at, _job) in first_seen.items():
        out.append({"at": at, "kind": "detected", "code": c, "issue": titles.get(c) or code_meta(c)["title"],
                    "text": "Issue detected in a crawl; recommendation created."})
    for e in (db.query(UrlActionEvent).filter(UrlActionEvent.monitor_id == monitor_id,
                                              UrlActionEvent.normalized_key == key)
              .order_by(UrlActionEvent.created_at.desc()).limit(150)):
        d = e.detail or {}
        if e.kind == "status":
            text = {ACTION_IN_PROGRESS: "Marked in progress", ACTION_READY: "Marked fixed, ready for verification",
                    ACTION_IGNORED: "Ignored", ACTION_REOPENED: "Reopened"}.get(e.to_status, f"Status: {STATUS_TEXT.get(e.to_status, e.to_status)}")
        elif e.kind == "note":
            text = "Note added"
        elif e.kind == "recheck_requested":
            text = "Re-check requested"
        elif e.kind == "recheck_cancelled":
            text = "Queued re-check cancelled"
        elif e.kind == "verification":
            text = f"Verification by {METHOD_TEXT.get(d.get('method'), 'check')}: {RESULT_TEXT.get(d.get('result'), d.get('result'))}"
            if e.to_status == ACTION_REOPENED:
                text += " (the verified fix came back, so it was reopened)"
        elif e.kind == "rules_changed":
            text = ("The checks were updated, so the latest crawl became the new baseline. No improvement or "
                    "regression is claimed from that comparison.")
        elif e.kind == "snapshot":
            w = d.get("window") or [None, None]
            text = (f"Performance snapshot complete: {fix_impact.SOURCE_LABEL.get(d.get('source'), d.get('source'))}, "
                    f"{d.get('period')} the fix ({w[0]} to {w[1]})")
        else:
            text = e.kind
        out.append({"at": e.created_at, "kind": e.kind, "code": e.code,
                    "issue": titles.get(e.code) or (code_meta(e.code)["title"] if e.code else None),
                    "text": text, "note": e.note, "from_status": e.from_status, "to_status": e.to_status,
                    "detail": d or None})
    out.sort(key=lambda x: (x["at"] or datetime.min), reverse=True)
    return out


def page_record(db: Session, run: PageValueRun, score: PageValueScore, row: CrawlUrl) -> dict:
    """The unified optimization record of one URL. References its sources; copies none."""
    key = row.normalized_key
    analysis = pi.latest_analysis(db, run.monitor_id, key)
    entry = _analysis_index(db, run.monitor_id, {key}).get(key)
    rows = {a.code: a for a in db.query(UrlAction).filter(UrlAction.monitor_id == run.monitor_id,
                                                          UrlAction.normalized_key == key).all()}
    recs = ((analysis.result or {}).get("recommendations") or []) if analysis else []
    actions = [_action_dict(r["code"], r, rows.get(r["code"]), entry) for r in recs]
    rec_codes = {r["code"] for r in recs}
    earlier = [_action_dict(c, None, a, entry) for c, a in rows.items() if c not in rec_codes]
    page = {"crawl_url_id": row.id, "url": row.url, "page_title": row.page_title, "score": score.score,
            "priority": score.priority}
    picks, held = _rank_page(page, recs, entry, rows, row.content_fingerprint)
    picks.sort(key=lambda x: -x["weight"])
    citations = next((c for c in ((analysis.result or {}).get("checks") or []) if c.get("code") == "citations"),
                     None) if analysis else None
    titles = {r["code"]: r["issue"] for r in recs} | {c: a.issue for c, a in rows.items()}
    impact = []
    for a in rows.values():
        cmp = fix_impact.comparison(db, a)
        if cmp:
            impact.append({"code": a.code, "issue": a.issue, "status": a.status, "verified_at": a.verified_at,
                           "comparison": cmp})
    return {
        "page": {"crawl_url_id": row.id, "url": row.url, "page_title": row.page_title,
                 "page_type": score.business_page_type, "crawled_at": row.crawled_at},
        "value": {"score": score.score, "priority": score.priority, "is_commercial": score.is_commercial},
        "measured": {k: getattr(score, k) for k in ("ai_sessions", "organic_sessions", "total_sessions", "conversions",
                                                    "conversion_source", "revenue", "revenue_currency", "gsc_clicks",
                                                    "gsc_impressions", "gsc_position")},
        "measured_window": (run.inputs or {}).get("window"),
        "ai_visibility": None if citations is None else {"status": citations["status"],
                                                         "observed": citations.get("observed"),
                                                         "detail": citations.get("detail")},
        "analysis": None if analysis is None else {"analyzed_at": analysis.created_at,
                                                   "rules_version": analysis.rules_version,
                                                   "crawl_job_id": analysis.crawl_job_id},
        "actions": actions, "earlier_actions": earlier,
        "next_action": picks[0] if picks else None, "held_back": held,
        "change": page_changes(db, run.monitor_id, key),
        "timeline": _timeline(db, run.monitor_id, key, titles),
        "impact": impact, "impact_disclaimer": fix_impact.DISCLAIMER,
    }


def cancel_scheduled_checks(db: Session, monitor_id: str, user_id: str | None) -> int:
    """Cancel every queued re-check of a website. A running batch skips pages whose checks
    were cleared; a pending batch has nothing left to do."""
    from app.db.models import JOB_COMPLETED, JOB_PENDING, ScheduledJob
    now = datetime.utcnow()
    acts = (db.query(UrlAction).filter(UrlAction.monitor_id == monitor_id,
                                       or_(UrlAction.next_check_at.isnot(None),
                                           UrlAction.recheck_requested_at.isnot(None))).all())
    for a in acts:
        a.next_check_at = a.recheck_requested_at = None
        a.updated_at = now
        _event(db, a, "recheck_cancelled", user_id=user_id, at=now)
    (db.query(ScheduledJob).filter(ScheduledJob.recheck_monitor_id == monitor_id, ScheduledJob.status == JOB_PENDING)
     .update({ScheduledJob.status: JOB_COMPLETED, ScheduledJob.finished_at: now, ScheduledJob.updated_at: now,
              ScheduledJob.error: "cancelled by a user"}, synchronize_session=False))
    db.commit()
    return len(acts)
