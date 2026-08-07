"""Performance-signal heuristics: compression, caching headers, image optimization,
lazy loading. These are static/header checks only (no runtime measurement)."""
from __future__ import annotations

from app.scanner.signals.base import SignalContext, SignalResult

ID, LABEL, WEIGHT = "performance", "Performance Signals", 10

_MODERN_IMG = (".webp", ".avif")


def analyze(ctx: SignalContext) -> SignalResult:
    soup = ctx.soup
    issues: list = []
    recs: list = []
    score = 0.0

    encoding = ctx.header("content-encoding").lower()
    if any(e in encoding for e in ("gzip", "br", "zstd", "deflate")):
        score += 25
    else:
        issues.append("Response is not compressed (no gzip/br Content-Encoding).")
        recs.append("Enable gzip or Brotli compression at the server/CDN.")

    cache_control = ctx.header("cache-control")
    if cache_control or ctx.header("etag") or ctx.header("expires"):
        score += 25
    else:
        issues.append("No caching headers (Cache-Control/ETag/Expires).")
        recs.append("Set Cache-Control and ETag headers so responses can be cached.")

    imgs = soup.find_all("img")
    total = len(imgs)
    lazy = sum(1 for i in imgs if (i.get("loading") or "").lower() == "lazy")
    sized = sum(1 for i in imgs if i.get("width") and i.get("height"))
    modern = sum(1 for i in imgs if str(i.get("src", "")).lower().endswith(_MODERN_IMG))

    if total == 0:
        score += 50  # nothing to optimize
    else:
        if lazy / total >= 0.5:
            score += 20
        else:
            issues.append(f"Only {lazy}/{total} images use lazy loading.")
            recs.append('Add loading="lazy" to below-the-fold images.')
        if sized / total >= 0.5:
            score += 15
        else:
            recs.append("Set explicit width/height on images to reduce layout shift.")
        if modern > 0:
            score += 15
        else:
            recs.append("Serve images in modern formats (WebP/AVIF).")

    return SignalResult.build(
        ID, LABEL, WEIGHT, score, issues=issues, recommendations=recs,
        evidence={
            "content_encoding": encoding or "none",
            "cache_control": cache_control or "none",
            "images": total, "lazy_loaded": lazy, "with_dimensions": sized,
            "modern_formats": modern,
        },
    )
