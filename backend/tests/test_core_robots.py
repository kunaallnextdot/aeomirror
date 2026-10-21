"""Shared robots.txt matching (app/core/robots.py) — a deliberate public port of the
algorithm already proven in app.services.crawler_access, used by the URL
Intelligence crawler. Verifies the port behaves identically on the cases that
matter: group fallback, wildcard/end-anchor matching, and the empty-robots default."""
from app.core.robots import is_allowed, parse_groups, path_blocked

_ROBOTS = """
User-agent: AEOMirrorBot
Disallow: /private/
Allow: /private/public-page$

User-agent: *
Disallow: /admin
"""


def test_empty_robots_allows_everything():
    assert is_allowed("", "AEOMirrorBot", "/anything") is True


def test_bot_specific_group_used_over_wildcard():
    assert is_allowed(_ROBOTS, "AEOMirrorBot", "/private/secret") is False
    assert is_allowed(_ROBOTS, "AEOMirrorBot", "/admin") is True   # AEOMirrorBot has its own group


def test_unknown_bot_falls_back_to_wildcard_group():
    assert is_allowed(_ROBOTS, "SomeOtherBot", "/admin") is False
    assert is_allowed(_ROBOTS, "SomeOtherBot", "/private/secret") is True   # not in its group


def test_end_anchor_allow_overrides_longer_disallow_prefix():
    assert is_allowed(_ROBOTS, "AEOMirrorBot", "/private/public-page") is True


def test_wildcard_pattern_matches_prefix():
    groups = parse_groups("User-agent: *\nDisallow: /secret*/x\n")
    assert path_blocked(groups["*"], "/secret-area/x") is True
    assert path_blocked(groups["*"], "/other/x") is False
