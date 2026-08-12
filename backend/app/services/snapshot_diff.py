"""Snapshot diff engine — turns two consecutive snapshots into a typed, severity-graded
change list that EXPLAINS a score movement.

Each change: {category, change_type (added|removed|modified), path, old_value, new_value,
severity (CRITICAL|WARNING|INFO)}.

Contracts:
- No prior snapshot (first-ever scan) => empty list, NOT an error.
- Incompatible schema_version => raise SnapshotVersionMismatch (refuse to diff rather than
  emit garbage).
- Identical snapshots => empty list (the determinism guarantee).
"""
from __future__ import annotations

from collections import Counter

from app.services.snapshot import SCHEMA_VERSION

CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"

ADDED = "added"
REMOVED = "removed"
MODIFIED = "modified"

# Which crawler keys are "critical" (a block on them is CRITICAL). Sourced from the
# crawler feature so the two stay in lockstep.
try:
    from app.services.crawler_access import CRAWLERS as _CRAWLERS
    _CRITICAL_BOTS = {c["key"] for c in _CRAWLERS if c["critical"]}
    _BLOCKED_STATUSES = {"blocked_by_robots", "blocked_by_server"}
except Exception:   # pragma: no cover - defensive; crawler feature is a hard dep
    _CRITICAL_BOTS, _BLOCKED_STATUSES = set(), {"blocked_by_robots", "blocked_by_server"}


class SnapshotVersionMismatch(Exception):
    """Raised when two snapshots have incompatible schema_versions."""


def _c(category, change_type, path, old, new, severity) -> dict:
    return {"category": category, "change_type": change_type, "path": path,
            "old_value": old, "new_value": new, "severity": severity}


def _diff_schema(prev: dict, curr: dict) -> list[dict]:
    """schema.org blocks — a removed block type is CRITICAL, an added one INFO."""
    prev_types = Counter(b["type"] for b in prev.get("schema_blocks", []))
    curr_types = Counter(b["type"] for b in curr.get("schema_blocks", []))
    out = []
    for t in sorted((prev_types - curr_types).elements()):
        out.append(_c("schema", REMOVED, t, t, None, CRITICAL))
    for t in sorted((curr_types - prev_types).elements()):
        out.append(_c("schema", ADDED, t, None, t, INFO))
    return out


def _diff_crawler(prev: dict, curr: dict) -> list[dict]:
    p = prev.get("crawler_access") or {}
    c = curr.get("crawler_access") or {}
    out = []
    # Site became unreachable — CRITICAL.
    if c.get("site_unreachable") and not p.get("site_unreachable"):
        out.append(_c("availability", MODIFIED, "site", "reachable", "unreachable", CRITICAL))
    pb, cb = p.get("bots") or {}, c.get("bots") or {}
    for key in sorted(set(pb) | set(cb)):
        old, new = pb.get(key), cb.get(key)
        if old == new:
            continue
        became_blocked = old == "allowed" and new in _BLOCKED_STATUSES
        # A critical crawler newly blocked (incl. "robots.txt now blocks it") is CRITICAL;
        # any other crawler status change is INFO.
        sev = CRITICAL if (became_blocked and key in _CRITICAL_BOTS) else INFO
        out.append(_c("crawler_access", MODIFIED, key, old, new, sev))
    return out


def _diff_meta(prev: dict, curr: dict) -> list[dict]:
    p, c = prev.get("meta") or {}, curr.get("meta") or {}
    out = []
    # canonical removed => CRITICAL; added/changed => INFO.
    pc, cc = p.get("canonical"), c.get("canonical")
    if pc and not cc:
        out.append(_c("meta", REMOVED, "canonical", pc, None, CRITICAL))
    elif not pc and cc:
        out.append(_c("meta", ADDED, "canonical", None, cc, INFO))
    elif pc and cc and pc != cc:
        out.append(_c("meta", MODIFIED, "canonical", pc, cc, INFO))
    # description removed => WARNING; added/changed => INFO (minor text edit).
    pd, cd = p.get("description"), c.get("description")
    if pd and not cd:
        out.append(_c("meta", REMOVED, "description", pd, None, WARNING))
    elif not pd and cd:
        out.append(_c("meta", ADDED, "description", None, cd, INFO))
    elif pd and cd and pd != cd:
        out.append(_c("meta", MODIFIED, "description", pd, cd, INFO))
    # title change => INFO (minor edit).
    pt, ct = p.get("title"), c.get("title")
    if pt != ct and (pt or ct):
        out.append(_c("meta", MODIFIED, "title", pt, ct, INFO))
    return out


def _diff_llms(prev: dict, curr: dict) -> list[dict]:
    was = (prev.get("llms_txt") or {}).get("present")
    now = (curr.get("llms_txt") or {}).get("present")
    if was and not now:
        return [_c("llms_txt", REMOVED, "llms.txt", "present", "absent", WARNING)]
    if not was and now:
        return [_c("llms_txt", ADDED, "llms.txt", "absent", "present", INFO)]
    return []


def _diff_sitemap(prev: dict, curr: dict) -> list[dict]:
    was = (prev.get("sitemap") or {}).get("present")
    now = (curr.get("sitemap") or {}).get("present")
    if was and not now:
        return [_c("sitemap", REMOVED, "sitemap.xml", "present", "absent", WARNING)]
    return []


def _diff_headings(prev: dict, curr: dict) -> list[dict]:
    """H1 removed or duplicated (a change from a healthy single H1) => WARNING."""
    def h1(snap):
        return sum(1 for h in snap.get("headings", []) if h.get("level") == 1)
    ph, ch = h1(prev), h1(curr)
    if ph == 1 and ch == 0:
        return [_c("headings", REMOVED, "h1", 1, 0, WARNING)]
    if ph == 1 and ch > 1:
        return [_c("headings", MODIFIED, "h1", 1, ch, WARNING)]
    return []


def _diff_content(prev: dict, curr: dict) -> list[dict]:
    pw, cw = prev.get("word_count"), curr.get("word_count")
    if pw != cw:
        return [_c("content", MODIFIED, "word_count", pw, cw, INFO)]
    return []


def diff_snapshots(prev: dict | None, curr: dict) -> list[dict]:
    """Compare `curr` against the immediately-previous snapshot `prev`. Empty list when
    there is no prior snapshot; raises SnapshotVersionMismatch across incompatible
    schema_versions."""
    if prev is None:
        return []
    pv, cv = prev.get("schema_version"), curr.get("schema_version", SCHEMA_VERSION)
    if pv != cv:
        raise SnapshotVersionMismatch(f"snapshot schema_version {pv} != {cv}")

    changes: list[dict] = []
    changes += _diff_schema(prev, curr)
    changes += _diff_crawler(prev, curr)
    changes += _diff_meta(prev, curr)
    changes += _diff_llms(prev, curr)
    changes += _diff_sitemap(prev, curr)
    changes += _diff_headings(prev, curr)
    changes += _diff_content(prev, curr)
    return changes
