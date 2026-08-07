"""Accessibility signal: alt text, ARIA, form labels, lang. Better accessibility
also makes a page cleaner for AI extraction."""
from __future__ import annotations

from app.scanner.signals.base import SignalContext, SignalResult

ID, LABEL, WEIGHT = "accessibility", "Accessibility", 9


def analyze(ctx: SignalContext) -> SignalResult:
    soup = ctx.soup
    issues: list = []
    recs: list = []
    score = 0.0

    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        score += 15
    else:
        issues.append("No lang attribute on <html>.")
        recs.append('Set <html lang="..."> so engines and readers know the language.')

    imgs = soup.find_all("img")
    with_alt = sum(1 for i in imgs if i.has_attr("alt"))
    if not imgs:
        score += 30
    elif with_alt / len(imgs) >= 0.9:
        score += 30
    else:
        score += int(30 * with_alt / len(imgs))
        issues.append(f"{len(imgs) - with_alt}/{len(imgs)} images are missing alt text.")
        recs.append("Add descriptive alt text to every meaningful image.")

    aria = soup.find_all(attrs={"role": True}) + soup.select("[aria-label], [aria-labelledby], [aria-describedby]")
    if aria:
        score += 20
    else:
        recs.append("Use ARIA roles/labels for interactive and landmark regions.")

    inputs = soup.find_all(["input", "select", "textarea"])
    real_inputs = [i for i in inputs if (i.get("type") or "").lower() not in ("hidden", "submit", "button")]
    if not real_inputs:
        score += 20
    else:
        labelled = 0
        for i in real_inputs:
            _id = i.get("id")
            if i.get("aria-label") or i.get("aria-labelledby") \
               or (_id and soup.find("label", attrs={"for": _id})):
                labelled += 1
        if labelled / len(real_inputs) >= 0.9:
            score += 20
        else:
            issues.append(f"{len(real_inputs) - labelled}/{len(real_inputs)} form fields lack labels.")
            recs.append("Associate a <label> (or aria-label) with every form field.")

    # descriptive links/buttons help both AT and AI
    empty_btns = sum(1 for b in soup.find_all("button") if not b.get_text(strip=True) and not b.get("aria-label"))
    if empty_btns == 0:
        score += 15
    else:
        recs.append(f"Give {empty_btns} unlabeled button(s) accessible text.")

    return SignalResult.build(
        ID, LABEL, WEIGHT, score, issues=issues, recommendations=recs,
        evidence={
            "lang": bool(html_tag and html_tag.get("lang")),
            "images": len(imgs), "images_with_alt": with_alt,
            "aria_usage": len(aria), "form_fields": len(real_inputs),
        },
    )
