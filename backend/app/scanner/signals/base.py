"""Shared types + utilities for the signal services.

The HTML is parsed ONCE into a SignalContext and passed to every signal, so no
module re-parses or duplicates extraction logic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property

from bs4 import BeautifulSoup

from app.scanner.models import PageBundle

# Bumped when signal logic or weights change (recorded on every scan).
SCANNER_VERSION = "3.0.0"

# score -> status thresholds (shared with the frontend band() colors)
GOOD = 75
WARN = 45


def clamp(score: float) -> int:
    return int(max(0, min(100, round(score))))


def status_from_score(score: float) -> str:
    """pass | warn | fail — matches the frontend status vocabulary."""
    if score >= GOOD:
        return "pass"
    if score >= WARN:
        return "warn"
    return "fail"


@dataclass
class SignalResult:
    """The uniform shape every signal returns."""
    id: str
    label: str
    score: int                                   # 0-100
    status: str                                  # pass | warn | fail
    weight: float                                # share of the overall score
    issues: list = field(default_factory=list)           # list[str]
    recommendations: list = field(default_factory=list)  # list[str]
    evidence: dict = field(default_factory=dict)         # raw findings

    @classmethod
    def build(cls, id, label, weight, score, *, issues=None, recommendations=None,
              evidence=None) -> "SignalResult":
        score = clamp(score)
        return cls(id=id, label=label, score=score, status=status_from_score(score),
                   weight=weight, issues=issues or [], recommendations=recommendations or [],
                   evidence=evidence or {})


class SignalContext:
    """Parsed, reusable view of one fetched page."""

    def __init__(self, page: PageBundle):
        self.page = page
        self.url = page.url
        self.html = page.html or ""
        self.robots_txt = page.robots_txt or ""
        self.sitemap_xml = page.sitemap_xml or ""
        self.llms_txt_present = page.llms_txt_present
        self.sitemap_present = page.sitemap_present
        self.status_code = page.status_code
        # Header lookups are case-insensitive.
        self.headers = {str(k).lower(): v for k, v in (page.headers or {}).items()}

    @cached_property
    def soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.html, "lxml")

    @cached_property
    def text(self) -> str:
        s = BeautifulSoup(self.html, "lxml")
        for t in s(["script", "style", "noscript"]):
            t.extract()
        return s.get_text(" ", strip=True)

    @cached_property
    def jsonld(self) -> list:
        blocks = []
        for tag in self.soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = tag.string or tag.get_text()
            try:
                blocks.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                blocks.append({"__parse_error__": True})
        return blocks

    @cached_property
    def jsonld_types(self) -> set:
        types: set = set()

        def walk(obj):
            if isinstance(obj, dict):
                t = obj.get("@type")
                if isinstance(t, str):
                    types.add(t)
                elif isinstance(t, list):
                    types.update(x for x in t if isinstance(x, str))
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)

        for b in self.jsonld:
            walk(b)
        return types

    def header(self, name: str) -> str:
        return str(self.headers.get(name.lower(), "") or "")
