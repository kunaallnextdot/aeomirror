"""Content Cannibalization & Duplicate Content Intelligence.

Pure, deterministic, read-side derivation over the SAME crawl evidence Technical SEO
and Crawl Graph already use, plus each page's own content fingerprint the `content`
signal now extracts (scanner/signals/content.py's word-trigram shingles — no second
HTML parse, no score change) and the raw title/H1/meta-description text `metadata`/
`content` already parse. No network calls, no new database table, no LLM, no
embeddings.

VERY IMPORTANT terminology discipline (per the task): without real Google Search
Console query/page data, this module can NEVER claim confirmed search-result
cannibalization — only technical, evidence-grounded signals. It always says
"potential cannibalization" / "near-duplicate content" / "content overlap", never
"these pages are competing in Google". See `_CLUSTER_LABEL` below for the exact wording
used everywhere a cluster type is surfaced.

Three distinct concepts, never conflated (the task's own core principle):
  - DUPLICATE CONTENT   — substantially identical/near-identical text.
  - CONTENT OVERLAP     — real but more modest shared content; not necessarily a problem.
  - POTENTIAL CANNIBALIZATION — apparent overlapping topic/intent, inferred from
    MULTIPLE independent signals together (never from content similarity alone).
"""
from __future__ import annotations

from app.scanner.bulk import normalize_dedup_key
from app.scanner.signals.content import normalize_content_text

# ------------------------------- thresholds (documented; not a Google claim) -------------------------------
# Word-trigram Jaccard over each page's own main-content fingerprint
# (scanner/signals/content.py's `content_shingles`). Trigram Jaccard is intentionally
# strict — a handful of word substitutions removes several shared trigrams — so these
# were picked empirically against representative near-duplicate/distinct/templated
# example pairs (see tests/test_content_intelligence.py's threshold-validation tests),
# NOT derived from or claimed to match any Google algorithm.
CONTENT_NEAR_DUPLICATE = 0.60   # near-verbatim copy (minor edits at most)
CONTENT_HIGH = 0.35             # substantial real overlap
CONTENT_MODERATE = 0.15         # some real, worth-surfacing overlap

# Word-level (unigram) Jaccard for SHORT strings (title/H1) — trigram shingling is too
# sparse for a 5-10 word string, so title/H1 similarity uses a simpler, separate
# word-set comparison.
TITLE_SIMILAR = 0.50

# A page needs at least this many usable words for its content fingerprint to be
# meaningful to compare at all — NOT a "thin content" SEO judgment (see THIN_CONTENT_*
# below for that, separate concept); just "is there enough text to fingerprint".
MIN_WORDS_FOR_COMPARISON = 20

# Thin-content candidates: a RELATIVE, site-based rule only (never a universal "under
# 300 words" claim) — a page is flagged only when the crawl has enough eligible pages
# to establish a meaningful distribution, and its own word count is well below the
# crawl's own median.
MIN_ELIGIBLE_FOR_THIN_CONTENT = 5
THIN_CONTENT_RATIO = 0.25


def _safe_key(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return normalize_dedup_key(url)
    except Exception:   # noqa: BLE001 — a malformed URL must never break the report
        return None


def _jaccard(a, b) -> float:
    a, b = set(a or ()), set(b or ())
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _word_set(text: str | None) -> set:
    return set(normalize_content_text(text or "").split())


UNAVAILABLE_SCAN_INCOMPLETE = {"available": False, "reason": "scan_incomplete"}
UNAVAILABLE_SINGLE_PAGE = {"available": False, "reason": "multi_page_crawl_required"}


# ------------------------------- eligibility -------------------------------
def _eligible_pages(bulk_pages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Splits bulk_pages into (eligible, excluded). Eligible = a genuinely successful
    2xx HTML page with enough extracted content to meaningfully fingerprint. Excluded
    pages are never compared and never called duplicate/unique — see the task's
    explicit `insufficient_content_evidence` requirement."""
    eligible, excluded = [], []
    for p in bulk_pages:
        url = p.get("url")
        if not url:
            continue
        status = p.get("status_code")
        if p.get("error") or not isinstance(status, int) or not (200 <= status < 300):
            excluded.append({"url": url, "reason": "insufficient_content_evidence"})
            continue
        word_count = p.get("word_count") or 0
        shingles = p.get("content_shingles") or []
        if word_count < MIN_WORDS_FOR_COMPARISON:
            excluded.append({"url": url, "reason": "insufficient_content_evidence"})
            continue
        eligible.append({
            "url": url, "title": p.get("title") or None, "h1": p.get("h1") or None,
            "description": p.get("description") or None, "canonical": p.get("canonical") or None,
            "word_count": word_count, "shingles": set(shingles),
        })
    eligible.sort(key=lambda r: r["url"])
    return eligible, excluded


# ------------------------------- duplicate page elements -------------------------------
def _duplicate_elements(eligible: list[dict]) -> list[dict]:
    """Exact-match grouping (not similarity) on the normalized title/H1/meta
    description — a concrete, unambiguous finding distinct from content similarity."""
    out = []
    for field, code in (("title", "duplicate_title"), ("h1", "duplicate_h1"),
                        ("description", "duplicate_meta_description")):
        groups: dict[str, list[str]] = {}
        for p in eligible:
            raw = p.get(field)
            if not raw:
                continue
            key = normalize_content_text(raw)
            if not key:
                continue
            groups.setdefault(key, []).append(p["url"])
        for key, urls in sorted(groups.items()):
            if len(urls) >= 2:
                out.append({"type": code, "value": key[:150], "urls": sorted(urls)})
    return out


# ------------------------------- pairwise classification -------------------------------
def _canonical_type(url: str, canonical: str | None) -> str:
    if not canonical:
        return "missing"
    if _safe_key(canonical) == _safe_key(url):
        return "self"
    return "other"


def _pair_signals(a: dict, b: dict) -> dict:
    content_sim = _jaccard(a["shingles"], b["shingles"])
    title_sim = _jaccard(_word_set(a["title"]), _word_set(b["title"]))
    h1_sim = _jaccard(_word_set(a["h1"]), _word_set(b["h1"]))
    both_self_canonical = (_canonical_type(a["url"], a["canonical"]) == "self"
                          and _canonical_type(b["url"], b["canonical"]) == "self")
    return {"content_sim": content_sim, "title_sim": title_sim, "h1_sim": h1_sim,
           "both_self_canonical": both_self_canonical}


def _pair_type(sig: dict) -> str | None:
    """Deterministic classification (documented, tested against representative
    examples — see module docstring). `None` means the pair simply isn't linked.

    Signal-counting rule (the task's explicit "potential cannibalization requires
    MULTIPLE supporting signals, never one similarity metric alone"): count how many
    of {real content overlap, similar title, similar H1, both pages self-canonicalize}
    hold. 2+ signals -> potential_cannibalization (this is what correctly catches the
    "different wording, same intent" case — title+H1 both matching is 2 signals even
    with near-zero content similarity). Exactly 1 signal, and it's real content
    overlap, -> content_overlap (a single non-content signal alone, e.g. only a
    similar title with unrelated body copy, is too weak to report at all)."""
    content_sim, title_sim, h1_sim = sig["content_sim"], sig["title_sim"], sig["h1_sim"]
    if content_sim >= CONTENT_NEAR_DUPLICATE:
        return "near_duplicate"
    has_content_overlap = content_sim >= CONTENT_MODERATE
    signal_count = sum([has_content_overlap, title_sim >= TITLE_SIMILAR,
                        h1_sim >= TITLE_SIMILAR, sig["both_self_canonical"]])
    if signal_count >= 2:
        return "potential_cannibalization"
    if signal_count == 1 and has_content_overlap:
        return "content_overlap"
    return None


_TYPE_RANK = {"near_duplicate": 0, "potential_cannibalization": 1, "content_overlap": 2}


# ------------------------------- clustering (union-find) -------------------------------
class _UnionFind:
    def __init__(self, keys):
        self.parent = {k: k for k in keys}

    def find(self, k):
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _canonical_situation(urls: list[str], canonical_by_url: dict[str, str | None]) -> str:
    """self_canonical_both | consolidated | inconsistent | mixed — see
    `_recommended_action`'s docstring for how each maps to an action."""
    keys = {_safe_key(u) for u in urls}
    targets, all_self, consolidated = set(), True, False
    for u in urls:
        c = canonical_by_url.get(u)
        ctype = _canonical_type(u, c)
        if ctype == "missing":
            all_self = False
            continue
        ck = _safe_key(c)
        if ctype == "self":
            targets.add(ck)
        else:
            all_self = False
            targets.add(ck)
            if ck in keys:
                consolidated = True
    if consolidated:
        return "consolidated"
    if all_self:
        return "self_canonical_both"
    if len(targets) > 1:
        return "inconsistent"
    return "mixed"


def _recommended_action(cluster_type: str, situation: str, titles_similar: bool) -> tuple[str, str]:
    """(action_code, human_sentence). Deterministic, evidence-based — never an
    automatic redirect/merge/canonical instruction, only a review recommendation."""
    if situation == "inconsistent":
        return ("REVIEW_CANONICAL",
                "Review canonical configuration — these pages have high similarity but "
                "canonicalize to different targets.")
    if situation == "consolidated":
        return ("KEEP_SEPARATE",
                "A canonical relationship already declares intent here — no further "
                "action needed unless that canonical target is wrong.")
    if cluster_type == "near_duplicate":
        if titles_similar:
            return ("CONSOLIDATE",
                    "Consider consolidating these near-duplicate pages if they serve the "
                    "same purpose, or differentiate them meaningfully if they don't.")
        return ("DIFFERENTIATE",
                "These pages share a lot of boilerplate/template text but appear to "
                "cover distinct subjects — differentiate the unique content further.")
    if cluster_type == "potential_cannibalization":
        return ("DIFFERENTIATE",
                "Review whether these pages should target distinct search intents; "
                "differentiate titles/H1s and supporting content.")
    return ("KEEP_SEPARATE", "Evidence suggests these pages serve distinct purposes.")


_CLUSTER_LABEL = {
    "near_duplicate": "Near-duplicate content",
    "potential_cannibalization": "Potential cannibalization",
    "content_overlap": "Content overlap",
}


def _build_clusters(eligible: list[dict]) -> list[dict]:
    by_url = {p["url"]: p for p in eligible}
    canonical_by_url = {p["url"]: p["canonical"] for p in eligible}
    uf = _UnionFind(p["url"] for p in eligible)
    pair_evidence: dict[tuple, dict] = {}   # (u1,u2) sorted -> {"type", "sig"}

    for i, a in enumerate(eligible):
        for b in eligible[i + 1:]:
            sig = _pair_signals(a, b)
            ptype = _pair_type(sig)
            if not ptype:
                continue
            uf.union(a["url"], b["url"])
            pair_evidence[tuple(sorted((a["url"], b["url"])))] = {"type": ptype, "sig": sig}

    groups: dict[str, list[str]] = {}
    for p in eligible:
        groups.setdefault(uf.find(p["url"]), []).append(p["url"])

    clusters = []
    for urls in groups.values():
        if len(urls) < 2:
            continue
        urls = sorted(urls)
        internal_pairs = [pair_evidence[pair] for i, u in enumerate(urls)
                          for pair in [tuple(sorted((u, v))) for v in urls[i + 1:]]
                          if pair in pair_evidence]
        if not internal_pairs:
            continue
        cluster_type = min((pe["type"] for pe in internal_pairs), key=lambda t: _TYPE_RANK[t])
        max_content_sim = max(pe["sig"]["content_sim"] for pe in internal_pairs)
        titles_similar = any(pe["sig"]["title_sim"] >= TITLE_SIMILAR for pe in internal_pairs)
        h1_similar = any(pe["sig"]["h1_sim"] >= TITLE_SIMILAR for pe in internal_pairs)
        confidence = ("high" if (cluster_type == "near_duplicate"
                                 or max_content_sim >= CONTENT_HIGH) else "medium")

        situation = _canonical_situation(urls, canonical_by_url)
        action, rec_text = _recommended_action(cluster_type, situation, titles_similar)

        evidence = [f"Content similarity: {round(max_content_sim * 100)}% (word-trigram overlap)"]
        if titles_similar:
            evidence.append("Similar titles")
        if h1_similar:
            evidence.append("Similar H1 headings")
        if situation == "self_canonical_both":
            evidence.append("Pages self-canonicalize independently (stronger overlap signal)")
        elif situation == "consolidated":
            evidence.append("One page already canonicalizes to another in this cluster "
                            "(may be intentional consolidation)")
        elif situation == "inconsistent":
            evidence.append("Pages canonicalize to different targets")

        clusters.append({
            "id": f"cluster:{'-'.join(urls)}",
            "type": cluster_type,
            "label": _CLUSTER_LABEL[cluster_type],
            "severity": _SEVERITY_FOR_TYPE[cluster_type],
            "confidence": confidence,
            "pages": [{"url": by_url[u]["url"], "title": by_url[u]["title"],
                      "h1": by_url[u]["h1"], "word_count": by_url[u]["word_count"],
                      "canonical": by_url[u]["canonical"]} for u in urls],
            "evidence": evidence,
            "canonical_situation": situation,
            "recommended_action": action,
            "recommendation": rec_text,
            "related_opportunity_id": f"content_cluster:{'-'.join(urls)}",
        })

    clusters.sort(key=lambda c: (_TYPE_RANK[c["type"]], -len(c["pages"]), c["id"]))
    return clusters


def _thin_pages(eligible: list[dict]) -> list[dict]:
    if len(eligible) < MIN_ELIGIBLE_FOR_THIN_CONTENT:
        return []
    counts = sorted(p["word_count"] for p in eligible)
    n = len(counts)
    median = counts[n // 2] if n % 2 else (counts[n // 2 - 1] + counts[n // 2]) / 2
    if median <= 0:
        return []
    floor = median * THIN_CONTENT_RATIO
    return sorted(
        [{"url": p["url"], "word_count": p["word_count"], "site_median_word_count": median}
         for p in eligible if p["word_count"] < floor],
        key=lambda r: r["url"])


def build_content_intelligence_block(*, bulk_pages: list[dict] | None,
                                     scan_ready: bool = True) -> dict:
    """The full, ungated Content Cannibalization & Duplicate Content Intelligence
    block embedded additively into `build_report`'s output
    (`report["content_intelligence"]`) — mirrors `reports.crawl_graph`'s and
    `reports.technical_seo`'s architecture. Only ever available for a multi-page
    (bulk) scan — a single page cannot establish a site-wide duplicate/cannibalization
    finding (never compared against itself)."""
    if not scan_ready:
        return dict(UNAVAILABLE_SCAN_INCOMPLETE)
    if not bulk_pages:
        return dict(UNAVAILABLE_SINGLE_PAGE)

    eligible, excluded = _eligible_pages(bulk_pages)
    duplicate_elements = _duplicate_elements(eligible)
    clusters = _build_clusters(eligible)
    thin_pages = _thin_pages(eligible)

    summary = {
        "pages_analyzed": len(eligible),
        "pages_excluded": len(excluded),
        "duplicate_clusters": sum(1 for c in clusters if c["type"] == "near_duplicate"),
        "overlap_clusters": sum(1 for c in clusters if c["type"] == "content_overlap"),
        "potential_cannibalization_clusters": sum(
            1 for c in clusters if c["type"] == "potential_cannibalization"),
        "duplicate_elements": len(duplicate_elements),
        "thin_pages": len(thin_pages),
    }

    return {
        "available": True,
        "summary": summary,
        "clusters": clusters,
        "duplicate_elements": duplicate_elements,
        "thin_pages": thin_pages,
        "excluded_pages": excluded,
    }


_SEVERITY_FOR_TYPE = {"near_duplicate": "High", "potential_cannibalization": "High",
                     "content_overlap": "Medium"}


def gate_content_intelligence(block: dict | None, unlocked: bool, *,
                              free_cluster_limit: int = 2,
                              free_element_limit: int = 2) -> dict | None:
    """Server-side trim to a free preview — same model as `technical_seo`/
    `crawl_graph`'s gate functions. Cluster PAGES are real paid detail (URLs +
    title/H1/canonical), so only the URLs of the free-preview clusters themselves are
    ever sent — never a different, independently-chosen slice, and never the full
    duplicate-element/thin-page lists."""
    if not block or unlocked or block.get("available") is False:
        return block

    clusters = block.get("clusters") or []
    elements = block.get("duplicate_elements") or []
    thin = block.get("thin_pages") or []

    preview_clusters = clusters[:free_cluster_limit]
    preview_elements = [{**e, "urls": e["urls"][:free_cluster_limit]} for e in elements[:free_element_limit]]

    return {
        **block,
        "preview": True,
        "clusters": preview_clusters,
        "locked_cluster_count": max(0, len(clusters) - len(preview_clusters)),
        "duplicate_elements": preview_elements,
        "locked_duplicate_element_count": max(0, len(elements) - len(preview_elements)),
        "thin_pages": [],
        "locked_thin_page_count": len(thin),
        "excluded_pages": [],
    }


__all__ = ["build_content_intelligence_block", "gate_content_intelligence",
          "UNAVAILABLE_SCAN_INCOMPLETE", "UNAVAILABLE_SINGLE_PAGE",
          "CONTENT_NEAR_DUPLICATE", "CONTENT_HIGH", "CONTENT_MODERATE", "TITLE_SIMILAR"]
