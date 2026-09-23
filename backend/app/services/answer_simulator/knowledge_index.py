"""AEO Answer Simulator — Knowledge Index construction.

Builds a lightweight, per-monitor "knowledge index" purely from evidence AEOMirror
has ALREADY scanned and persisted — no new crawl, no external call. The verbatim
per-page text universe stored anywhere in this codebase is: title (<=120 chars),
meta description (<=300 chars), H1 text (<=200 chars), interrogative headings
(H1-H4 text that literally ends in "?"), FAQPage Q&A pairs, Organization/
LocalBusiness entity fields (name/url/logo/sameAs), AND — since the body-content-
evidence phase — bounded, chunked BODY-CONTENT passages (real paragraph/heading-
boundary text, ~600 words/chunk with overlap; see
app/scanner/signals/content.py::build_body_evidence). See
app/scanner/signals/{metadata,content,schema}.py for exactly what each field caps
at and how it's extracted; nothing here re-extracts or re-parses HTML.

A monitor's own `latest_scan_id` is always a single-page scan (see
app/monitoring/runner.py), so this module widens the source set to every COMPLETED
Scan the org owns whose normalized host matches the monitor's domain — its own scan
history plus any bulk-scan pages for that domain. Bulk-scan pages carry a REDUCED
evidence tier (title/description/H1/word_count/body_evidence — routes_scan.py's
`_bulk_page_entry` intentionally drops FAQ/heading-question/entity fields to keep a
bulk scan's stored result small, but DOES pull body_evidence through); single-page
scans carry the FULL tier (every field, including FAQ/entity).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.db.models import SCAN_COMPLETED, Monitor, Scan


def _bare_host(url: str | None) -> str:
    """Lowercased, `www.`-stripped host of a URL/bare domain. Empty when it can't be
    derived. A small, local copy of the same normalization already duplicated in
    services/answer_tracking/{runner,service}.py — kept independent rather than
    importing a private helper across module boundaries."""
    if not url:
        return ""
    host = url.strip().lower()
    if "//" not in host:
        host = "//" + host
    host = (urlparse(host).netloc or "").split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


@dataclass
class EvidenceDoc:
    doc_id: str
    url: str
    scan_id: str
    evidence_tier: str          # "full" | "reduced"
    title: str | None = None
    description: str | None = None
    h1: str | None = None
    heading_questions: list[str] = field(default_factory=list)
    faq_pairs: list[dict] = field(default_factory=list)   # [{"question","answer"}]
    entity: dict | None = None                             # {"name","url","logo","same_as"}
    body_chunks: list[dict] = field(default_factory=list)  # [{"id","text","start_word","end_word"}]
    content_hash: str | None = None
    body_truncated: bool = False


@dataclass
class EvidenceUnit:
    unit_id: str
    doc_id: str
    url: str
    field_name: str      # "title"|"meta_description"|"h1"|"heading_question"|
                          # "faq_question"|"faq_answer"|"entity_name"|"entity_sameas"|
                          # "body_chunk"
    text: str
    evidence_tier: str    # "full" | "reduced"
    # body_chunk units only — carried through to the retrieval-output shape (see
    # services/answer_simulator/batch.py) so the UI can show which passage/position
    # supported an answer. `heading` is the doc's own H1 as nearest-heading CONTEXT
    # (an approximation — not per-chunk DOM tracking), documented as such.
    chunk_id: str | None = None
    word_start: int | None = None
    word_end: int | None = None
    heading: str | None = None


def _section_evidence(sections: list[dict] | None, signal_id: str) -> dict:
    for s in sections or []:
        if s.get("id") == signal_id:
            return s.get("evidence") or {}
    return {}


def _doc_from_single_page_scan(scan: Scan) -> EvidenceDoc:
    result = scan.result or {}
    sections = result.get("sections") or []
    meta_ev = _section_evidence(sections, "metadata")
    content_ev = _section_evidence(sections, "content")
    schema_ev = _section_evidence(sections, "schema")
    entity_evidence = schema_ev.get("entity_evidence") or {}
    entity = entity_evidence if entity_evidence.get("name") else None
    body_ev = content_ev.get("body_evidence") or {}
    return EvidenceDoc(
        doc_id=f"{scan.id}:{scan.url}", url=scan.url, scan_id=scan.id, evidence_tier="full",
        title=meta_ev.get("title") or None,
        description=meta_ev.get("description") or None,
        h1=content_ev.get("h1_text") or None,
        heading_questions=list(content_ev.get("heading_questions") or []),
        faq_pairs=list(schema_ev.get("faq_questions") or []),
        entity=entity,
        body_chunks=list(body_ev.get("chunks") or []),
        content_hash=body_ev.get("content_hash"),
        body_truncated=bool(body_ev.get("truncated")),
    )


def _docs_from_bulk_scan(scan: Scan) -> list[EvidenceDoc]:
    pages = ((scan.result or {}).get("bulk") or {}).get("pages") or []
    docs = []
    for p in pages:
        url = p.get("url")
        if not url:
            continue
        body_ev = p.get("body_evidence") or {}
        docs.append(EvidenceDoc(
            doc_id=f"{scan.id}:{url}", url=url, scan_id=scan.id, evidence_tier="reduced",
            title=p.get("title") or None,
            description=p.get("description") or None,
            h1=p.get("h1") or None,
            body_chunks=list(body_ev.get("chunks") or []),
            content_hash=body_ev.get("content_hash"),
            body_truncated=bool(body_ev.get("truncated")),
        ))
    return docs


def resolve_evidence_sources(db: Session, monitor: Monitor) -> list[EvidenceDoc]:
    """Every COMPLETED Scan this org owns whose normalized host matches the monitor's
    domain, decomposed into EvidenceDocs. Deduplicated by URL — the most recently
    scanned version of a given page wins. Zero new fetch: every source here already
    exists in `scans.result`."""
    domain = _bare_host(monitor.normalized_url or monitor.url)
    if not domain:
        return []
    scans = (
        db.query(Scan)
        .filter(Scan.organization_id == monitor.organization_id, Scan.status == SCAN_COMPLETED)
        .order_by(Scan.created_at.desc())
        .all()
    )
    by_url: dict[str, EvidenceDoc] = {}
    for scan in scans:
        if _bare_host(scan.url) != domain:
            continue
        result = scan.result or {}
        if (result.get("bulk") or {}).get("pages"):
            for doc in _docs_from_bulk_scan(scan):
                by_url.setdefault(doc.url, doc)
        else:
            doc = _doc_from_single_page_scan(scan)
            by_url.setdefault(doc.url, doc)
    return list(by_url.values())


# Every text field an EvidenceDoc might carry, mapped to the EvidenceUnit field name
# retrieval.py boosts by. Order is deterministic (dict insertion order in Python).
_SCALAR_FIELDS = [("title", "title"), ("description", "meta_description"), ("h1", "h1")]


def build_knowledge_index(sources: list[EvidenceDoc]) -> list[EvidenceUnit]:
    """Flattens every EvidenceDoc into short, independently-scorable EvidenceUnits.
    Field-level units (title/description/h1/etc) are single verbatim values already
    capped upstream to <=120/300/200 chars; body_chunk units are real ~600-word
    paragraph/heading-boundary passages (see
    scanner/signals/content.py::chunk_blocks) — already bounded per-page/per-scan at
    the scanner layer, never re-truncated or re-parsed here."""
    units: list[EvidenceUnit] = []
    for doc in sources:
        for attr, field_name in _SCALAR_FIELDS:
            text = getattr(doc, attr)
            if text:
                units.append(EvidenceUnit(
                    unit_id=f"{doc.doc_id}#{field_name}", doc_id=doc.doc_id, url=doc.url,
                    field_name=field_name, text=text, evidence_tier=doc.evidence_tier,
                ))
        for i, hq in enumerate(doc.heading_questions):
            units.append(EvidenceUnit(
                unit_id=f"{doc.doc_id}#heading_question[{i}]", doc_id=doc.doc_id, url=doc.url,
                field_name="heading_question", text=hq, evidence_tier=doc.evidence_tier,
            ))
        for i, pair in enumerate(doc.faq_pairs):
            q, a = pair.get("question"), pair.get("answer")
            if q:
                units.append(EvidenceUnit(
                    unit_id=f"{doc.doc_id}#faq_question[{i}]", doc_id=doc.doc_id, url=doc.url,
                    field_name="faq_question", text=q, evidence_tier=doc.evidence_tier,
                ))
            if a:
                units.append(EvidenceUnit(
                    unit_id=f"{doc.doc_id}#faq_answer[{i}]", doc_id=doc.doc_id, url=doc.url,
                    field_name="faq_answer", text=a, evidence_tier=doc.evidence_tier,
                ))
        if doc.entity:
            if doc.entity.get("name"):
                units.append(EvidenceUnit(
                    unit_id=f"{doc.doc_id}#entity_name", doc_id=doc.doc_id, url=doc.url,
                    field_name="entity_name", text=doc.entity["name"], evidence_tier=doc.evidence_tier,
                ))
            for i, sa in enumerate((doc.entity.get("same_as") or [])):
                units.append(EvidenceUnit(
                    unit_id=f"{doc.doc_id}#entity_sameas[{i}]", doc_id=doc.doc_id, url=doc.url,
                    field_name="entity_sameas", text=sa, evidence_tier=doc.evidence_tier,
                ))
        for chunk in doc.body_chunks:
            text = chunk.get("text")
            if not text:
                continue
            units.append(EvidenceUnit(
                unit_id=f"{doc.doc_id}#body_chunk[{chunk.get('id')}]", doc_id=doc.doc_id,
                url=doc.url, field_name="body_chunk", text=text, evidence_tier=doc.evidence_tier,
                chunk_id=chunk.get("id"), word_start=chunk.get("start_word"),
                word_end=chunk.get("end_word"), heading=doc.h1,
            ))
    return units
