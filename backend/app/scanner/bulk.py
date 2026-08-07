"""Bulk scan: build + fetch a user-provided list of URLs.

A bulk scan replaces the old "site crawl": instead of discovering pages from a
sitemap, the user supplies up to `bulk_max_urls` URLs (pasted, or via a CSV / XLSX
upload). This module owns:

- **Input parsing** — `extract_urls_from_file` reads the first column of a CSV
  (stdlib) or XLSX (openpyxl), skipping a header row that isn't itself a URL.
- **Validation** — `build_url_list` normalizes, dedupes, and SSRF-validates every URL
  (`app.core.ssrf.validate_url`), returning the accepted list plus a per-URL
  `skipped` summary (reasons: invalid, duplicate, ssrf_blocked, over_limit). Pure and
  network-light (only DNS via validate_url), so it can run in a threadpool.
- **Domain policy** — `is_single_registered_domain` supports the Free "bulk trial",
  which is limited to one website at a time.
- **Concurrent fetch** — `fetch_pages` fetches the accepted URLs concurrently
  (bounded by a semaphore + a whole-job budget), each SSRF-validated again before
  fetch, yielding results as they settle. Safety is never weakened.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import zipfile
from urllib.parse import urlparse

from app.core.fetch import fetch
from app.core.ssrf import UnsafeUrlError, has_forbidden_url_chars, validate_url

# Skip reasons surfaced in the API summary.
SKIP_INVALID = "invalid"
SKIP_DUPLICATE = "duplicate"
SKIP_SSRF = "ssrf_blocked"
SKIP_OVER_LIMIT = "over_limit"

# User-facing message for a file we recognise but can't parse (legacy .xls / .ods) or
# that isn't a supported list format. Lists what IS accepted and how to convert.
SUPPORTED_HINT = (
    "Unsupported file type. Upload your URLs as a .csv, .xlsx, .txt, .json, .jsonl, "
    "or .tsv file — one URL per line, or a spreadsheet with URLs in a column. "
    "For a legacy .xls or .ods file, please re-save it as .csv or .xlsx first.")

# A spreadsheet may hold far more rows than we'll ever accept; only bulk_max_urls URLs
# are kept, so we never parse more than this many rows when scanning for URLs.
MAX_SCAN_ROWS = 5000


# ------------------------------- url helpers -------------------------------
def registered_domain(host: str | None) -> str:
    """Best-effort registered domain (eTLD+1) without a public-suffix list: the last
    two labels, ignoring a leading 'www.'. Good enough for the single-domain policy;
    the multi-label-TLD edge (example.co.uk) is intentionally lenient."""
    host = (host or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = [p for p in host.split(".") if p]
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def _dedup_key(url: str) -> str:
    """Path-aware normalized key so a trailing slash / 'www.' host / scheme collapse to
    one entry, but distinct paths and queries stay distinct."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "/").rstrip("/") or "/"
    return f"{host}{path}?{p.query}" if p.query else f"{host}{path}"


def normalize_url(raw: str) -> str | None:
    """Trim, add an https:// scheme when missing, and return a syntactically valid
    http(s) URL with a host — or None if it can't be one. Network-free."""
    if not raw or not str(raw).strip():
        return None
    url = str(raw).strip()
    if has_forbidden_url_chars(url):   # e.g. a stray trailing '<' — reject, don't strip
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname or "." not in p.hostname:
        return None
    return url


def _looks_like_url(cell: str) -> bool:
    return normalize_url(cell) is not None


def _decode_text(data: bytes) -> str:
    """Decode uploaded text robustly, never raising. Tries utf-8-sig (handles a UTF-8
    BOM), then utf-16 for Excel 'Unicode text' exports (only when a UTF-16 BOM is
    actually present, so a Windows cp1252 file isn't silently mangled into garbage),
    then cp1252, finally utf-8 with replacement as a last resort."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeError:
        pass
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeError:
            pass
    try:
        return data.decode("cp1252")
    except UnicodeError:
        pass
    return data.decode("utf-8", errors="replace")


# ------------------------------- file parsing -------------------------------
def _read_delimited_rows(data: bytes, delimiter: str | None) -> list[list[str]]:
    """Decode + split a delimited file into rows. When ``delimiter`` is None the
    delimiter (',' / tab / ';') is sniffed from the content, falling back to comma."""
    text = _decode_text(data)
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",\t;").delimiter
        except csv.Error:
            delimiter = ","
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def parse_csv(data: bytes) -> list[str]:
    """URL column of a delimited file. The delimiter (',' / tab / ';') is sniffed from
    the content; a first row whose first cell isn't a URL is treated as a header and
    skipped. Column A is preferred, but if it holds no URLs every cell is scanned."""
    return _first_column(_read_delimited_rows(data, None))


def parse_tsv(data: bytes) -> list[str]:
    """Tab-delimited variant of parse_csv. Kept as an explicit path (delimiter forced to
    tab) rather than folded into the sniffer: a single-column TSV is ambiguous to sniff,
    and an explicit .tsv extension should win over a guess."""
    return _first_column(_read_delimited_rows(data, "\t"))


def parse_text(data: bytes) -> list[str]:
    """Extract URLs from a plain-text file (.txt/.md/.log or any unknown text): one URL
    per line (or comma/tab-separated). NOT an HTML parser — a token containing markup
    like href="..." is rejected by the URL check, so an .html upload yields nothing;
    HTML is not a supported list format (see extract_urls_from_file / SUPPORTED_HINT).
    A '#'-only line is skipped. Within a line, commas and tabs separate fields, and the
    first URL-looking whitespace token of each field is taken — so a line can carry prose
    or a trailing '# comment' and still yield its URL, while a single comma/tab line
    yields every URL on it."""
    out: list[str] = []
    for line in _decode_text(data).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for field in re.split(r"[,\t]", line):
            for tok in field.split():
                if _looks_like_url(tok):
                    out.append(tok)
                    break
    return out


_JSON_URL_KEYS = ("url", "link", "href")


def _url_from_json_item(item) -> str | None:
    """A URL string from a JSON item that is either a bare string or an object with a
    url / URL / link / href key (case-insensitive)."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for k, v in item.items():
            if isinstance(k, str) and k.lower() in _JSON_URL_KEYS and isinstance(v, str):
                return v
    return None


def _urls_from_json(obj) -> list[str]:
    """URLs from a parsed JSON value: an object with a "urls" array, a bare
    url-bearing object, or an array of strings/url-bearing objects."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() == "urls" and isinstance(v, list):
                obj = v
                break
        else:
            u = _url_from_json_item(obj)
            return [u] if u else []
    if isinstance(obj, list):
        return [u for u in (_url_from_json_item(i) for i in obj) if u]
    return []


def parse_json(data: bytes) -> list[str]:
    """Extract URLs from JSON — a top-level array of strings, an array of url-bearing
    objects, or an object with a "urls" array — plus .jsonl (one JSON value per line).
    Never raises on malformed JSON: falls back to plain-text extraction."""
    text = _decode_text(data)
    try:
        return _urls_from_json(json.loads(text))
    except (ValueError, TypeError):
        pass
    # Try JSONL: one JSON value per line. If any non-blank line fails to parse, this
    # isn't JSONL either → treat the whole upload as free text (never raise).
    parsed = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed.append(json.loads(line))
        except ValueError:
            return parse_text(data)
    return _urls_from_json(parsed) if parsed else parse_text(data)


def parse_xlsx(data: bytes) -> list[str]:
    """URL column of the first worksheet of an XLSX. Column A is preferred (header row
    skipped like CSV); if it holds no URLs, every column is scanned. Read is streamed
    (read_only=True) and capped at MAX_SCAN_ROWS rows — only bulk_max_urls URLs are ever
    kept, so there's no reason to materialise a huge sheet."""
    import openpyxl  # imported lazily so the rest of the app doesn't require it

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= MAX_SCAN_ROWS:
                break
            rows.append([("" if c is None else str(c)) for c in row])
    finally:
        wb.close()
    return _first_column(rows)


def _first_column(rows: list[list]) -> list[str]:
    """Extract the URL list from parsed rows (capped at MAX_SCAN_ROWS). Prefer column A —
    its non-empty cells, dropping a leading header cell that isn't itself a URL. If
    column A yields no URLs, fall back to scanning every cell in row-major order and
    collecting URL-looking values, so a sheet with URLs in (say) column C still works
    instead of silently returning nothing. Non-URL cells in a URL-bearing column A are
    kept as-is and get reported as `invalid` skips downstream, preserving prior behaviour."""
    rows = rows[:MAX_SCAN_ROWS]
    col = [str(r[0]).strip() for r in rows if r and str(r[0] or "").strip()]
    if col and not _looks_like_url(col[0]):
        col = col[1:]   # drop a header row
    if any(_looks_like_url(c) for c in col):
        return col
    found: list[str] = []
    for r in rows:
        for cell in (r or []):
            val = str(cell or "").strip()
            if val and _looks_like_url(val):
                found.append(val)
    return found


def _zip_is_opendocument(data: bytes) -> bool:
    """True if a PK zip is actually an OpenDocument (.ods) file, detected via its small
    uncompressed 'mimetype' member — so a mislabelled .ods is caught by content, not
    name. Only that one tiny member is read; the archive is never fully decompressed."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return b"opendocument" in zf.read("mimetype")[:64].lower()
    except (KeyError, zipfile.BadZipFile, OSError):
        return False


def _sniff_format(data: bytes, name: str) -> str:
    """Decide how to parse an upload by inspecting its LEADING BYTES first, using the
    filename only as a hint. Returns one of: "zip" (xlsx/xlsm), "ods" (OpenDocument),
    "ole2" (legacy .xls), "csv", "tsv", "json", "jsonl", "text". Magic bytes win over
    the extension so a mislabelled file is handled by its real content, not its name."""
    head = data[:8]
    if head.startswith(b"PK\x03\x04"):                 # zip container: xlsx / xlsm / ods
        if name.endswith(".ods") or _zip_is_opendocument(data):
            return "ods"
        return "zip"
    if head.startswith(b"\xD0\xCF\x11\xE0"):
        return "ole2"                                  # legacy OLE2 .xls
    # Text-family: let the extension pick the most specific text parser, else infer.
    if name.endswith(".jsonl") or name.endswith(".ndjson"):
        return "jsonl"
    if name.endswith(".json"):
        return "json"
    if name.endswith(".tsv"):
        return "tsv"
    if name.endswith(".csv"):
        return "csv"
    return "text"


def extract_urls_from_file(filename: str, data: bytes) -> list[str]:
    """Parse an uploaded URL list. Dispatch is by CONTENT (leading bytes) first, then
    the filename extension as a hint — see `_sniff_format`. Raises ValueError for a
    format we recognise but can't yet parse (legacy .xls / .ods)."""
    name = (filename or "").lower()
    kind = _sniff_format(data, name)
    if kind == "zip":
        return parse_xlsx(data)
    if kind in ("ole2", "ods"):                        # legacy .xls / .ods — need extra deps
        raise ValueError(SUPPORTED_HINT)
    if kind == "jsonl" or kind == "json":
        return parse_json(data)
    if kind == "tsv":
        return parse_tsv(data)
    if kind == "csv":
        return parse_csv(data)
    return parse_text(data)


# ------------------------------- validation -------------------------------
def build_url_list(raw_urls, max_urls: int) -> tuple[list[str], list[dict]]:
    """Normalize + SSRF-validate + dedupe a list of raw URL strings, capping at
    `max_urls`. Returns ``(accepted, skipped)`` where each skipped entry is
    ``{"url", "reason"}``. Order is preserved; the first occurrence of a URL wins.

    Only DNS I/O (via validate_url) — safe to run in a threadpool from an async
    endpoint so it never blocks the event loop."""
    accepted: list[str] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    for raw in raw_urls:
        norm = normalize_url(raw)
        if not norm:
            skipped.append({"url": str(raw), "reason": SKIP_INVALID})
            continue
        try:
            safe = validate_url(norm)
        except UnsafeUrlError:
            skipped.append({"url": norm, "reason": SKIP_SSRF})
            continue
        key = _dedup_key(safe)
        if key in seen:
            skipped.append({"url": norm, "reason": SKIP_DUPLICATE})
            continue
        if len(accepted) >= max_urls:
            skipped.append({"url": norm, "reason": SKIP_OVER_LIMIT})
            continue
        seen.add(key)
        accepted.append(safe)
    return accepted, skipped


def is_single_registered_domain(urls: list[str]) -> bool:
    """True if every URL is on the same registered domain (Free bulk-trial policy)."""
    domains = {registered_domain(urlparse(u).hostname) for u in urls}
    return len(domains) <= 1


# ------------------------------- concurrent fetch -------------------------------
async def fetch_pages(urls, *, concurrency: int, budget_seconds: int,
                      page_timeout: int, transport=None):
    """Async generator that fetches ``urls`` concurrently and yields
    ``(url, PageBundle | Exception | None)`` as each settles, in completion order.

    - Concurrency is bounded by ``concurrency``; each fetch is SSRF-validated and
      capped at ``page_timeout`` seconds.
    - A per-URL failure is yielded as its Exception — never raised — so one bad page
      cannot fail the job.
    - The run honours ``budget_seconds``: once the budget expires, pages not yet
      started yield ``(url, None)`` (not attempted) so the caller can mark the scan
      truncated without recording a spurious error row.

    Consumed sequentially, so the caller's per-page work (scoring, DB progress writes)
    runs single-threaded and never races on a shared Session."""
    sem = asyncio.Semaphore(max(1, concurrency))
    loop = asyncio.get_event_loop()
    deadline = loop.time() + budget_seconds

    async def _one(u: str):
        async with sem:
            if loop.time() >= deadline:
                return (u, None)   # budget exhausted before we got to this URL
            try:
                validate_url(u)
                bundle = await asyncio.wait_for(fetch(u, transport=transport), timeout=page_timeout)
                return (u, bundle)
            except Exception as e:   # noqa: BLE001 - any failure is captured, never fatal
                return (u, e)

    tasks = [asyncio.create_task(_one(u)) for u in urls]
    for coro in asyncio.as_completed(tasks):
        yield await coro
