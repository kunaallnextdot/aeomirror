"""Scanner data structures. Checks are pure functions of a PageBundle."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PageBundle:
    url: str
    html: str
    robots_txt: str = ""
    llms_txt_present: bool = False
    sitemap_present: bool = False
    sitemap_xml: str = ""          # captured sitemap.xml body (Phase 3 signals)
    status_code: int = 200
    headers: dict = field(default_factory=dict)


@dataclass
class CheckResult:
    id: str
    label: str
    status: str          # pass | warn | fail
    detail: str
    fix_hint: str = ""
    weight: float = 0.0
    earned: float = 0.0


@dataclass
class FamilyResult:
    id: str
    label: str
    weight: float
    earned: float
    checks: list = field(default_factory=list)


@dataclass
class ScanReport:
    url: str
    domain: str
    ars: int
    rubric_version: str
    families: list = field(default_factory=list)
    top_issues: list = field(default_factory=list)
    crawlers: list = field(default_factory=list)
