"""Explainable business-value prioritization (Phase 7).

Computed fresh at read time from a UrlFinding's own already-validated fields — never
persisted, never re-interpreted by a second AI call. Deliberately separates TWO
concepts the ticket asks to keep distinct:

- `priority` on the finding itself is the AI/deterministic engine's TECHNICAL
  severity judgement (critical/high/medium/low), already bounded to a fixed
  vocabulary by `ai_analysis._validate_ai_output`.
- the business-value SCORE computed here blends that technical severity with page
  business-intent (from `classification.INTENT_WEIGHT`), confidence, and how many
  pages share the affected template — so a privacy-policy page never outranks a
  pricing page by business value alone, UNLESS the finding is itself flagged
  `critical` (a security/legal/accessibility issue is never down-weighted by
  business intent — see the `critical` carve-out below).

Every score comes with a plain-English `reason` string — there is no opaque number
shown anywhere without an explanation of how it was built.
"""
from __future__ import annotations

import math

from app.db.models import UrlFinding
from app.services.url_intelligence.classification import intent_weight

_SEVERITY_SCORE = {"critical": 4.0, "high": 3.0, "medium": 2.0, "low": 1.0}
_CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.4, "insufficient_evidence": 0.15}


def priority_score(finding: UrlFinding) -> dict:
    severity = _SEVERITY_SCORE.get(finding.priority, 1.0)
    confidence = _CONFIDENCE_WEIGHT.get(finding.confidence, 0.15)
    # Critical findings (e.g. a security/legal/accessibility issue the AI engine itself
    # flagged critical) are never down-weighted by business intent — a critical issue
    # on a legal page still needs attention. Everything else IS weighted by business
    # intent so a low-traffic utility page doesn't outrank a pricing page by default.
    business = 1.0 if finding.priority == "critical" else intent_weight(finding.page_type or "other")
    affected = min(2.0, 1.0 + math.log2(max(1, finding.affected_url_count or 1)) * 0.25)

    score = round(severity * confidence * business * affected, 3)

    reason_parts = [f"{finding.priority} technical severity (x{severity:g})"]
    if finding.confidence != "high":
        reason_parts.append(f"{finding.confidence} confidence (x{confidence:g})")
    if business < 1.0:
        reason_parts.append(f"lower business intent for a {finding.page_type or 'other'} page (x{business:g})")
    if (finding.affected_url_count or 1) > 1:
        reason_parts.append(f"affects {finding.affected_url_count} pages sharing this template (x{affected:g})")

    return {"score": score, "reason": "; ".join(reason_parts)}
