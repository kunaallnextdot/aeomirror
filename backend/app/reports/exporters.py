"""JSON + CSV exporters for a built report (Phase 6).

Both return bytes so the API can stream them as file downloads. The PDF exporter
lives in app/reports/pdf.py (heavier import kept separate).
"""
from __future__ import annotations

import csv
import io
import json


def to_json_bytes(report: dict) -> bytes:
    return json.dumps(report, indent=2, default=str).encode("utf-8")


# Flat recommendation columns — one row per recommendation, tool-friendly.
CSV_COLUMNS = [
    "priority", "severity", "category", "issue_title", "score", "difficulty",
    "estimated_fix_time", "description", "business_impact", "ai_visibility_impact",
    "recommended_fix", "expected_outcome",
]


def to_csv_bytes(report: dict) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for r in report.get("recommendations", []):
        fix = r.get("fix_template", {})
        writer.writerow([
            r.get("priority", ""),
            r.get("severity", ""),
            r.get("category", ""),
            r.get("issue_title", ""),
            r.get("score", ""),
            r.get("difficulty", ""),
            r.get("estimated_fix_time", ""),
            r.get("description", ""),
            r.get("business_impact", ""),
            r.get("ai_visibility_impact", ""),
            " | ".join(fix.get("recommended_fix", []) or []),
            fix.get("expected_outcome", ""),
        ])
    # Prepend a UTF-8 BOM so Excel opens accented text correctly.
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
