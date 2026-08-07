"""Phase 6: actionable AI-visibility reports.

Turns a stored scan (its 10 signal sections) into a rich, rule-based report:
a recommendation engine (what's wrong, why it matters, how to fix it, expected
impact, priority), a website scorecard, and JSON / CSV / PDF exporters. No LLM or
paid API is used anywhere — all copy comes from structured templates.
"""
from app.reports.engine import REPORT_VERSION, build_report

__all__ = ["REPORT_VERSION", "build_report"]
