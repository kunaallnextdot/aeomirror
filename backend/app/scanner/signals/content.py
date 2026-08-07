"""Content-structure signal: H1 count, H2 hierarchy, semantic HTML, heading problems."""
from __future__ import annotations

from app.scanner.signals.base import SignalContext, SignalResult

ID, LABEL, WEIGHT = "content", "Content Structure", 12


def analyze(ctx: SignalContext) -> SignalResult:
    soup = ctx.soup
    issues: list = []
    recs: list = []
    score = 0.0

    h1s = soup.find_all("h1")
    h2s = soup.find_all("h2")
    if len(h1s) == 1:
        score += 25
    elif len(h1s) == 0:
        issues.append("No H1 heading — the page has no clear primary topic.")
        recs.append("Add exactly one descriptive H1.")
    else:
        score += 10
        issues.append(f"{len(h1s)} H1 headings found — there should be exactly one.")
        recs.append("Use a single H1 and demote the rest to H2/H3.")

    if h2s:
        score += 20
    else:
        issues.append("No H2 subheadings — content lacks a scannable hierarchy.")
        recs.append("Break the content into sections with H2/H3 headings.")

    # heading order sanity: an H3 appearing before any H2 is a hierarchy jump
    heading_seq = [int(t.name[1]) for t in soup.find_all(["h1", "h2", "h3", "h4"])]
    jumps = sum(1 for a, b in zip(heading_seq, heading_seq[1:]) if b - a >= 2)
    if jumps == 0:
        score += 15
    else:
        issues.append(f"{jumps} heading-level jump(s) (e.g. H2 straight to H4).")
        recs.append("Keep heading levels sequential (H1 -> H2 -> H3).")

    semantic = bool(soup.find(["main", "article", "section", "header", "footer", "nav"]))
    if semantic:
        score += 20
    else:
        issues.append("No semantic HTML5 elements (main/article/section).")
        recs.append("Wrap content in <main> and <article> for cleaner extraction.")

    paras = soup.find_all("p")
    lists = soup.find_all(["ul", "ol"])
    if len(paras) >= 3 or lists:
        score += 20
    else:
        issues.append("Content is not broken into paragraphs or lists.")
        recs.append("Use paragraphs and lists to make content scannable.")

    return SignalResult.build(
        ID, LABEL, WEIGHT, score, issues=issues, recommendations=recs,
        evidence={
            "h1_count": len(h1s), "h2_count": len(h2s),
            "heading_jumps": jumps, "semantic_html": semantic,
            "paragraphs": len(paras), "lists": len(lists),
        },
    )
