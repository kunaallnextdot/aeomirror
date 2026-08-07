"""Run every signal over a page and build the weighted AI-visibility report.

Resilient: a signal that raises degrades to a single failed section rather than
failing the whole scan. Weights sum to 100, so overall_score is a clean 0-100.
"""
from __future__ import annotations

from dataclasses import asdict

from app.scanner.models import PageBundle
from app.scanner.signals import (
    accessibility, ai_readiness, content, freshness, links, metadata,
    performance, robots, schema, sitemap,
)
from app.scanner.signals.base import (
    SCANNER_VERSION, SignalContext, SignalResult, clamp, status_from_score,
)

# Order defines display order; each module carries its own WEIGHT (sum = 100).
SIGNALS = [robots, sitemap, metadata, schema, content, links, performance,
           accessibility, freshness, ai_readiness]


def _safe(module, ctx: SignalContext) -> SignalResult:
    try:
        return module.analyze(ctx)
    except Exception as e:  # never let one signal break the report
        return SignalResult.build(
            module.ID, module.LABEL, module.WEIGHT, 0,
            issues=[f"Signal could not be evaluated ({type(e).__name__})."],
            recommendations=[], evidence={"error": type(e).__name__},
        )


def run_signals(page: PageBundle) -> dict:
    ctx = SignalContext(page)
    results = [_safe(m, ctx) for m in SIGNALS]

    total_weight = sum(r.weight for r in results) or 1
    overall = clamp(sum(r.score * r.weight for r in results) / total_weight)

    return {
        "scanner_version": SCANNER_VERSION,
        "overall_score": overall,
        "overall_status": status_from_score(overall),
        "sections": [asdict(r) for r in results],
    }
