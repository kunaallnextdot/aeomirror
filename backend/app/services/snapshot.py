"""Change-attribution snapshots.

Capture a NORMALISED, DETERMINISTIC structure of a scanned page so a later scan's score
movement can be explained by diffing consecutive snapshots (see snapshot_diff).

Determinism is the whole game: two scans of an unchanged page MUST produce byte-identical
payloads, or the diff engine invents phantom changes and the feature loses trust. So we
sort every collection and strip volatile values (timestamps, nonces, session ids,
cache-buster query params) — and the payload carries NO timestamp of its own (the row's
created_at lives on the table, never in the diffed payload).

`SCHEMA_VERSION` is stamped into every payload; the diff engine refuses to compare across
versions rather than emit garbage.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Monitor, ScanSnapshot
from app.scanner.signals.base import SignalContext

SCHEMA_VERSION = 1

# Query params that are cache-busters / nonces / session ids — stripped from any URL so
# the same page doesn't look "changed" every scan.
_VOLATILE_QS = {"v", "_", "t", "ts", "cb", "cache", "cachebust", "nonce", "sid",
                "session", "sessionid", "token", "timestamp", "rand", "r"}
# JSON-LD property names whose VALUES move on every render — excluded from a block's
# key-field signature.
_VOLATILE_KEYS = {"datemodified", "datepublished", "datecreated", "uploaddate",
                  "expires", "nonce", "token", "sessionid", "timestamp"}


def _strip_url(url: str) -> str:
    """Drop the fragment and any cache-buster query params so a URL is stable."""
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip()
    qs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
          if k.lower() not in _VOLATILE_QS]
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(sorted(qs)), ""))


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


# ------------------------------- per-section extraction -------------------------------
def _schema_blocks(ctx: SignalContext) -> list[dict]:
    """Sorted list of {type, source_url, key_fields}. @graph is flattened; volatile
    property names are excluded from key_fields."""
    from app.scanner.schema_extract import collect_nodes, types_of

    blocks = []
    for node in collect_nodes(ctx.jsonld):
        if not isinstance(node, dict):
            continue
        types = sorted(str(t) for t in types_of(node))
        if not types:
            continue
        key_fields = sorted(k for k in node.keys()
                            if isinstance(k, str) and k.lower() not in _VOLATILE_KEYS)
        src = node.get("url") or node.get("@id") or ctx.url
        blocks.append({"type": ", ".join(types),
                       "source_url": _strip_url(str(src)),
                       "key_fields": key_fields})
    # Deterministic order.
    blocks.sort(key=lambda b: (b["type"], b["source_url"], tuple(b["key_fields"])))
    return blocks


def _meta_tags(ctx: SignalContext) -> dict:
    """title, description, robots, canonical, and og:* — as a sorted key/value dict with
    volatile URL params stripped."""
    soup = ctx.soup
    meta: dict[str, str] = {}
    if soup.title and soup.title.string:
        meta["title"] = _clean_text(soup.title.string)
    for name in ("description", "robots"):
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            meta[name] = _clean_text(tag["content"])
    canon = soup.find("link", attrs={"rel": "canonical"})
    if canon and canon.get("href"):
        meta["canonical"] = _strip_url(canon["href"])
    for tag in soup.find_all("meta", attrs={"property": re.compile(r"^og:", re.I)}):
        prop = tag.get("property", "").lower()
        val = tag.get("content")
        if prop and val:
            meta[prop] = _strip_url(val) if prop == "og:url" else _clean_text(val)
    return dict(sorted(meta.items()))


def _headings(ctx: SignalContext) -> list[dict]:
    """Ordered heading hierarchy: [{level, text}] in document order (order is meaningful,
    so it is NOT sorted)."""
    out = []
    for tag in ctx.soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        out.append({"level": int(tag.name[1]), "text": _clean_text(tag.get_text())})
    return out


def _robots(ctx: SignalContext) -> dict:
    """robots.txt: normalised raw + a deterministic parsed view."""
    raw_lines = [ln.strip() for ln in (ctx.robots_txt or "").splitlines()]
    raw = "\n".join(ln for ln in raw_lines if ln and not ln.startswith("#"))
    parsed: dict[str, list[str]] = {}
    current: list[str] = []
    for ln in raw.splitlines():
        if ":" not in ln:
            continue
        field, _, value = ln.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            current = [value.lower()]
            parsed.setdefault(value.lower(), [])
        elif field in ("allow", "disallow") and current:
            for a in current:
                parsed.setdefault(a, []).append(f"{field} {value}")
    return {"raw": raw, "parsed": {k: sorted(v) for k, v in sorted(parsed.items())}}


def _sitemap(ctx: SignalContext) -> dict:
    xml = ctx.sitemap_xml or ""
    return {"present": bool(ctx.sitemap_present),
            "url_count": len(re.findall(r"<loc>", xml, flags=re.I))}


def _llms(ctx: SignalContext) -> dict:
    # The fetch layer records presence only (a 200 probe), so HTTP status isn't available.
    return {"present": bool(ctx.llms_txt_present)}


def _crawler(crawler_access: dict | None) -> dict | None:
    if not crawler_access:
        return None
    bots = {b["key"]: b.get("status") for b in crawler_access.get("bots", [])}
    return {"site_unreachable": bool(crawler_access.get("site_unreachable")),
            "bots": dict(sorted(bots.items()))}


def build_snapshot(page, crawler_access: dict | None = None) -> dict:
    """Deterministic snapshot payload for one scanned page. Same page in => same dict."""
    ctx = SignalContext(page)
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_blocks": _schema_blocks(ctx),
        "meta": _meta_tags(ctx),
        "headings": _headings(ctx),
        "robots": _robots(ctx),
        "llms_txt": _llms(ctx),
        "sitemap": _sitemap(ctx),
        "crawler_access": _crawler(crawler_access),
        "word_count": len((ctx.text or "").split()),
        "page_count": 1,
    }


# ------------------------------- persistence -------------------------------
def previous_snapshot(db: Session, monitor_id: str | None) -> ScanSnapshot | None:
    """The most recent snapshot for a monitor (the diff baseline). Scoped by monitor_id,
    which belongs to exactly one org — so a diff can never cross orgs."""
    if not monitor_id:
        return None
    return (db.query(ScanSnapshot)
            .filter(ScanSnapshot.monitor_id == monitor_id)
            .order_by(ScanSnapshot.created_at.desc(), ScanSnapshot.id.desc())
            .first())


def create_snapshot(db: Session, *, scan_id: str, monitor_id: str | None,
                    org_id: str, payload: dict) -> ScanSnapshot:
    row = ScanSnapshot(
        scan_id=scan_id, monitor_id=monitor_id, organization_id=org_id,
        schema_version=payload.get("schema_version", SCHEMA_VERSION), payload=payload)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ------------------------------- retention -------------------------------
def purge_old_snapshots(db: Session, *, retention_days: int | None = None,
                        now: datetime | None = None) -> int:
    """Delete snapshots older than the retention window. NEVER deletes the most recent
    snapshot of an EXISTING monitor — active OR paused — even beyond the window, so a
    monitor keeps its diff baseline and recovers on resume. Snapshots whose monitor was
    deleted (or that were never monitor-scoped) are eligible. Returns the number deleted."""
    days = retention_days if retention_days is not None else settings.snapshot_retention_days
    cutoff = (now or datetime.utcnow()) - timedelta(days=days)

    old = db.query(ScanSnapshot).filter(ScanSnapshot.created_at < cutoff).all()
    if not old:
        return 0
    # Preserve the latest snapshot of every monitor that still exists (any status).
    keep: set[str] = set()
    for m in db.query(Monitor).all():
        latest = previous_snapshot(db, m.id)
        if latest:
            keep.add(latest.id)

    deleted = 0
    for row in old:
        if row.id in keep:
            continue
        db.delete(row)
        deleted += 1
    if deleted:
        db.commit()
    return deleted
