"""Public report shares: opaque-token creation, resolution, revocation, and the
whitelisted public payload.

Security invariants:
- The token is high-entropy (384-bit) and STORED so the owning org can retrieve its own
  active share URL. A share token is not a credential — it only grants read access to a
  report the org already owns and can view — so hashing it (which would make the URL
  unrecoverable) buys ~nothing against the only threat it addresses, a DB leak, in which
  the reports are directly readable anyway.
- A share resolves ONLY via a valid (matching token) + unexpired + unrevoked row.
  Callers turn any miss into a 404 that never distinguishes the three failure modes.
- The public payload is built by an explicit WHITELIST from the persisted report dict.
  It never includes scan_id, internal versions, the AI narrative _meta, per-page bulk
  URLs, or the scraped `evidence` block (page <title>, robots Disallow paths). The
  public read path never regenerates the report or its AI narrative.
"""
from __future__ import annotations

import logging

from app.core.security import generate_token, now_utc, token_expiry
from app.db.models import Report, ReportShare

log = logging.getLogger("app.reports.shares")


# ------------------------------- share lifecycle -------------------------------
def create_share(db, *, scan_id: str, org_id: str, user_id: str | None,
                 ttl_seconds: int) -> tuple[ReportShare, str]:
    """Mint a new share for a scan. Returns (row, token). The token is stored so the
    owner can retrieve the URL later (list endpoint)."""
    raw = generate_token()
    row = ReportShare(
        token=raw, scan_id=scan_id, organization_id=org_id,
        created_by=user_id, expires_at=token_expiry(ttl_seconds),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw


def active_shares(db, org_id: str) -> list[ReportShare]:
    """The org's live share links (unrevoked, unexpired), newest first."""
    return (db.query(ReportShare)
            .filter(ReportShare.organization_id == org_id,
                    ReportShare.revoked_at.is_(None),
                    ReportShare.expires_at > now_utc())
            .order_by(ReportShare.created_at.desc())
            .all())


def revoke_share(db, *, share_id: str, org_id: str) -> bool:
    """Revoke one of the org's shares (idempotent). Returns True if a matching active
    share was revoked, False if none matched (caller turns that into a 404)."""
    row = (db.query(ReportShare)
           .filter(ReportShare.id == share_id,
                   ReportShare.organization_id == org_id,
                   ReportShare.revoked_at.is_(None))
           .first())
    if not row:
        return False
    row.revoked_at = now_utc()
    db.commit()
    return True


def resolve_valid_share(db, raw_token: str) -> ReportShare | None:
    """Return the share for a raw token IFF it is valid: hash matches AND not revoked
    AND not expired. Otherwise None — the caller returns a 404 that never distinguishes
    invalid / expired / revoked."""
    if not raw_token:
        return None
    row = (db.query(ReportShare)
           .filter(ReportShare.token == raw_token)
           .first())
    if not row or row.revoked_at is not None or row.expires_at <= now_utc():
        return None
    return row


def record_view(db, share: ReportShare) -> None:
    """Count a public view (best-effort). Viewer IPs are NOT stored (count-only). A
    failed metrics write must NEVER break the content read: on error we log the
    exception type, roll back, and let the caller still serve the report."""
    try:
        share.view_count = (share.view_count or 0) + 1
        share.last_viewed_at = now_utc()
        db.commit()
    except Exception as e:  # noqa: BLE001 — a metrics write must not fail the read
        log.warning("report share view-count update failed (%s)", type(e).__name__)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def latest_persisted_report(db, scan_id: str) -> Report | None:
    """The most recent PERSISTED report row for a scan. The public path reads this
    only — it NEVER calls generate_report / the AI narrative path."""
    return (db.query(Report)
            .filter(Report.scan_id == scan_id)
            .order_by(Report.generated_at.desc())
            .first())


# ------------------------------- public payload (whitelist) -------------------------------
# Recommendation fields dropped from the public payload. `evidence` is the only place
# scraped page content lands (page <title>, robots Disallow paths, dates); everything
# else in a recommendation is generic template/scoring text.
_REC_DROP = {"evidence"}


def _public_recommendation(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in _REC_DROP}


def _public_insights(ins: dict | None) -> dict | None:
    """Public-safe insights: the negative-first score-loss breakdown + top problems, with
    the scraped `evidence` block dropped from every row (same rule as recommendations —
    evidence is the only place scraped page content lands). Derived scoring math (scores,
    weights, points_lost, issues text) is safe to share."""
    if not ins:
        return None
    def strip(row: dict) -> dict:
        return {k: v for k, v in row.items() if k != "evidence"}
    out = dict(ins)
    if isinstance(out.get("score_breakdown"), list):
        out["score_breakdown"] = [strip(r) for r in out["score_breakdown"]]
    if isinstance(out.get("top_problems"), list):
        out["top_problems"] = [strip(r) for r in out["top_problems"]]
    return out


def _stripped_ai(ai: dict | None) -> dict | None:
    """The persisted narrative WITHOUT internal provenance (_meta: model id, tier,
    timestamps). Never regenerated here — this is whatever is already stored."""
    if not ai:
        return None
    return {k: v for k, v in ai.items() if k != "_meta"}


def build_public_payload(report: dict, *, include_ai: bool) -> dict:
    """Purpose-built, whitelisted public view of a persisted report. Explicit allow-list
    — anything not named here (scan_id, report_version, scanner_version, generated_at,
    bulk page URLs, recommendation evidence) is excluded."""
    return {
        "domain": report.get("domain"),
        "url": report.get("url"),
        "scanned_at": report.get("scanned_at"),
        "scorecard": report.get("scorecard"),
        "recommendations": [_public_recommendation(r) for r in (report.get("recommendations") or [])],
        "recommendation_count": report.get("recommendation_count"),
        "insights": _public_insights(report.get("insights")),
        "ai": _stripped_ai(report.get("ai")) if include_ai else None,
    }
