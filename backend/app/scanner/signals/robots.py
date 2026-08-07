"""robots.txt signal: exists, crawlable, AI-crawler rules, blocked paths."""
from __future__ import annotations

from app.scanner.signals.base import SignalContext, SignalResult

ID, LABEL, WEIGHT = "robots", "robots.txt", 10

AI_BOTS = {
    "GPTBot": ["GPTBot"],
    "ClaudeBot": ["ClaudeBot", "Claude-User", "anthropic-ai"],
    "PerplexityBot": ["PerplexityBot"],
    "Google-Extended": ["Google-Extended"],
}


def _parse(robots_txt: str) -> dict:
    """Return {user-agent: [disallow paths]}."""
    groups: dict = {}
    current: list = []
    for line in robots_txt.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "user-agent":
            current = [val]
            groups.setdefault(val, [])
        elif key == "disallow" and current:
            for a in current:
                groups.setdefault(a, []).append(val)
    return groups


def _blocked(groups: dict, agents: list) -> bool | None:
    for a in agents:
        if a in groups:
            return "/" in groups[a]
    if "*" in groups:
        return "/" in groups["*"]
    return None


def analyze(ctx: SignalContext) -> SignalResult:
    robots = ctx.robots_txt
    issues: list = []
    recs: list = []

    if not robots.strip():
        return SignalResult.build(
            ID, LABEL, WEIGHT, 60,
            issues=["No robots.txt found — crawler access is allowed by default but not declared."],
            recommendations=["Add a robots.txt that explicitly allows the AI crawlers (GPTBot, ClaudeBot, PerplexityBot, Google-Extended)."],
            evidence={"exists": False},
        )

    groups = _parse(robots)
    blanket = _blocked(groups, ["*"]) is True

    score = 100.0
    ai_status = {}
    for name, agents in AI_BOTS.items():
        blocked = _blocked(groups, agents)
        ai_status[name] = "blocked" if blocked else ("undeclared" if blocked is None else "allowed")
        if blocked:
            score -= 18
            issues.append(f"{name} is disallowed in robots.txt — this AI crawler cannot read the site.")
            recs.append(f"Remove the Disallow rule affecting {name}.")

    if blanket:
        score -= 45
        issues.append("robots.txt blocks all crawlers with 'Disallow: /'.")
        recs.append("Remove or scope the site-wide 'Disallow: /' under 'User-agent: *'.")

    disallow_paths = sorted({p for paths in groups.values() for p in paths if p and p != "/"})

    return SignalResult.build(
        ID, LABEL, WEIGHT, score, issues=issues, recommendations=recs,
        evidence={
            "exists": True,
            "crawlable": not blanket,
            "ai_crawlers": ai_status,
            "blocked_paths": disallow_paths[:25],
            "sitemap_referenced": "sitemap" in robots.lower(),
        },
    )
