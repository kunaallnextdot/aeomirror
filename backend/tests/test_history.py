"""Extended monitor history endpoint (Part A): ordering, `days` date filter, per-entry
change_summary (severity counts / null), backward compatibility, and cross-org isolation."""
from datetime import datetime, timedelta

from app.db.models import Monitor, MonitorHistory, Scan
from app.db.session import SessionLocal
from tests.authutil import auth_client


def _mk_monitor(db, org_id):
    m = Monitor(organization_id=org_id, url="https://h.test/", normalized_url="h.test",
                status="active", frequency="weekly")
    db.add(m); db.commit(); db.refresh(m)
    return m


def _mk_scan(db, org_id, change_set):
    result = {"overall_score": 80}
    if change_set is not None:
        result["change_set"] = change_set
    s = Scan(url="https://h.test/", normalized_url="h.test", ars=50, rubric_version="t",
             result=result, organization_id=org_id)
    db.add(s); db.commit(); db.refresh(s)
    return s


def _mk_history(db, m, scan_id, *, score, age_days, delta=None):
    h = MonitorHistory(
        monitor_id=m.id, organization_id=m.organization_id, scan_id=scan_id,
        overall_score=score, status="pass", issue_count=1, scores={"schema": score},
        changes=({"overall": {"delta": delta}} if delta is not None else {}),
        created_at=datetime.utcnow() - timedelta(days=age_days))
    db.add(h); db.commit(); db.refresh(h)
    return h


def test_history_change_summary_ordering_and_days_filter():
    client, obody = auth_client()
    org = obody["organization"]["id"]
    db = SessionLocal()
    try:
        m = _mk_monitor(db, org)
        s_recent = _mk_scan(db, org, [{"severity": "CRITICAL"}, {"severity": "WARNING"},
                                      {"severity": "WARNING"}])
        s_none = _mk_scan(db, org, None)     # no change_set at all -> summary null
        s_old = _mk_scan(db, org, [])        # empty diff (exists) -> zeroed counts
        _mk_history(db, m, s_old.id, score=70, age_days=10, delta=-5)
        _mk_history(db, m, s_none.id, score=72, age_days=5)
        _mk_history(db, m, s_recent.id, score=80, age_days=1, delta=8)
        mid, rid, nid, oid = m.id, s_recent.id, s_none.id, s_old.id
    finally:
        db.close()

    hist = client.get(f"/history/{mid}").json()["history"]
    # newest first
    assert [h["scan_id"] for h in hist] == [rid, nid, oid]
    by_scan = {h["scan_id"]: h for h in hist}
    assert by_scan[rid]["change_summary"] == {"CRITICAL": 1, "WARNING": 2, "INFO": 0}
    assert by_scan[rid]["delta"] == 8
    assert len(by_scan[rid]["change_set"]) == 3          # raw list carried for the reveal
    assert by_scan[oid]["change_summary"] == {"CRITICAL": 0, "WARNING": 0, "INFO": 0}  # empty exists
    assert by_scan[nid]["change_summary"] is None        # no change set -> null

    # date filter: only the age-1 scan falls inside a 3-day window
    filtered = client.get(f"/history/{mid}?days=3").json()["history"]
    assert [h["scan_id"] for h in filtered] == [rid]


def test_history_backward_compatible_shape():
    client, obody = auth_client()
    org = obody["organization"]["id"]
    db = SessionLocal()
    try:
        m = _mk_monitor(db, org)
        s = _mk_scan(db, org, [{"severity": "INFO"}])
        _mk_history(db, m, s.id, score=90, age_days=1)
        mid = m.id
    finally:
        db.close()

    r = client.get(f"/history/{mid}").json()   # `days` omitted -> original behaviour
    assert "trends" in r and "score_series" in r["trends"]
    h0 = r["history"][0]
    for k in ("id", "scan_id", "overall_score", "status", "issue_count", "scores",
              "changes", "created_at"):
        assert k in h0                          # existing fields unchanged
    for k in ("change_summary", "change_set", "delta"):
        assert k in h0                          # additive fields present


def test_history_cross_org_access_denied():
    owner, obody = auth_client()
    other, _ = auth_client()
    org = obody["organization"]["id"]
    db = SessionLocal()
    try:
        m = _mk_monitor(db, org)
        mid = m.id
    finally:
        db.close()
    assert owner.get(f"/history/{mid}").status_code == 200
    assert other.get(f"/history/{mid}").status_code == 404   # another org can't read it
