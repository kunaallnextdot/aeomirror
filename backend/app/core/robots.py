"""Shared robots.txt group-matching primitives.

This is a deliberate, intentional PORT of the matching algorithm already proven in
`app.services.crawler_access` (`_parse_groups`/`_pattern_matches`/`_path_blocked`),
made public so the URL Intelligence crawler (`app.services.url_intelligence.discovery`)
can reuse the exact same matching rules without importing another module's
underscore-prefixed internals. `crawler_access.py` and `app.scanner.signals.robots`
are left untouched — both are already tested and used in the live scan path, and
re-pointing them at this module is an unrelated, riskier refactor that isn't needed
for this feature. Keep this file's logic identical to crawler_access.py's if either
ever changes.
"""
from __future__ import annotations

import re

RobotsGroups = dict[str, list[tuple[str, str]]]


def parse_groups(text: str) -> RobotsGroups:
    """Parse robots.txt into {user-agent(lower): [(allow|disallow, value), ...]}.
    Consecutive User-agent lines share the rule block that follows them."""
    groups: RobotsGroups = {}
    current: list[str] = []
    started_rules = False
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if started_rules:
                current = []
                started_rules = False
            current.append(value.lower())
            groups.setdefault(value.lower(), [])
        elif field in ("allow", "disallow") and current:
            started_rules = True
            for agent in current:
                groups[agent].append((field, value))
    return groups


def _pattern_matches(pattern: str, path: str) -> bool:
    """robots.txt path match: prefix match, with `*` wildcard and `$` end-anchor."""
    rx = re.escape(pattern).replace(r"\*", ".*")
    if rx.endswith(r"\$"):
        rx = rx[:-2] + "$"
    return re.match(rx, path) is not None


def path_blocked(rules: list[tuple[str, str]], path: str) -> bool:
    """Longest-match wins; on a tie, Allow beats Disallow (Google's rule). An empty
    Disallow value means 'allow everything' and is a no-op."""
    best_len, best_is_allow = -1, True
    for typ, value in rules:
        if value == "":
            continue
        if _pattern_matches(value, path):
            length = len(value)
            if length > best_len or (length == best_len and typ == "allow"):
                best_len, best_is_allow = length, (typ == "allow")
    if best_len < 0:
        return False
    return not best_is_allow


def is_allowed(robots_txt: str, user_agent_token: str, path: str) -> bool:
    """Whether `user_agent_token` (e.g. "AEOMirrorBot") may fetch `path`, falling back
    to the `*` group when the bot has no own group. Empty/missing robots.txt allows
    everything (the standard default)."""
    if not robots_txt or not robots_txt.strip():
        return True
    groups = parse_groups(robots_txt)
    rules = groups.get(user_agent_token.lower())
    if rules is None:
        rules = groups.get("*", [])
    return not path_blocked(rules, path)
