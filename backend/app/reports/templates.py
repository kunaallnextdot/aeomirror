"""Structured, rule-based copy for the recommendation engine (Phase 6).

One template per signal id. No LLM: every "why it matters", fix, and example is
authored here so reports are deterministic, fast, and free to generate. Each
template supplies the category, business/AI-visibility impact, difficulty, fix
time, an explanation, an implementation example, and the expected outcome.

The signal's own `issues` and `recommendations` (from Phase 3) are merged in at
build time to describe the SPECIFIC findings for the scanned page.
"""
from __future__ import annotations

# The 9 report categories (spec) each map from one or more signals.
CATEGORY = {
    "robots": "Crawlability",
    "sitemap": "Indexability",
    "metadata": "Metadata",
    "schema": "Schema",
    "content": "Content",
    "links": "Internal Linking",
    "performance": "Performance",
    "accessibility": "Accessibility",
    "freshness": "Content",          # freshness folds into the Content category
    "ai_readiness": "AI Extractability",
}

CATEGORIES = [
    "Crawlability", "Metadata", "Schema", "Content", "Performance",
    "Accessibility", "Internal Linking", "Indexability", "AI Extractability",
]

# difficulty vocabulary: Easy | Moderate | Hard
TEMPLATES: dict[str, dict] = {
    "robots": {
        "title": "AI crawler access via robots.txt",
        "difficulty": "Easy",
        "fix_time": "15–30 minutes",
        "business_impact": (
            "If AI crawlers can't fetch your pages, your brand is invisible in AI "
            "answers — prospects get your competitors' recommendations instead of yours."
        ),
        "ai_impact": (
            "robots.txt decides whether GPTBot, ClaudeBot, PerplexityBot and "
            "Google-Extended can read your site at all. A block here caps every other signal."
        ),
        "explanation": (
            "AI assistants honor robots.txt. A missing file, an over-broad `Disallow: /`, "
            "or an explicit block of an AI user-agent stops the crawlers that feed ChatGPT, "
            "Claude, Gemini and Perplexity from ever seeing your content."
        ),
        "example": (
            "# robots.txt — allow AI crawlers and point them to your sitemap\n"
            "User-agent: GPTBot\nAllow: /\n\n"
            "User-agent: ClaudeBot\nAllow: /\n\n"
            "User-agent: PerplexityBot\nAllow: /\n\n"
            "User-agent: Google-Extended\nAllow: /\n\n"
            "Sitemap: https://example.com/sitemap.xml"
        ),
        "outcome": "AI crawlers can reach your pages, making them eligible to be cited in AI answers.",
    },
    "sitemap": {
        "title": "XML sitemap for discovery",
        "difficulty": "Easy",
        "fix_time": "30–60 minutes",
        "business_impact": (
            "Without a sitemap, new and deep pages are discovered slowly or not at all, "
            "so your freshest content is missing when AI engines answer."
        ),
        "ai_impact": (
            "A sitemap tells crawlers exactly which URLs to read and when they changed, "
            "improving how completely and how quickly your site is indexed for AI."
        ),
        "explanation": (
            "An XML sitemap is the map crawlers use to find every important page. Missing "
            "or unreferenced sitemaps leave discovery to chance, especially for large or "
            "JavaScript-heavy sites."
        ),
        "example": (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
            "  <url>\n    <loc>https://example.com/</loc>\n"
            "    <lastmod>2026-07-01</lastmod>\n  </url>\n"
            "</urlset>\n\n# Reference it in robots.txt:\nSitemap: https://example.com/sitemap.xml"
        ),
        "outcome": "Crawlers discover every important URL quickly, keeping your AI-visible content complete and current.",
    },
    "metadata": {
        "title": "Page metadata (title, description, canonical)",
        "difficulty": "Easy",
        "fix_time": "20–45 minutes per page",
        "business_impact": (
            "Titles and descriptions are how AI and search summarize you. Weak metadata "
            "means your pages are mislabeled or skipped in favor of clearer competitors."
        ),
        "ai_impact": (
            "AI engines lean on <title>, meta description and canonical tags to understand "
            "what a page is about and which version is authoritative."
        ),
        "explanation": (
            "Missing or duplicate titles, absent meta descriptions, or no canonical tag make "
            "it hard for engines to classify and attribute your content correctly."
        ),
        "example": (
            "<head>\n"
            "  <title>AI Visibility Scanner for Websites | AEOMirror</title>\n"
            "  <meta name=\"description\" content=\"Scan any site to see what ChatGPT, "
            "Claude, Gemini and Perplexity can read and cite.\">\n"
            "  <link rel=\"canonical\" href=\"https://example.com/features\">\n"
            "</head>"
        ),
        "outcome": "Each page is clearly and uniquely described, so engines summarize and attribute it correctly.",
    },
    "schema": {
        "title": "Structured data (Schema.org / JSON-LD)",
        "difficulty": "Moderate",
        "fix_time": "1–3 hours",
        "business_impact": (
            "Structured data is the difference between being paraphrased vaguely and being "
            "quoted with your name, product, and facts intact in AI answers."
        ),
        "ai_impact": (
            "JSON-LD gives AI engines machine-readable facts (Organization, Product, FAQ, "
            "Article) they can lift directly instead of inferring them from prose."
        ),
        "explanation": (
            "Without valid JSON-LD, engines must guess your entity, offerings and Q&A from "
            "prose. Adding Organization + content-type schema makes your facts unambiguous."
        ),
        "example": (
            "<script type=\"application/ld+json\">\n"
            "{\n  \"@context\": \"https://schema.org\",\n  \"@type\": \"Organization\",\n"
            "  \"name\": \"AEOMirror\",\n  \"url\": \"https://example.com\",\n"
            "  \"logo\": \"https://example.com/logo.png\"\n}\n</script>"
        ),
        "outcome": "AI engines read your entity and content facts directly, improving how often and how accurately you're cited.",
    },
    "content": {
        "title": "Content structure and extractability",
        "difficulty": "Moderate",
        "fix_time": "2–4 hours",
        "business_impact": (
            "AI answers pull from clearly structured, self-contained passages. Thin or "
            "unstructured content simply doesn't get surfaced."
        ),
        "ai_impact": (
            "Headings, concise paragraphs, lists and real rendered text let engines extract "
            "quotable answers instead of skipping the page."
        ),
        "explanation": (
            "A single H1, a logical heading hierarchy, and substantive server-rendered text "
            "make content easy for AI to parse, chunk and quote."
        ),
        "example": (
            "<h1>AI Visibility, explained</h1>\n"
            "<h2>What AI crawlers can read</h2>\n"
            "<p>Server-render the main content so it's present in the initial HTML…</p>\n"
            "<ul><li>Use one H1 per page</li><li>Break copy into short paragraphs</li></ul>"
        ),
        "outcome": "Engines can extract clean, quotable passages from your pages, increasing the chance you're the cited source.",
    },
    "links": {
        "title": "Internal linking and site structure",
        "difficulty": "Moderate",
        "fix_time": "1–2 hours",
        "business_impact": (
            "Orphaned or shallow pages rarely get crawled or cited. Good internal links "
            "spread authority to the pages you most want AI to recommend."
        ),
        "ai_impact": (
            "Descriptive internal links help engines understand topical relationships and "
            "reach every important page from your key entry points."
        ),
        "explanation": (
            "Too few internal links, generic anchor text ('click here'), or dead ends make "
            "it harder for crawlers to navigate and weight your content."
        ),
        "example": (
            "<!-- Descriptive, topical anchor text -->\n"
            "<a href=\"/guides/ai-visibility\">Read our AI visibility guide</a>\n"
            "<a href=\"/features/schema-generator\">Generate Schema.org markup</a>"
        ),
        "outcome": "Crawlers reach and correctly weight every key page, and topical relationships become clear to AI.",
    },
    "performance": {
        "title": "Performance and delivery signals",
        "difficulty": "Hard",
        "fix_time": "0.5–3 days",
        "business_impact": (
            "Slow, heavy pages time out crawlers and frustrate users — both cost you "
            "visibility and conversions."
        ),
        "ai_impact": (
            "Fast responses, compression and sensible caching help crawlers fetch more of "
            "your pages within their time budget."
        ),
        "explanation": (
            "Missing compression, no caching headers, or bloated responses slow crawlers and "
            "reduce how much of your site gets read in each visit."
        ),
        "example": (
            "# Enable gzip/brotli and caching (nginx example)\n"
            "gzip on;\n"
            "gzip_types text/html text/css application/javascript application/json;\n"
            "location /assets/ { add_header Cache-Control \"public, max-age=31536000\"; }"
        ),
        "outcome": "Pages load fast and compress well, so crawlers read more of your site per visit.",
    },
    "accessibility": {
        "title": "Accessibility and semantic HTML",
        "difficulty": "Moderate",
        "fix_time": "2–5 hours",
        "business_impact": (
            "Accessible, semantic markup widens your audience and gives machines the same "
            "clear structure that assistive tech relies on."
        ),
        "ai_impact": (
            "Landmarks, alt text and labeled elements give AI unambiguous structure and "
            "descriptions to extract from."
        ),
        "explanation": (
            "Semantic elements (main, nav, article), image alt text and a declared language "
            "make content easier for both people and machines to interpret."
        ),
        "example": (
            "<html lang=\"en\">\n<body>\n  <main>\n"
            "    <img src=\"chart.png\" alt=\"AI visibility score rising from 42 to 78\">\n"
            "  </main>\n</body>\n</html>"
        ),
        "outcome": "Content is unambiguous to assistive tech and to AI, improving both reach and extractability.",
    },
    "freshness": {
        "title": "Content freshness signals",
        "difficulty": "Easy",
        "fix_time": "30–60 minutes",
        "business_impact": (
            "Content without a visible or structured date gives AI systems less signal about "
            "how current it is, which can make freshness harder to establish in "
            "freshness-sensitive contexts."
        ),
        "ai_impact": (
            "Visible and structured dates (dateModified) tell engines your content is current "
            "and worth citing over older material."
        ),
        "explanation": (
            "Without visible dates or dateModified in schema, engines can't tell how fresh "
            "your content is and may prefer newer-looking sources."
        ),
        "example": (
            "<script type=\"application/ld+json\">\n"
            "{\n  \"@context\": \"https://schema.org\",\n  \"@type\": \"Article\",\n"
            "  \"dateModified\": \"2026-07-20\"\n}\n</script>\n"
            "<p class=\"updated\">Last updated: 20 July 2026</p>"
        ),
        "outcome": "Engines have a clearer, unambiguous signal of how current your content is.",
    },
    "ai_readiness": {
        "title": "AI extractability (llms.txt, answer-ready content)",
        "difficulty": "Moderate",
        "fix_time": "1–2 hours",
        "business_impact": (
            "Answer-ready pages — clear Q&A, an llms.txt, direct definitions — are what AI "
            "engines quote. Without them you're paraphrased at best."
        ),
        "ai_impact": (
            "Emerging conventions like llms.txt and FAQ/Q&A blocks make it explicit what an "
            "AI should read and quote about you."
        ),
        "explanation": (
            "AI readiness covers the newest best practices: an llms.txt guide file, FAQ "
            "sections, and concise, self-contained answers that map to how people ask AI."
        ),
        "example": (
            "# /llms.txt\n"
            "# AEOMirror — AI Visibility Scanner\n"
            "> Scan any website to see what AI engines can read and cite.\n\n"
            "## Key pages\n"
            "- [Features](https://example.com/features): what the scanner checks\n"
            "- [Pricing](https://example.com/pricing): plans and limits"
        ),
        "outcome": "AI engines find explicit, answer-ready content and a guide to your best pages, raising quote-worthiness.",
    },
}


def template_for(signal_id: str) -> dict:
    """Return the template for a signal, with a safe generic fallback."""
    return TEMPLATES.get(signal_id, {
        "title": signal_id.replace("_", " ").title(),
        "difficulty": "Moderate",
        "fix_time": "1–2 hours",
        "business_impact": "Improving this area helps AI engines read and recommend your site.",
        "ai_impact": "Contributes to how completely AI engines can access and understand your content.",
        "explanation": "This signal affects how well AI assistants can read, understand and cite your site.",
        "example": "",
        "outcome": "Better AI visibility for your pages.",
    })
