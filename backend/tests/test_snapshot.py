"""Change Attribution Engine — snapshot extraction, the diff engine (severity mapping +
determinism + version gating), retention, and cross-org isolation."""
import asyncio
import json
import logging
from datetime import datetime, timedelta

import pytest

import app.api.routes_scan as rs
from app.core.ssrf import validate_url
from app.db.models import (
    MONITOR_ACTIVE, MONITOR_PAUSED, Monitor, Scan, ScanSnapshot,
)
from app.db.session import SessionLocal
from app.scanner.models import PageBundle
from app.services.snapshot import (
    build_snapshot, previous_snapshot, purge_old_snapshots,
)
from app.services.snapshot_diff import (
    CRITICAL, INFO, WARNING, SnapshotVersionMismatch, diff_snapshots,
)


def _snap(**over) -> dict:
    """A valid baseline snapshot payload; override keys per test."""
    base = {
        "schema_version": 1,
        "schema_blocks": [{"type": "Organization", "source_url": "", "key_fields": ["name", "url"]}],
        "meta": {"title": "Home", "description": "desc", "canonical": "https://x.test/"},
        "headings": [{"level": 1, "text": "Welcome"}],
        "robots": {"raw": "", "parsed": {}},
        "llms_txt": {"present": True},
        "sitemap": {"present": True, "url_count": 3},
        "crawler_access": {"site_unreachable": False, "bots": {"gptbot": "allowed", "ccbot": "allowed"}},
        "word_count": 100,
        "page_count": 1,
    }
    base.update(over)
    return base


# ------------------------------- diff: contracts -------------------------------
def test_first_scan_has_empty_diff():
    assert diff_snapshots(None, _snap()) == []


def _canon(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True)


def _page(html: str, *, headers: dict | None = None, url: str = "https://det.test/") -> PageBundle:
    from tests.test_scanner import GOOD_ROBOTS
    return PageBundle(url=url, html=html, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True, headers=headers or {})


def test_build_snapshot_is_deterministic_across_distinct_fetches():
    """FIX 3: build_snapshot() TWICE against the same HTML but with DIFFERENT surrounding
    fetch context (distinct response Date headers = distinct fetch timestamps). The
    payloads must be byte-identical after JSON serialisation with sorted keys — proving
    no fetch-time volatility leaks in."""
    from tests.test_scanner import GOOD_HTML
    a = build_snapshot(_page(GOOD_HTML, headers={"date": "Mon, 01 Jan 2026 00:00:00 GMT"}))
    b = build_snapshot(_page(GOOD_HTML, headers={"date": "Tue, 02 Sep 2026 13:37:59 GMT"}))
    assert _canon(a) == _canon(b)
    assert a["schema_version"] == 1 and a["page_count"] == 1


def _fixture_html(*, date: str, cb: str, order: tuple[str, str]) -> str:
    """Two JSON-LD blocks (Organization + WebSite) in `order`; the Organization carries a
    volatile dateModified, and the canonical URL carries a cache-buster query param."""
    blocks = {
        "Organization": ('{"@context":"https://schema.org","@type":"Organization",'
                         f'"name":"Acme","url":"https://det.test/","dateModified":"{date}"}}'),
        "WebSite": ('{"@context":"https://schema.org","@type":"WebSite",'
                    '"name":"Acme Site","url":"https://det.test/"}'),
    }
    scripts = "".join(f'<script type="application/ld+json">{blocks[t]}</script>' for t in order)
    return (f'<html><head><title>Home</title>'
            f'<link rel="canonical" href="https://det.test/?{cb}">'
            f'<meta name="description" content="Acme description">'
            f'{scripts}</head><body><h1>Welcome</h1><p>Hello there.</p></body></html>')


def test_build_snapshot_strips_volatiles_and_normalises_order():
    """FIX 3: two pages differing ONLY in volatile ways — a JSON-LD dateModified value, a
    cache-buster on the canonical URL, and schema blocks in a different source order —
    still produce byte-identical snapshots."""
    a = build_snapshot(_page(_fixture_html(date="2026-01-01", cb="v=111",
                                           order=("Organization", "WebSite"))))
    b = build_snapshot(_page(_fixture_html(date="2026-09-09", cb="cb=999",
                                           order=("WebSite", "Organization"))))
    assert _canon(a) == _canon(b)
    # And the stripping is real: no dateModified in key_fields, canonical has no query.
    assert all("dateModified" not in blk["key_fields"] for blk in a["schema_blocks"])
    assert a["meta"]["canonical"] == "https://det.test/"


def test_schema_version_mismatch_refuses_to_diff():
    prev, curr = _snap(schema_version=1), _snap(schema_version=2)
    with pytest.raises(SnapshotVersionMismatch):
        diff_snapshots(prev, curr)


# ------------------------------- diff: severity mapping -------------------------------
def test_schema_block_removed_is_critical():
    prev = _snap()
    curr = _snap(schema_blocks=[])
    changes = diff_snapshots(prev, curr)
    schema = [c for c in changes if c["category"] == "schema"]
    assert len(schema) == 1
    assert schema[0]["change_type"] == "removed"
    assert schema[0]["severity"] == CRITICAL
    assert schema[0]["path"] == "Organization"


def test_llms_txt_disappeared_is_warning():
    changes = diff_snapshots(_snap(), _snap(llms_txt={"present": False}))
    llms = [c for c in changes if c["category"] == "llms_txt"]
    assert len(llms) == 1 and llms[0]["severity"] == WARNING
    assert llms[0]["new_value"] == "absent"


def test_word_count_change_only_is_info():
    changes = diff_snapshots(_snap(), _snap(word_count=140))
    assert changes == [{"category": "content", "change_type": "modified", "path": "word_count",
                        "old_value": 100, "new_value": 140, "severity": INFO}]


def test_critical_crawler_newly_blocked_is_critical():
    prev = _snap()
    curr = _snap(crawler_access={"site_unreachable": False,
                                 "bots": {"gptbot": "blocked_by_robots", "ccbot": "allowed"}})
    changes = diff_snapshots(prev, curr)
    gpt = [c for c in changes if c["category"] == "crawler_access" and c["path"] == "gptbot"][0]
    assert gpt["severity"] == CRITICAL and gpt["new_value"] == "blocked_by_robots"


def test_noncritical_crawler_change_is_info():
    prev = _snap()
    curr = _snap(crawler_access={"site_unreachable": False,
                                 "bots": {"gptbot": "allowed", "ccbot": "blocked_by_server"}})
    cc = [c for c in diff_snapshots(prev, curr) if c["path"] == "ccbot"][0]
    assert cc["severity"] == INFO


def test_canonical_removed_is_critical_and_h1_removed_is_warning():
    prev = _snap()
    curr = _snap(meta={"title": "Home", "description": "desc"},   # canonical gone
                 headings=[{"level": 2, "text": "Sub"}])          # H1 gone
    changes = diff_snapshots(prev, curr)
    canon = [c for c in changes if c["path"] == "canonical"][0]
    h1 = [c for c in changes if c["category"] == "headings"][0]
    assert canon["severity"] == CRITICAL and canon["change_type"] == "removed"
    assert h1["severity"] == WARNING


# ------------------------------- persistence / retention / isolation -------------------------------
def _mk_monitor(db, org_id, status=MONITOR_ACTIVE):
    m = Monitor(organization_id=org_id, url=f"https://{org_id}.test/",
                normalized_url=f"{org_id}.test", status=status, frequency="weekly")
    db.add(m); db.commit(); db.refresh(m)
    return m


def _mk_snapshot(db, m, *, org_id, age_days, scan_id):
    row = ScanSnapshot(scan_id=scan_id, monitor_id=m.id, organization_id=org_id,
                       schema_version=1, payload=_snap(),
                       created_at=datetime.utcnow() - timedelta(days=age_days))
    db.add(row); db.commit(); db.refresh(row)
    return row


def test_retention_preserves_latest_for_active_and_paused_monitors():
    """FIX 4: the most recent snapshot is preserved for EVERY existing monitor (active OR
    paused), even beyond the window. Older ones, and orphan snapshots whose monitor was
    deleted, are pruned."""
    db = SessionLocal()
    try:
        ma = _mk_monitor(db, "org-ret-a", status=MONITOR_ACTIVE)
        a_old = _mk_snapshot(db, ma, org_id="org-ret-a", age_days=200, scan_id="a-old")
        a_latest = _mk_snapshot(db, ma, org_id="org-ret-a", age_days=120, scan_id="a-latest")
        mp = _mk_monitor(db, "org-ret-b", status=MONITOR_PAUSED)
        p_old = _mk_snapshot(db, mp, org_id="org-ret-b", age_days=200, scan_id="p-old")
        p_latest = _mk_snapshot(db, mp, org_id="org-ret-b", age_days=130, scan_id="p-latest")
        # Orphan: a snapshot whose monitor no longer exists — eligible for deletion.
        orphan = ScanSnapshot(scan_id="orph", monitor_id="ghost-monitor", organization_id="org-ret-c",
                              schema_version=1, payload=_snap(),
                              created_at=datetime.utcnow() - timedelta(days=200))
        db.add(orphan); db.commit(); db.refresh(orphan)

        deleted = purge_old_snapshots(db, retention_days=90)

        ids = {r.id for r in db.query(ScanSnapshot).all()}
        assert a_latest.id in ids and a_old.id not in ids       # active: latest kept
        assert p_latest.id in ids and p_old.id not in ids       # paused: latest kept (FIX 4)
        assert orphan.id not in ids                             # orphan pruned
        assert deleted == 3
    finally:
        db.close()


def test_paused_monitor_retains_its_most_recent_snapshot():
    """FIX 4 (focused): a paused monitor whose snapshots are ALL out-of-window keeps
    exactly its most recent one, so it has a baseline when resumed."""
    db = SessionLocal()
    try:
        m = _mk_monitor(db, "org-paused", status=MONITOR_PAUSED)
        _mk_snapshot(db, m, org_id="org-paused", age_days=300, scan_id="pm-old")
        latest = _mk_snapshot(db, m, org_id="org-paused", age_days=150, scan_id="pm-latest")
        purge_old_snapshots(db, retention_days=90)
        remaining = db.query(ScanSnapshot).filter(ScanSnapshot.monitor_id == m.id).all()
        assert len(remaining) == 1 and remaining[0].id == latest.id
    finally:
        db.close()


def test_version_mismatch_persists_baseline_and_monitor_recovers(monkeypatch, caplog):
    """FIX 2: a scan against a stale-version baseline must (i) persist the new v1 snapshot,
    (ii) yield an empty change_set + a WARNING (not raise), and (iii) let the NEXT scan
    diff normally — the monitor recovers on its own instead of deadlocking forever."""
    from tests.authutil import auth_client
    from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS

    client, obody = auth_client()
    org_id = obody["organization"]["id"]
    db = SessionLocal()
    try:
        m = Monitor(organization_id=org_id, url="https://recover.test/",
                    normalized_url="recover.test", status=MONITOR_ACTIVE, frequency="weekly")
        db.add(m); db.commit(); db.refresh(m)
        mid = m.id
        db.add(ScanSnapshot(scan_id="seed", monitor_id=mid, organization_id=org_id,
                            schema_version=0, payload={"schema_version": 0},
                            created_at=datetime.utcnow() - timedelta(days=2)))
        db.commit()
    finally:
        db.close()

    # Two distinct pages so the SECOND scan (post-recovery) produces a non-empty diff.
    pages = [
        PageBundle(url="https://recover.test/", html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                   llms_txt_present=True, sitemap_present=True),
        PageBundle(url="https://recover.test/",
                   html=GOOD_HTML.replace("</body>", "<p>several extra distinct words added inside the body</p></body>"),
                   robots_txt=GOOD_ROBOTS, llms_txt_present=True, sitemap_present=True),
    ]
    seq = {"n": 0}

    async def _fake_fetch(url, *, transport=None, retries=None):
        p = pages[min(seq["n"], len(pages) - 1)]
        seq["n"] += 1
        return p
    monkeypatch.setattr(rs, "fetch", _fake_fetch)

    safe = validate_url("https://recover.test/")   # allow_private_hosts on in tests → no DNS

    db = SessionLocal()
    try:
        with caplog.at_level(logging.WARNING, logger="app.api.scan"):
            p1 = asyncio.run(rs.run_scan(db, safe, ip="scheduler", org_id=org_id, monitor_id=mid))

        # (i) the new v1 snapshot was persisted despite the mismatch
        versions = [s.schema_version for s in db.query(ScanSnapshot)
                    .filter(ScanSnapshot.monitor_id == mid).all()]
        assert 1 in versions
        # a WARNING was logged (not a raised exception)
        assert any("mismatch" in r.message.lower() for r in caplog.records)
        # (ii) change_set empty on the mismatching scan
        assert (db.get(Scan, p1["scan_id"]).result or {}).get("change_set") == []

        # SECOND scan — baseline advanced to v1, so this diffs v1 vs v1' normally.
        p2 = asyncio.run(rs.run_scan(db, safe, ip="scheduler", org_id=org_id, monitor_id=mid))
        assert (db.get(Scan, p2["scan_id"]).result or {}).get("change_set")   # non-empty => recovered
    finally:
        db.close()


def test_previous_snapshot_never_crosses_orgs():
    db = SessionLocal()
    try:
        ma = _mk_monitor(db, "org-iso-a")
        mb = _mk_monitor(db, "org-iso-b")
        _mk_snapshot(db, ma, org_id="org-iso-a", age_days=1, scan_id="iso-a")
        _mk_snapshot(db, mb, org_id="org-iso-b", age_days=1, scan_id="iso-b")
        prev_a = previous_snapshot(db, ma.id)
        assert prev_a is not None
        assert prev_a.organization_id == "org-iso-a" and prev_a.monitor_id == ma.id
        # A monitor with no snapshots (and None) never borrows another org's baseline.
        assert previous_snapshot(db, "nonexistent-monitor") is None
        assert previous_snapshot(db, None) is None
    finally:
        db.close()
