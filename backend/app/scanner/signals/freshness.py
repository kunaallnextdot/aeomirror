"""Freshness signal: last-modified, publish/updated dates, stale content."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.scanner.signals.base import SignalContext, SignalResult

ID, LABEL, WEIGHT = "freshness", "Freshness", 7

_STALE_DAYS = 730  # ~2 years
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _schema_date(ctx: SignalContext) -> str | None:
    found = {"v": None}

    def walk(o):
        if isinstance(o, dict):
            for k in ("dateModified", "datePublished"):
                if isinstance(o.get(k), str) and not found["v"]:
                    found["v"] = o[k]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for b in ctx.jsonld:
        walk(b)
    return found["v"]


def _age_days(date_str: str) -> int | None:
    m = _ISO.search(date_str or "")
    if not m:
        return None
    try:
        d = datetime.strptime(m.group(0), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except ValueError:
        return None


def analyze(ctx: SignalContext) -> SignalResult:
    soup = ctx.soup
    issues: list = []
    recs: list = []
    score = 0.0

    last_modified = ctx.header("last-modified")
    if last_modified:
        score += 20
    else:
        recs.append("Send a Last-Modified header so crawlers know when content changed.")

    schema_date = _schema_date(ctx)
    if schema_date:
        score += 35
    else:
        issues.append("No dateModified/datePublished in structured data.")
        recs.append("Add dateModified/datePublished to your schema.")

    time_tag = soup.find("time") or soup.find(
        attrs={"class": re.compile("date|published|updated", re.I)})
    if time_tag:
        score += 25
    else:
        issues.append("No visible published/updated date.")
        recs.append("Show a visible last-updated date on the page.")

    age = _age_days(schema_date or "")
    stale = age is not None and age > _STALE_DAYS
    if age is None:
        score += 20  # cannot assess age; don't penalize
    elif not stale:
        score += 20
    else:
        issues.append(f"Newest structured date is ~{age // 365} year(s) old — content may be stale.")
        recs.append("Refresh the content and update its dateModified.")

    return SignalResult.build(
        ID, LABEL, WEIGHT, score, issues=issues, recommendations=recs,
        evidence={
            "last_modified_header": last_modified or "none",
            "schema_date": schema_date or "none",
            "visible_date": bool(time_tag),
            "age_days": age, "stale": stale,
        },
    )
