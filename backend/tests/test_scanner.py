"""Scanner unit tests. No network: pure functions over hand-made PageBundles."""
from app.scanner.engine import score
from app.scanner.models import PageBundle

GOOD_HTML = """<!doctype html><html lang="en"><head>
<title>Best Cold Brew Coffee Maker: Buyer's Guide 2026</title>
<meta name="description" content="An independent guide to cold brew makers.">
<link rel="canonical" href="https://good.com/g">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"BrewLab","url":"https://good.com"}</script>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Article","datePublished":"2026-06-01","dateModified":"2026-07-10"}</script>
</head><body><main><article>
<h1>Best Cold Brew Coffee Maker</h1><time datetime="2026-07-10">Updated</time>
<p>Cold brew is brewed with cold water over 12 to 24 hours for a smoother cup. This guide answers common questions.</p>
<p>We tested nine devices across four weeks measuring extraction and durability.</p>
<h2>FAQ</h2>
<p>How long does cold brew last? Up to two weeks refrigerated.</p>
<p>Is it stronger? The concentrate is, before dilution.</p>
<ul><li>Refractometer tested</li><li>Cleaning timed</li></ul>
</article></main></body></html>"""

GOOD_ROBOTS = """User-agent: *
Allow: /
User-agent: GPTBot
Allow: /
User-agent: ClaudeBot
Allow: /
User-agent: PerplexityBot
Allow: /
Sitemap: https://good.com/sitemap.xml"""

BAD_HTML = """<!doctype html><html><head><title>Home</title></head>
<body><div id="root"></div><script src="/main.bundle.js"></script></body></html>"""

BAD_ROBOTS = """User-agent: *
Disallow: /
User-agent: GPTBot
Disallow: /"""


def good_page():
    return PageBundle(url="https://good.com/g", html=GOOD_HTML,
                      robots_txt=GOOD_ROBOTS, llms_txt_present=True, sitemap_present=True)


def bad_page():
    return PageBundle(url="https://bad.com/", html=BAD_HTML,
                      robots_txt=BAD_ROBOTS, llms_txt_present=False, sitemap_present=False)


def test_good_page_scores_high():
    r = score(good_page())
    assert r.ars >= 90
    assert r.domain == "good.com"


def test_bad_page_scores_low():
    r = score(bad_page())
    assert r.ars <= 35


def test_blocked_gptbot_is_flagged():
    r = score(bad_page())
    ids = [i["id"] for i in r.top_issues]
    assert "gptbot_allowed" in ids or any("gptbot" in i for i in ids)


def test_crawler_strip_has_four_engines():
    r = score(good_page())
    assert len(r.crawlers) == 4


def test_families_sum_to_ars():
    r = score(good_page())
    assert r.ars == max(0, min(100, round(sum(f.earned for f in r.families))))
