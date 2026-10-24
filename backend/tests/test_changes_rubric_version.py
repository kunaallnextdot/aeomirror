"""Monitoring change detection must not mistake a rubric-formula change for a real
site change (app/monitoring/changes.py, app/monitoring/alerts.py). Pure-function
tests against detect_changes()/evaluate_alerts() directly — no DB, no HTTP."""
from app.monitoring.alerts import evaluate_alerts
from app.monitoring.changes import detect_changes

_PREV = {
    "overall_score": 80, "rubric_version": "2026.07.1",
    "sections": [
        {"id": "schema", "label": "Schema", "score": 80, "status": "pass", "issues": [], "evidence": {}},
        {"id": "accessibility", "label": "Accessibility", "score": 80, "status": "pass", "issues": [], "evidence": {}},
    ],
}


def _curr(score, rubric_version="2026.07.1"):
    return {
        "overall_score": score, "rubric_version": rubric_version,
        "sections": [
            {"id": "schema", "label": "Schema", "score": score, "status": "pass" if score >= 75 else "fail",
             "issues": [], "evidence": {}},
            {"id": "accessibility", "label": "Accessibility", "score": score, "status": "pass" if score >= 75 else "fail",
             "issues": [], "evidence": {}},
        ],
    }


def test_same_rubric_version_real_drop_is_flagged():
    changes = detect_changes(_PREV, _curr(50))
    assert changes["rubric_version_changed"] is False
    assert changes["overall"]["delta"] == -30
    assert len(changes["regressions"]) == 2


def test_rubric_version_change_suppresses_fabricated_regressions():
    changes = detect_changes(_PREV, _curr(50, rubric_version="2026.11.1"))
    assert changes["rubric_version_changed"] is True
    # The site didn't necessarily change at all — a formula-driven score drop must
    # never be reported as a real regression.
    assert changes["regressions"] == []
    assert changes["improvements"] == []
    assert changes["overall"]["delta"] is None
    for cat in changes["categories"]:
        assert cat["delta"] is None
        # The raw before/after scores stay visible for transparency.
        assert cat["prev"] == 80


def test_rubric_version_change_never_fires_a_score_drop_alert(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "alert_score_drop_warning", 8)
    changes = detect_changes(_PREV, _curr(40, rubric_version="2026.11.1"))
    alerts = evaluate_alerts(changes, _PREV, _curr(40, rubric_version="2026.11.1"))
    assert all(a["type"] != "score_drop" for a in alerts)
    assert all(a["type"] not in ("accessibility_regression", "performance_degradation") for a in alerts)


def test_same_rubric_version_still_fires_a_score_drop_alert(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "alert_score_drop_warning", 8)
    monkeypatch.setattr(settings, "alert_score_drop_critical", 20)
    changes = detect_changes(_PREV, _curr(40))
    alerts = evaluate_alerts(changes, _PREV, _curr(40))
    assert any(a["type"] == "score_drop" for a in alerts)


def test_critical_issue_alert_still_fires_regardless_of_rubric_change(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "alert_critical_score", 25)
    low = _curr(10, rubric_version="2026.11.1")
    changes = detect_changes(_PREV, low)
    alerts = evaluate_alerts(changes, _PREV, low)
    # Absolute-threshold alerts are score-vs-constant, not a delta, so they remain
    # valid evidence even across a rubric-version change.
    assert any(a["type"] == "critical_issue" for a in alerts)


def test_first_scan_has_no_rubric_change_flag():
    changes = detect_changes(None, _curr(80))
    assert changes["rubric_version_changed"] is False
