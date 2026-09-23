"""Content-structure signal: H1 count, H2 hierarchy, semantic HTML, heading problems."""
from __future__ import annotations

import copy
import hashlib
import re
import zlib

from app.scanner.signals.base import SignalContext, SignalResult

ID, LABEL, WEIGHT = "content", "Content Structure", 12

# --- Content Cannibalization & Duplicate Content Intelligence (additive; no score
# impact) --- word-trigram shingle fingerprint of the page's own MAIN content, used for
# deterministic near-duplicate/overlap detection across a bulk scan's pages (see
# reports/content_intelligence.py). A single, documented, non-"sophisticated"
# boilerplate rule: prefer <main>/<article> when present (excludes nav/header/footer by
# construction); otherwise the <body> with <nav>/<header>/<footer>/<aside> removed.
# Never mutates the shared `ctx.soup` (every other signal reads it too) — works on an
# independent copy.
_SHINGLE_SIZE = 3
_MAX_SHINGLES = 2000
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# --- Full Body-Content Evidence (additive; no score impact) --- bounded, chunked
# body-content text for the AEO Answer Simulator's retrieval (see
# services/answer_simulator/knowledge_index.py). Centralized limits so the scanner
# never stores unbounded text: a page's body content is capped at
# MAX_BODY_WORDS_PER_PAGE words / MAX_BODY_CHUNKS_PER_PAGE chunks (truncated
# deterministically + flagged, never silently pretended complete);
# MAX_TOTAL_BODY_WORDS_PER_SCAN is enforced across an entire bulk scan by
# api/routes_scan.py (only that layer has cross-page state).
MAX_BODY_WORDS_PER_PAGE = 4800
MAX_BODY_CHUNKS_PER_PAGE = 8
MAX_TOTAL_BODY_WORDS_PER_SCAN = 40000
_CHUNK_TARGET_WORDS = 600
_CHUNK_OVERLAP_WORDS = 75
# A single block/sentence longer than this is split further (sentence boundary,
# then a hard word-count slice as the last resort) rather than becoming one huge
# chunk — see chunk_blocks().
_CHUNK_HARD_LIMIT_WORDS = 900


def _scoped_content(soup):
    """The SAME boilerplate-stripped scope used for both word-count/shingle scoring
    (_main_content_text) and body-evidence chunking (_main_content_blocks) — one
    extraction, never two independent implementations that could drift apart.
    Prefers <main>/<article>; otherwise <body> with <nav>/<header>/<footer>/<aside>
    removed. Always strips <script>/<style>/<noscript>. Works on an independent
    copy — never mutates the shared `ctx.soup` other signals also read."""
    working = copy.copy(soup)
    main = working.find(["main", "article"])
    scope = main if main is not None else (working.find("body") or working)
    if main is None:
        for tag in scope.find_all(["nav", "header", "footer", "aside"]):
            tag.decompose()
    for tag in scope.find_all(["script", "style", "noscript"]):
        tag.decompose()
    return scope


def _main_content_text(soup) -> str:
    return _scoped_content(soup).get_text(" ", strip=True)


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text or "") if s.strip()]


def _main_content_blocks(soup) -> list[str]:
    """Ordered, readable (NOT normalized/lowercased) block-level texts — paragraphs,
    list items, blockquotes, headings — from the SAME scope `_main_content_text()`
    uses. This is the paragraph/heading-boundary structure `chunk_blocks()` needs;
    kept human-readable since chunks are shown to the user and sent to the optional
    LLM verbatim. Falls back to sentence-splitting the flattened scope text when
    block-tag extraction finds negligible content — many real sites wrap body copy
    in bare <div>s with no <p> tags. Deterministic; never an LLM."""
    scope = _scoped_content(soup)
    blocks = [t for t in (
        tag.get_text(" ", strip=True)
        for tag in scope.find_all(["p", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"])
    ) if t]
    flat_text = scope.get_text(" ", strip=True)
    flat_words = len(flat_text.split())
    block_words = sum(len(b.split()) for b in blocks)
    if flat_words > 0 and block_words < flat_words * 0.3:
        return _split_sentences(flat_text)
    return blocks


def normalize_content_text(text: str) -> str:
    """ONE deterministic normalization: lowercase, strip punctuation (kept as a word
    boundary, not deleted — "AI-ready" -> "ai ready" not "aiready"), collapse
    whitespace. No stemming, no stopword removal, no LLM."""
    t = _PUNCT_RE.sub(" ", (text or "").lower())
    return _WS_RE.sub(" ", t).strip()


def content_shingles(normalized_text: str) -> list[int]:
    """Deterministic word-trigram shingle fingerprint (CRC32 hashes — stable across
    runs/processes, unlike Python's randomized str hash()). Capped at `_MAX_SHINGLES`
    (smallest hash values kept) so one very long page can't bloat the stored report;
    typical pages are far under the cap."""
    words = normalized_text.split()
    if len(words) < _SHINGLE_SIZE:
        return []
    grams = (" ".join(words[i:i + _SHINGLE_SIZE]) for i in range(len(words) - _SHINGLE_SIZE + 1))
    hashes = {zlib.crc32(g.encode("utf-8")) for g in grams}
    return sorted(hashes)[:_MAX_SHINGLES]


def content_hash(normalized_text: str) -> str:
    """Deterministic CHANGE-DETECTION hash over a page's normalized body content —
    equality only ("did this page's content change since the last scan"), never a
    similarity/relevance score. Kept separate from `content_shingles` (a similarity
    fingerprint, not an equality hash) and from retrieval scoring."""
    return hashlib.sha256((normalized_text or "").encode("utf-8")).hexdigest()[:24]


def _normalize_chunk_units(blocks: list[str], target_words: int) -> list[str]:
    """Any block bigger than `target_words` (the greedy accumulator below could
    never fit it into a single well-sized chunk otherwise) is split at sentence
    boundaries; a single sentence still over `_CHUNK_HARD_LIMIT_WORDS` falls back
    to a hard word-count slice — the ticket's "preferred order" for chunk
    boundaries: paragraph -> heading -> sentence -> hard limit."""
    units: list[str] = []
    for block in blocks:
        words = block.split()
        if len(words) <= target_words:
            units.append(block)
            continue
        for sentence in _split_sentences(block):
            sw = sentence.split()
            if len(sw) <= _CHUNK_HARD_LIMIT_WORDS:
                units.append(sentence)
            else:
                for i in range(0, len(sw), _CHUNK_HARD_LIMIT_WORDS):
                    units.append(" ".join(sw[i:i + _CHUNK_HARD_LIMIT_WORDS]))
    return units


def chunk_blocks(blocks: list[str], *, target_words: int = _CHUNK_TARGET_WORDS,
                 overlap_words: int = _CHUNK_OVERLAP_WORDS) -> list[dict]:
    """Deterministic chunking, no LLM, no stemming. Greedily accumulates whole
    blocks (paragraph/heading boundaries preferred) up to `target_words`, then
    starts the next chunk seeded with the previous chunk's trailing
    `overlap_words`. A block over `target_words` is pre-split by
    `_normalize_chunk_units`. Empty input -> []; small input -> one chunk."""
    units = _normalize_chunk_units(blocks, target_words)
    chunks: list[dict] = []
    current_units: list[str] = []
    current_word_count = 0
    start_word = 0

    def _flush():
        nonlocal start_word
        if not current_units:
            return
        chunk_text = " ".join(current_units)
        chunk_words = chunk_text.split()
        chunks.append({
            "id": f"chunk-{len(chunks)}", "text": chunk_text,
            "start_word": start_word, "end_word": start_word + len(chunk_words),
        })
        return chunk_words

    for unit in units:
        unit_word_count = len(unit.split())
        if current_units and current_word_count + unit_word_count > target_words:
            chunk_words = _flush()
            overlap_tail = chunk_words[-overlap_words:] if overlap_words else []
            start_word = start_word + len(chunk_words) - len(overlap_tail)
            current_units = [" ".join(overlap_tail)] if overlap_tail else []
            current_word_count = len(overlap_tail)
        current_units.append(unit)
        current_word_count += unit_word_count

    _flush()
    return chunks


def build_body_evidence(soup, *, normalized_main_text: str, word_count: int) -> dict:
    """Bounded, chunked body-content evidence for the AEO Answer Simulator. Reuses
    the SAME boilerplate-stripped extraction word_count/content_shingles already
    use (`_scoped_content`/`_main_content_blocks`) — never a second extraction
    implementation. Truncates deterministically past MAX_BODY_WORDS_PER_PAGE /
    MAX_BODY_CHUNKS_PER_PAGE and records `truncated: true` rather than silently
    pretending full coverage. Never persists raw HTML/CSS/JS/nav markup — only
    already-boilerplate-stripped readable text."""
    blocks = _main_content_blocks(soup)
    truncated = False

    total_block_words = sum(len(b.split()) for b in blocks)
    if total_block_words > MAX_BODY_WORDS_PER_PAGE:
        truncated = True
        capped: list[str] = []
        running = 0
        for b in blocks:
            if running >= MAX_BODY_WORDS_PER_PAGE:
                break
            bw = b.split()
            remaining = MAX_BODY_WORDS_PER_PAGE - running
            if len(bw) > remaining:
                capped.append(" ".join(bw[:remaining]))
                running += remaining
                break
            capped.append(b)
            running += len(bw)
        blocks = capped

    chunks = chunk_blocks(blocks)
    if len(chunks) > MAX_BODY_CHUNKS_PER_PAGE:
        truncated = True
        chunks = chunks[:MAX_BODY_CHUNKS_PER_PAGE]

    return {
        "word_count": word_count,
        "content_hash": content_hash(normalized_main_text),
        "truncated": truncated,
        "chunks": chunks,
    }


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

    # Phase 4 (Question Mining): real question-shaped headings, copied verbatim — never
    # generated. Purely additive evidence; does not affect this signal's score.
    heading_questions = []
    seen_q = set()
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = h.get_text(" ", strip=True)
        if text and text.endswith("?") and text.lower() not in seen_q:
            seen_q.add(text.lower())
            heading_questions.append(text)
        if len(heading_questions) >= 20:
            break

    # Content Cannibalization & Duplicate Content Intelligence (additive; no score
    # impact): the first H1's own text (for duplicate-H1 detection), the page's usable
    # word count, and a deterministic content fingerprint (for near-duplicate/overlap
    # detection across a bulk scan) — see reports/content_intelligence.py.
    h1_text = h1s[0].get_text(" ", strip=True)[:200] if len(h1s) >= 1 else None
    main_text = _main_content_text(soup)
    normalized = normalize_content_text(main_text)
    word_count = len(normalized.split()) if normalized else 0

    return SignalResult.build(
        ID, LABEL, WEIGHT, score, issues=issues, recommendations=recs,
        evidence={
            "h1_count": len(h1s), "h2_count": len(h2s),
            "heading_jumps": jumps, "semantic_html": semantic,
            "paragraphs": len(paras), "lists": len(lists),
            "heading_questions": heading_questions,
            "h1_text": h1_text,
            "word_count": word_count,
            "content_shingles": content_shingles(normalized),
            # Full Body-Content Evidence (additive; no score impact) — bounded,
            # chunked readable body text for the AEO Answer Simulator's retrieval.
            "body_evidence": build_body_evidence(
                soup, normalized_main_text=normalized, word_count=word_count),
        },
    )
