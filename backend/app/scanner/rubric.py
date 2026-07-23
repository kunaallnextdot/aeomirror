"""Versioned scoring rubric.

These weights encode what "AI ready" means. They belong to whoever owns AEO
strategy, not to engineering. The active rubric is loaded from the
rubric_versions table (see rubric_provider.py) so weights can change without a
code deploy, and the version is recorded on every stored scan for score
comparability over time. The constants below are the seed values and the
fallback used when the database has no active rubric.
"""
from __future__ import annotations

from dataclasses import dataclass, field

RUBRIC_VERSION = "2026.07.1"

FAMILY_WEIGHTS = {
    "crawler_access": 25,
    "render_parity": 20,
    "schema": 20,
    "structure": 15,
    "extractability": 12,
    "freshness": 8,
}

# Per-check share of its family weight. Keys must match check ids in families.py
CHECK_WEIGHTS = {
    # crawler_access (25)
    "gptbot_allowed": 7, "claudebot_allowed": 7, "perplexitybot_allowed": 5,
    "google_extended_ok": 3, "no_blanket_disallow": 3,
    # render_parity (20)
    "has_real_text": 10, "content_in_raw_html": 7, "not_js_shell": 3,
    # schema (20)
    "has_jsonld": 6, "has_org_schema": 6, "has_type_schema": 5, "schema_parses": 3,
    # structure (15)
    "has_title": 3, "single_h1": 3, "has_meta_description": 3,
    "has_canonical": 2, "has_sitemap_ref": 2, "has_llms_txt": 2,
    # extractability (12)
    "semantic_html": 4, "has_faq_or_qa": 4, "scannable_structure": 4,
    # freshness (8)
    "has_date_modified": 4, "has_visible_date": 4,
}

STATUS_MULTIPLIER = {"pass": 1.0, "warn": 0.5, "fail": 0.0}


@dataclass(frozen=True)
class Rubric:
    """A resolved scoring rubric passed to the engine. Sourced from the active
    row in rubric_versions, or from the constants above as a fallback."""
    version: str
    family_weights: dict = field(default_factory=dict)
    check_weights: dict = field(default_factory=dict)
    status_multiplier: dict = field(default_factory=lambda: dict(STATUS_MULTIPLIER))


def default_rubric() -> Rubric:
    """The code-defined rubric (seed + fallback). Identical to the seeded DB row,
    so scores are the same whether sourced from code or the database."""
    return Rubric(
        version=RUBRIC_VERSION,
        family_weights=dict(FAMILY_WEIGHTS),
        check_weights=dict(CHECK_WEIGHTS),
        status_multiplier=dict(STATUS_MULTIPLIER),
    )
