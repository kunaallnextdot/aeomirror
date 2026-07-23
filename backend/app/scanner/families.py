"""The six signal families. Each is a pure function of a PageBundle returning
a list of CheckResult. No LLM, no network here: fetch already happened.

These are grouped in one module for readability. They can be split into a
package (one file per family) with zero call-site changes if you prefer.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from app.scanner.models import CheckResult, PageBundle

AI_BOTS = {
    "gptbot_allowed": ["GPTBot"],
    "claudebot_allowed": ["ClaudeBot", "Claude-User", "anthropic-ai"],
    "perplexitybot_allowed": ["PerplexityBot"],
    "google_extended_ok": ["Google-Extended"],
}


# ----------------------------- helpers -----------------------------

def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "lxml")


def _jsonld_blocks(soup: BeautifulSoup) -> list:
    blocks = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        try:
            blocks.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            blocks.append({"__parse_error__": True})
    return blocks


def _types_in(blocks: list) -> set:
    types = set()

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

    for b in blocks:
        walk(b)
    return types


def _robots_blocks(robots_txt: str, agent_names: list) -> Optional[bool]:
    """True if blocked from '/', False if allowed, None if unmentioned."""
    if not robots_txt:
        return None
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

    def blocked_for(agent: str) -> Optional[bool]:
        if agent not in groups:
            return None
        return "/" in groups[agent]

    for name in agent_names:
        state = blocked_for(name)
        if state is not None:
            return state
    return blocked_for("*")


# ---------------------- 1. crawler access ----------------------

def crawler_access(page: PageBundle) -> list:
    results = []
    for check_id, agents in AI_BOTS.items():
        blocked = _robots_blocks(page.robots_txt, agents)
        bot = agents[0]
        if blocked is True:
            results.append(CheckResult(check_id, f"{bot} access", "fail",
                f"{bot} is disallowed in robots.txt. This AI crawler cannot read the site.",
                f"Remove the Disallow rule for {bot} in robots.txt."))
        elif blocked is None and not page.robots_txt:
            results.append(CheckResult(check_id, f"{bot} access", "warn",
                "No robots.txt found. Access is allowed by default but not declared.",
                f"Add a robots.txt that explicitly allows {bot}."))
        else:
            results.append(CheckResult(check_id, f"{bot} access", "pass",
                f"{bot} is allowed to crawl the site.", ""))

    blanket = _robots_blocks(page.robots_txt, ["*"]) is True
    results.append(CheckResult("no_blanket_disallow", "No blanket block",
        "fail" if blanket else "pass",
        "robots.txt blocks all crawlers with Disallow: /" if blanket
        else "No site-wide crawler block detected.",
        "Remove or scope the Disallow: / under User-agent: *" if blanket else ""))
    return results


# ---------------------- 2. render parity (v1 heuristic) ----------------------

def render_parity(page: PageBundle) -> list:
    soup = _soup(page.html)
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    text = soup.get_text(" ", strip=True)
    text_len = len(text)
    ratio = text_len / max(len(page.html), 1)
    results = []

    if text_len >= 500:
        results.append(CheckResult("has_real_text", "Readable text present", "pass",
            f"{text_len} characters of extractable text found.", ""))
    elif text_len >= 150:
        results.append(CheckResult("has_real_text", "Readable text present", "warn",
            f"Only {text_len} characters of text. Thin content for AI extraction.",
            "Add substantive on-page text."))
    else:
        results.append(CheckResult("has_real_text", "Readable text present", "fail",
            f"Almost no extractable text ({text_len} chars). Page likely renders via JS.",
            "Server-render or pre-render the main content."))

    if ratio >= 0.10:
        results.append(CheckResult("content_in_raw_html", "Content in source", "pass",
            f"Text-to-HTML ratio {ratio:.0%}. Content is in the raw HTML.", ""))
    elif ratio >= 0.04:
        results.append(CheckResult("content_in_raw_html", "Content in source", "warn",
            f"Low text-to-HTML ratio ({ratio:.0%}). Some content may be JS-dependent.",
            "Move key content into server-rendered HTML."))
    else:
        results.append(CheckResult("content_in_raw_html", "Content in source", "fail",
            f"Very low text-to-HTML ratio ({ratio:.0%}). Content is likely JS-injected.",
            "Server-render the page."))

    shell = bool(soup.select_one("#root, #app, #__next")) and text_len < 300
    results.append(CheckResult("not_js_shell", "Not an empty shell",
        "fail" if shell else "pass",
        "Page looks like an SPA shell with no server-rendered content." if shell
        else "Page is not an empty client-rendered shell.",
        "Enable SSR or static pre-rendering for this route." if shell else ""))
    return results


# ---------------------- 3. schema validity ----------------------

def schema(page: PageBundle) -> list:
    soup = _soup(page.html)
    blocks = _jsonld_blocks(soup)
    parse_error = any(isinstance(b, dict) and b.get("__parse_error__") for b in blocks)
    types = _types_in(blocks)
    results = []

    results.append(CheckResult("has_jsonld", "Structured data present",
        "pass" if blocks else "fail",
        f"{len(blocks)} JSON-LD block(s) found." if blocks else "No JSON-LD structured data found.",
        "" if blocks else "Add JSON-LD so AI engines can parse entities."))

    has_org = bool(types & {"Organization", "Corporation", "LocalBusiness"})
    results.append(CheckResult("has_org_schema", "Organization schema",
        "pass" if has_org else "fail",
        "Organization-level schema found." if has_org
        else "No Organization schema. AI cannot reliably identify the brand entity.",
        "" if has_org else "Add Organization JSON-LD with name, url, logo, sameAs."))

    tset = types & {"Product", "Article", "BlogPosting", "FAQPage", "Service"}
    results.append(CheckResult("has_type_schema", "Content-type schema",
        "pass" if tset else "warn",
        f"Content-type schema found: {', '.join(sorted(tset))}." if tset
        else "No Product/Article/FAQ schema for the page content.",
        "" if tset else "Add schema matching the page type."))

    results.append(CheckResult("schema_parses", "Schema is valid JSON",
        "fail" if parse_error else "pass",
        "One or more JSON-LD blocks failed to parse." if parse_error
        else "All structured data parses cleanly.",
        "Fix the malformed JSON-LD." if parse_error else ""))
    return results


# ---------------------- 4. structure ----------------------

def structure(page: PageBundle) -> list:
    soup = _soup(page.html)
    results = []

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    if title and 10 <= len(title) <= 65:
        results.append(CheckResult("has_title", "Page title", "pass",
            f'Title present: "{title[:60]}"', ""))
    elif title:
        results.append(CheckResult("has_title", "Page title", "warn",
            f"Title length {len(title)} is outside the 10 to 65 range.",
            "Tighten the title tag."))
    else:
        results.append(CheckResult("has_title", "Page title", "fail",
            "No title tag found.", "Add a descriptive <title> tag."))

    h1s = soup.find_all("h1")
    if len(h1s) == 1:
        results.append(CheckResult("single_h1", "Single H1", "pass",
            "Exactly one H1 heading found.", ""))
    elif len(h1s) == 0:
        results.append(CheckResult("single_h1", "Single H1", "fail",
            "No H1 heading found.", "Add one clear H1."))
    else:
        results.append(CheckResult("single_h1", "Single H1", "warn",
            f"{len(h1s)} H1 headings found.", "Reduce to a single H1."))

    md = soup.find("meta", attrs={"name": "description"})
    ok = bool(md and md.get("content"))
    results.append(CheckResult("has_meta_description", "Meta description",
        "pass" if ok else "warn",
        "Meta description present." if ok else "No meta description found.",
        "" if ok else "Add a meta description."))

    can = soup.find("link", attrs={"rel": "canonical"})
    results.append(CheckResult("has_canonical", "Canonical URL",
        "pass" if can else "warn",
        "Canonical link present." if can else "No canonical link found.",
        "" if can else "Add a canonical link."))

    results.append(CheckResult("has_sitemap_ref", "Sitemap",
        "pass" if page.sitemap_present else "warn",
        "Sitemap found." if page.sitemap_present else "No sitemap.xml detected.",
        "" if page.sitemap_present else "Publish a sitemap.xml."))

    results.append(CheckResult("has_llms_txt", "llms.txt",
        "pass" if page.llms_txt_present else "warn",
        "llms.txt found." if page.llms_txt_present
        else "No llms.txt found (emerging convention, engine adoption unconfirmed).",
        "" if page.llms_txt_present else "Optional: add an llms.txt. Emerging standard."))
    return results


# ---------------------- 5. extractability ----------------------

def extractability(page: PageBundle) -> list:
    soup = _soup(page.html)
    results = []

    semantic = bool(soup.find(["article", "main", "section"]))
    results.append(CheckResult("semantic_html", "Semantic HTML",
        "pass" if semantic else "warn",
        "Semantic elements present." if semantic else "No semantic sectioning elements found.",
        "" if semantic else "Wrap content in <main> and <article>."))

    text = soup.get_text(" ", strip=True).lower()
    has_qa = (bool(soup.find(attrs={"itemtype": re.compile("FAQPage")}))
              or "frequently asked" in text or text.count("?") >= 3)
    results.append(CheckResult("has_faq_or_qa", "Question-answer content",
        "pass" if has_qa else "warn",
        "Question-answer content detected." if has_qa else "No FAQ or Q-and-A structure found.",
        "" if has_qa else "Add an FAQ section."))

    scannable = len(soup.find_all("p")) >= 3 or len(soup.find_all(["ul", "ol"])) >= 1
    results.append(CheckResult("scannable_structure", "Scannable structure",
        "pass" if scannable else "warn",
        "Content is broken into scannable blocks." if scannable
        else "Content is not broken into paragraphs or lists.",
        "" if scannable else "Break content into paragraphs and lists."))
    return results


# ---------------------- 6. freshness ----------------------

def freshness(page: PageBundle) -> list:
    soup = _soup(page.html)
    blocks = _jsonld_blocks(soup)
    has_date = False

    def walk(o):
        nonlocal has_date
        if isinstance(o, dict):
            if "dateModified" in o or "datePublished" in o:
                has_date = True
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for b in blocks:
        walk(b)

    results = [CheckResult("has_date_modified", "Date in schema",
        "pass" if has_date else "warn",
        "dateModified/datePublished present in schema." if has_date
        else "No dateModified in structured data.",
        "" if has_date else "Add dateModified to schema.")]

    time_tag = soup.find("time") or soup.find(
        attrs={"class": re.compile("date|published|updated", re.I)})
    results.append(CheckResult("has_visible_date", "Visible date",
        "pass" if time_tag else "warn",
        "A visible date element was found." if time_tag
        else "No visible published/updated date.",
        "" if time_tag else "Show a visible last-updated date."))
    return results


FAMILY_FUNCS = [
    ("crawler_access", "Crawler access", crawler_access),
    ("render_parity", "Render parity", render_parity),
    ("schema", "Schema validity", schema),
    ("structure", "Structure", structure),
    ("extractability", "Extractability", extractability),
    ("freshness", "Freshness", freshness),
]
