"""Real Crawl Graph + True Orphan Detection.

Pure, deterministic, read-side derivation over the SAME crawl evidence Technical SEO
and Phase 4 already use — the per-page internal `link_targets` the `links` signal now
extracts (scanner/signals/links.py) from its own already-parsed HTML (no second HTML
parse, no new scanner logic, no score change) plus the per-page HTTP facts already
persisted by routes_scan. No network calls of its own, no new database table.

ARCHITECTURE NOTE (read before changing anything here): AEOMirror's "bulk scan" is NOT
a spidering crawler that discovers pages by following links from a seed — it scans a
flat, user-supplied list of URLs (see scanner/bulk.py's own docstring). There is no
crawl-time "depth from seed" and no crawl-discovery order. This module treats the
FIRST URL in that user-submitted list (`bulk_requested_urls[0]`, NOT `bulk_pages`'s
storage order, which reflects concurrent-fetch COMPLETION order and is therefore not
even deterministic run-to-run) as the crawl "seed" — the one deterministic,
user-intended notion of a primary/home URL this architecture actually has. This is
documented explicitly because it's the single biggest interpretive judgment call this
module makes; see build_crawl_graph_block's docstring.

Three distinct concepts, never conflated (per the task's own core principle):
  - a GRAPH EDGE (A links to B),
  - REACHABILITY (B is reachable from the seed by following internal links), and
  - ORPHAN status (B has zero inbound internal links from OTHER crawled pages).
A page can have inbound links yet be unreachable (a disconnected component); a page
can have zero inbound links yet still exist in the crawl dataset (a true orphan).
"""
from __future__ import annotations

from collections import Counter, deque

from app.scanner.bulk import normalize_dedup_key
from app.scanner.signals.links import GENERIC_ANCHOR_TEXTS

# Depth >= this many hops from the seed is flagged DEEP_PAGE. A configurable, documented
# constant (not a claim about what Google/any crawler can or can't reach) — three hops
# past the homepage is a common, but NOT universal, "getting hard to find" threshold.
DEEP_PAGE_THRESHOLD = 3

# A crawl needs at least this many pages before a single-referrer page is flagged
# WEAK_INBOUND_COVERAGE — on a tiny site, one referring page is completely normal, so
# the issue is gated to a large-enough sample that "weak" is actually meaningful.
_MIN_PAGES_FOR_WEAK_INBOUND = 5

# A page's unique outbound-link count is flagged HIGH_OUTBOUND_PAGE only when it's
# BOTH clearly above the crawl's own average (crawl-relative, never a fixed universal
# "good site structure" number) AND at least this floor, so a small crawl where every
# page happens to link to 4 others doesn't get flagged.
_HIGH_OUTBOUND_FLOOR = 5
_HIGH_OUTBOUND_RATIO = 2.0

_UNRESOLVED_TARGET_CAP = 50
_UNRESOLVED_REFERRERS_CAP = 10
_TOP_REFERENCED_CAP = 10


def _safe_key(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return normalize_dedup_key(url)
    except Exception:   # noqa: BLE001 — a malformed URL must never break the report
        return None


UNAVAILABLE_SCAN_INCOMPLETE = {"available": False, "reason": "scan_incomplete"}
UNAVAILABLE_SINGLE_PAGE = {"available": False, "reason": "multi_page_crawl_required"}


def _depth_bucket_label(depth: int) -> str:
    return str(depth) if depth < 5 else "5+"


def build_crawl_graph_block(*, bulk_pages: list[dict] | None,
                            bulk_requested_urls: list[str] | None = None,
                            scan_ready: bool = True) -> dict:
    """The full, ungated Real Crawl Graph block embedded additively into
    `build_report`'s output (`report["crawl_graph"]`) — mirrors
    `reports.technical_seo.build_technical_seo_block`'s architecture.

    A single-page scan (`bulk_pages` is None — no `result["bulk"]` exists at all, the
    exact same distinction Technical SEO already uses) can never produce a meaningful
    site-wide graph, so it returns `multi_page_crawl_required` rather than a fabricated
    "this one page is an orphan" claim."""
    if not scan_ready:
        return dict(UNAVAILABLE_SCAN_INCOMPLETE)
    if not bulk_pages:
        return dict(UNAVAILABLE_SINGLE_PAGE)

    # ---- node set: every URL genuinely attempted in this crawl ----
    node_keys: dict[str, str] = {}       # normalized key -> canonical (first-seen) URL
    page_by_key: dict[str, dict] = {}
    for p in bulk_pages:
        u = p.get("url")
        key = _safe_key(u)
        if not key:
            continue
        node_keys.setdefault(key, u)
        page_by_key[key] = p

    # ---- raw internal-link occurrences (before de-duplication) ----
    raw_edges: list[dict] = []           # every {source, target, anchor_text}, may repeat
    unresolved: dict[str, list[str]] = {}   # target url -> [referring source urls]
    for key, p in page_by_key.items():
        if p.get("error"):
            continue                     # no HTML was ever fetched -> no outgoing evidence
        src_url = node_keys[key]
        for lt in (p.get("link_targets") or []):
            tgt = lt.get("target")
            tgt_key = _safe_key(tgt)
            if not tgt_key or tgt_key == key:
                continue                 # a self-link is never a graph edge
            if tgt_key in node_keys:
                raw_edges.append({"source": src_url, "target": node_keys[tgt_key],
                                  "anchor_text": lt.get("anchor_text") or None})
            else:
                unresolved.setdefault(tgt, []).append(src_url)

    # ---- de-duplicated edges (one per source->target pair) + adjacency ----
    seen_pairs: dict[tuple, dict] = {}
    for e in raw_edges:
        pair = (_safe_key(e["source"]), _safe_key(e["target"]))
        if pair not in seen_pairs:
            seen_pairs[pair] = e
    edges = sorted(seen_pairs.values(), key=lambda e: (e["source"], e["target"]))

    outbound_adj: dict[str, set] = {}
    inbound_adj: dict[str, set] = {}
    for (s, t) in seen_pairs:
        outbound_adj.setdefault(s, set()).add(t)
        inbound_adj.setdefault(t, set()).add(s)

    raw_outbound = Counter(_safe_key(e["source"]) for e in raw_edges)
    raw_inbound = Counter(_safe_key(e["target"]) for e in raw_edges)

    anchor_counts: dict[str, Counter] = {}
    empty_anchor_inbound: Counter = Counter()
    for e in raw_edges:
        tkey = _safe_key(e["target"])
        if e.get("anchor_text"):
            anchor_counts.setdefault(tkey, Counter())[e["anchor_text"]] += 1
        else:
            empty_anchor_inbound[tkey] += 1

    # ---- seed + BFS reachability/depth ----
    requested = [u for u in (bulk_requested_urls or []) if u]
    seed_url = requested[0] if requested else None
    seed_key = _safe_key(seed_url)
    graph_has_seed = bool(seed_key and seed_key in node_keys)

    depth_by_key: dict[str, int] = {}
    if graph_has_seed:
        depth_by_key[seed_key] = 0
        q = deque([seed_key])
        while q:
            cur = q.popleft()
            for nxt in outbound_adj.get(cur, ()):
                if nxt not in depth_by_key:
                    depth_by_key[nxt] = depth_by_key[cur] + 1
                    q.append(nxt)

    # ---- per-page records ----
    avg_outbound = (sum(len(v) for v in outbound_adj.values()) / len(node_keys)) if node_keys else 0.0
    pages: list[dict] = []
    for key, url in node_keys.items():
        p = page_by_key.get(key) or {}
        is_error = bool(p.get("error"))
        is_seed = key == seed_key
        inbound_pages = len(inbound_adj.get(key, ()))
        outbound_pages = len(outbound_adj.get(key, ()))
        if not graph_has_seed:
            reachable = None
        else:
            reachable = key in depth_by_key
        depth = depth_by_key.get(key) if reachable else None
        # The seed naturally has zero inbound links inside its own graph — never a
        # fabricated orphan. An error/never-fetched page has no meaningful "content
        # orphan" verdict (Technical SEO already covers "this URL errors").
        if is_seed or is_error:
            orphan = None
        else:
            orphan = inbound_pages == 0
        anchors = anchor_counts.get(key)
        pages.append({
            "url": url,
            "is_seed": is_seed,
            "inbound_pages": inbound_pages,
            "outbound_pages": outbound_pages,
            "inbound_links": raw_inbound.get(key, 0),
            "outbound_links": raw_outbound.get(key, 0),
            "empty_anchor_inbound_links": empty_anchor_inbound.get(key, 0),
            "reachable": reachable,
            "depth": depth,
            "orphan": orphan,
            "referring_anchors": ([{"text": t, "count": n} for t, n in anchors.most_common(5)]
                                 if anchors else []),
        })
    pages.sort(key=lambda r: r["url"])

    orphans = [p for p in pages if p["orphan"] is True]
    unresolved_targets = sorted(
        [{"target": t, "referenced_by": sorted(set(srcs))[:_UNRESOLVED_REFERRERS_CAP]}
         for t, srcs in unresolved.items()],
        key=lambda r: r["target"])[:_UNRESOLVED_TARGET_CAP]

    top_referenced = sorted(
        [{"url": p["url"], "inbound_pages": p["inbound_pages"]} for p in pages if p["inbound_pages"] > 0],
        key=lambda r: (-r["inbound_pages"], r["url"]))[:_TOP_REFERENCED_CAP]

    depth_counts: Counter = Counter(_depth_bucket_label(p["depth"]) for p in pages if p["depth"] is not None)
    order = [str(d) for d in range(5)] + ["5+"]
    depth_histogram = [{"label": lbl, "count": depth_counts.get(lbl, 0)} for lbl in order
                       if depth_counts.get(lbl, 0) > 0]

    reachable_count = sum(1 for p in pages if p["reachable"] is True)
    disconnected_count = sum(1 for p in pages if p["reachable"] is False)
    unknown_reachability = sum(1 for p in pages if p["reachable"] is None)
    max_depth = max((p["depth"] for p in pages if p["depth"] is not None), default=None)

    summary = {
        "pages": len(pages),
        "internal_edges": len(edges),
        "reachable_pages": reachable_count,
        "disconnected_pages": disconnected_count,
        "orphan_pages": len(orphans),
        "max_depth": max_depth,
        "unknown_reachability_pages": unknown_reachability,
    }

    return {
        "available": True,
        "seed_url": seed_url if graph_has_seed else None,
        "summary": summary,
        "pages": pages,
        "edges": edges,
        "orphans": orphans,
        "depth_histogram": depth_histogram,
        "top_referenced": top_referenced,
        "unresolved_internal_targets": unresolved_targets,
        "issues": _issue_summary(pages, avg_outbound, len(node_keys)),
    }


def _issue_summary(pages: list[dict], avg_outbound: float, total_pages: int) -> list[dict]:
    """Deterministic, evidence-only issue rollup. A metric being merely non-zero is
    never enough on its own (e.g. one outbound link is not an issue) — every rule below
    is gated by an explicit, documented condition."""
    def add(out: list[dict], code: str, severity: str, label: str, urls: list[str]) -> None:
        if urls:
            out.append({"code": code, "severity": severity, "count": len(urls),
                        "label": label, "affected_urls": sorted(urls)})

    out: list[dict] = []
    orphan_urls = [p["url"] for p in pages if p["orphan"] is True]
    add(out, "ORPHAN_PAGE", "High",
       f"{len(orphan_urls)} crawled page{'s' if len(orphan_urls) != 1 else ''} "
       f"{'have' if len(orphan_urls) != 1 else 'has'} zero inbound internal links",
       orphan_urls)

    disconnected_urls = [p["url"] for p in pages if p["reachable"] is False]
    add(out, "DISCONNECTED_PAGE", "Medium",
       f"{len(disconnected_urls)} crawled page{'s' if len(disconnected_urls) != 1 else ''} "
       f"{'are' if len(disconnected_urls) != 1 else 'is'} not reachable from the crawl seed",
       disconnected_urls)

    deep_urls = [p["url"] for p in pages if p["depth"] is not None and p["depth"] >= DEEP_PAGE_THRESHOLD]
    add(out, "DEEP_PAGE", "Low",
       f"{len(deep_urls)} page{'s' if len(deep_urls) != 1 else ''} "
       f"{'are' if len(deep_urls) != 1 else 'is'} deep within the internal crawl path "
       f"({DEEP_PAGE_THRESHOLD}+ hops from the seed)",
       deep_urls)

    outbound_floor = max(_HIGH_OUTBOUND_FLOOR, round(avg_outbound * _HIGH_OUTBOUND_RATIO))
    hub_urls = [p["url"] for p in pages if p["outbound_pages"] >= outbound_floor and total_pages > 1]
    add(out, "HIGH_OUTBOUND_PAGE", "Low",
       f"{len(hub_urls)} page{'s' if len(hub_urls) != 1 else ''} link{'s' if len(hub_urls) == 1 else ''} "
       f"out to an unusually high number of other crawled pages",
       hub_urls)

    weak_urls = ([p["url"] for p in pages
                 if p["orphan"] is False and p["reachable"] is not False and p["inbound_pages"] == 1]
                if total_pages >= _MIN_PAGES_FOR_WEAK_INBOUND else [])
    add(out, "WEAK_INBOUND_COVERAGE", "Low",
       f"{len(weak_urls)} page{'s' if len(weak_urls) != 1 else ''} "
       f"{'have' if len(weak_urls) != 1 else 'has'} only a single internal referring page",
       weak_urls)

    generic_urls, empty_urls = [], []
    for p in pages:
        anchors = p.get("referring_anchors") or []
        named_total = sum(a["count"] for a in anchors)
        if named_total >= 2:
            generic_n = sum(a["count"] for a in anchors if a["text"].strip().lower() in GENERIC_ANCHOR_TEXTS)
            if generic_n == named_total:
                generic_urls.append(p["url"])
        if p["inbound_links"] >= 2 and p["empty_anchor_inbound_links"] == p["inbound_links"]:
            empty_urls.append(p["url"])
    add(out, "GENERIC_ANCHOR_PATTERN", "Low",
       f"{len(generic_urls)} page{'s' if len(generic_urls) != 1 else ''} "
       f"{'are' if len(generic_urls) != 1 else 'is'} referenced only by generic anchor text "
       f"(e.g. 'click here')",
       generic_urls)

    add(out, "EMPTY_ANCHOR_PATTERN", "Low",
       f"{len(empty_urls)} page{'s' if len(empty_urls) != 1 else ''} "
       f"{'are' if len(empty_urls) != 1 else 'is'} referenced only by links with no anchor text",
       empty_urls)
    return [i for i in out if i["count"] > 0]


_SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def gate_crawl_graph(block: dict | None, unlocked: bool, *,
                     free_orphan_limit: int = 3, free_issue_limit: int = 3) -> dict | None:
    """Server-side trim to a free preview — same model as `technical_seo.gate_technical_seo`.
    Graph EDGES are withheld entirely for a free caller (per the task's explicit "be
    careful, edges can reveal many paid URLs" instruction) rather than trimmed to a
    small sample — a partial edge list would still expose real paid URL relationships.
    The free `pages` preview reuses the SAME orphan URLs already shown (never a
    different, independently-chosen slice of paid URLs)."""
    if not block or unlocked or block.get("available") is False:
        return block

    orphans = block.get("orphans") or []
    issues = sorted(block.get("issues") or [], key=lambda i: _SEVERITY_RANK.get(i["severity"], 9))
    pages = block.get("pages") or []
    edges = block.get("edges") or []
    top_referenced = block.get("top_referenced") or []
    unresolved = block.get("unresolved_internal_targets") or []

    preview_orphans = orphans[:free_orphan_limit]
    preview_issues = [{**i, "affected_urls": (i.get("affected_urls") or [])[:free_orphan_limit]}
                      for i in issues[:free_issue_limit]]

    return {
        **block,
        "preview": True,
        "orphans": preview_orphans,
        "locked_orphan_count": max(0, len(orphans) - len(preview_orphans)),
        "issues": preview_issues,
        "locked_issue_count": max(0, len(issues) - len(preview_issues)),
        "pages": preview_orphans,
        "locked_page_count": max(0, len(pages) - len(preview_orphans)),
        "edges": [],
        "locked_edge_count": len(edges),
        "top_referenced": top_referenced[:3],
        "locked_top_referenced_count": max(0, len(top_referenced) - 3),
        "unresolved_internal_targets": [],
        "locked_unresolved_target_count": len(unresolved),
    }


__all__ = ["build_crawl_graph_block", "gate_crawl_graph",
          "UNAVAILABLE_SCAN_INCOMPLETE", "UNAVAILABLE_SINGLE_PAGE", "DEEP_PAGE_THRESHOLD"]
