"""Professional PDF report generator (Phase 6), built with reportlab (pure Python,
no system libraries). Produces a print-friendly, branded document:

  Cover page · Executive summary · Overall score (donut) · Category breakdown
  (bar chart + table) · Strengths/Weaknesses · Top priorities · Recommendations
  · Appendix.

`build_pdf(report)` returns PDF bytes. It is CPU-bound and synchronous; callers
run it in a threadpool so it never blocks the event loop / scans.
"""
from __future__ import annotations

import io

from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.charts.doughnut import Doughnut
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ------------------------------- brand palette -------------------------------
ACCENT = HexColor("#0FA6B4")     # slightly darker teal for print legibility
INK = HexColor("#0B0F14")
MID = HexColor("#5A6772")
LINE = HexColor("#D8DEE4")
LIGHT = HexColor("#EDF1F4")
GOOD = HexColor("#2FA36B")
WARN = HexColor("#C8862E")
BAD = HexColor("#D2453E")
PRI_COLORS = {"Critical": BAD, "High": HexColor("#E0722A"), "Medium": WARN, "Low": MID}


def _score_color(s: float):
    return GOOD if s >= 75 else WARN if s >= 45 else BAD


def _styles():
    ss = getSampleStyleSheet()
    base = ss["Normal"]
    base.fontName, base.fontSize, base.leading, base.textColor = "Helvetica", 9.5, 14, INK
    def mk(name, **kw):
        return ParagraphStyle(name, parent=base, **kw)
    return {
        "body": base,
        "h1": mk("h1", fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=INK, spaceBefore=6, spaceAfter=8),
        "h2": mk("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=INK, spaceBefore=12, spaceAfter=5),
        "h3": mk("h3", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=INK, spaceBefore=6, spaceAfter=2),
        "small": mk("small", fontSize=8.5, leading=12, textColor=MID),
        "label": mk("label", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=MID),
        "cover_title": mk("cover_title", fontName="Helvetica-Bold", fontSize=30, leading=34, textColor=INK),
        "cover_brand": mk("cover_brand", fontName="Helvetica-Bold", fontSize=13, textColor=ACCENT),
        "cover_domain": mk("cover_domain", fontName="Helvetica", fontSize=15, textColor=MID),
        "code": mk("code", fontName="Courier", fontSize=8, leading=11, textColor=INK,
                   backColor=LIGHT, borderPadding=6, leftIndent=2),
        "cell": mk("cell", fontSize=8.5, leading=11),
        "cellb": mk("cellb", fontName="Helvetica-Bold", fontSize=8.5, leading=11),
    }


# ------------------------------- charts -------------------------------
def _score_donut(score: int) -> Drawing:
    d = Drawing(150, 150)
    dn = Doughnut()
    dn.x, dn.y, dn.width, dn.height = 15, 5, 120, 120
    dn.data = [max(0.01, score), max(0.01, 100 - score)]
    dn.innerRadiusFraction = 0.66
    dn.slices.strokeColor = colors.white
    dn.slices.strokeWidth = 0.5
    dn.slices[0].fillColor = _score_color(score)
    dn.slices[1].fillColor = LIGHT
    d.add(dn)
    d.add(String(75, 66, str(score), fontSize=30, textAnchor="middle",
                 fillColor=INK, fontName="Helvetica-Bold"))
    d.add(String(75, 52, "of 100", fontSize=8, textAnchor="middle", fillColor=MID))
    return d


def _category_chart(cats: list[dict]) -> Drawing:
    cats = list(reversed(cats))  # bar charts plot bottom-up; keep listed order top-down
    names = [c["category"] for c in cats]
    values = [c["score"] for c in cats]
    h = max(150, 20 * len(cats) + 30)
    d = Drawing(460, h)
    bc = HorizontalBarChart()
    bc.x, bc.y, bc.height, bc.width = 110, 12, h - 24, 320
    bc.data = [values]
    bc.strokeColor = None
    bc.valueAxis.valueMin, bc.valueAxis.valueMax, bc.valueAxis.valueStep = 0, 100, 25
    bc.valueAxis.labels.fontSize = 7
    bc.valueAxis.labels.fillColor = MID
    bc.categoryAxis.categoryNames = names
    bc.categoryAxis.labels.fontSize = 8
    bc.categoryAxis.labels.fillColor = INK
    bc.categoryAxis.labels.boxAnchor = "e"
    bc.categoryAxis.strokeColor = LINE
    bc.valueAxis.strokeColor = LINE
    bc.barWidth = 8
    bc.groupSpacing = 6
    for i, v in enumerate(values):
        bc.bars[(0, i)].fillColor = _score_color(v)
    d.add(bc)
    return d


# ------------------------------- small helpers -------------------------------
def _badge(text: str, color) -> Table:
    t = Table([[text]], colWidths=[len(text) * 5.4 + 12])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROUNDEDCORNERS", [3, 3, 3, 3]),
    ]))
    return t


def _rule():
    t = Table([[""]], colWidths=[520])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.75, LINE)]))
    return t


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ------------------------------- page furniture -------------------------------
def _make_on_page(domain: str):
    def _on_page(canvas, doc):
        canvas.saveState()
        page = canvas.getPageNumber()
        if page > 1:  # no footer on the cover
            canvas.setStrokeColor(LINE)
            canvas.setLineWidth(0.5)
            canvas.line(40, 34, A4[0] - 40, 34)
            canvas.setFont("Helvetica", 7.5)
            canvas.setFillColor(MID)
            canvas.drawString(40, 24, f"AEOMirror · AI Visibility Report · {domain}")
            canvas.drawRightString(A4[0] - 40, 24, f"Page {page}")
        canvas.restoreState()
    return _on_page


# ------------------------------- builder -------------------------------
def build_pdf(report: dict) -> bytes:
    st = _styles()
    sc = report.get("scorecard", {})
    recs = report.get("recommendations", [])
    domain = report.get("domain") or report.get("url") or "your site"
    overall = sc.get("overall_score", 0)
    # Feature A: AI narrative (present only on paid reports). Used in place of the
    # generic template text where available; absent -> everything renders as before.
    ai = report.get("ai") or {}
    ai_by_id = {i["id"]: i for i in ai.get("issue_insights", []) if i.get("id")}

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=46, bottomMargin=46,
        title=f"AEOMirror AI Visibility Report — {domain}", author="AEOMirror",
    )
    story: list = []

    # ---------------- cover ----------------
    story += [
        Spacer(1, 60),
        Paragraph("◈ AEOMIRROR", st["cover_brand"]),
        Spacer(1, 10),
        Paragraph("AI Visibility Report", st["cover_title"]),
        Spacer(1, 6),
        Paragraph(_esc(domain), st["cover_domain"]),
        Spacer(1, 28),
    ]
    donut = _score_donut(overall)
    donut.hAlign = "CENTER"
    story += [donut, Spacer(1, 6)]
    story += [Paragraph(
        f"<font size=13><b>Grade {sc.get('grade', '-')}</b></font> &nbsp; "
        f"<font color='#5A6772'>Overall AI Visibility Score</font>",
        ParagraphStyle("cs", parent=st["body"], alignment=TA_CENTER))]
    story += [Spacer(1, 30), _rule(), Spacer(1, 8)]
    gen = (report.get("generated_at") or "")[:10]
    story += [Paragraph(
        f"Generated {gen} &nbsp;·&nbsp; Report v{report.get('report_version', '')} "
        f"&nbsp;·&nbsp; Scanner {report.get('scanner_version', '')}", st["small"])]
    if ai.get("executive_summary"):
        # Scores/evidence stay deterministic; only the prose narrative is AI-written.
        story += [Paragraph("Scores, categories and evidence are computed deterministically "
                            "from what AI engines (ChatGPT, Claude, Gemini, Perplexity) can "
                            "read, understand and cite. The written narrative is generated by "
                            "Claude from this scan's findings.", st["small"])]
    else:
        story += [Paragraph("Rule-based analysis of what AI engines (ChatGPT, Claude, "
                            "Gemini, Perplexity) can read, understand and cite. No AI/LLM "
                            "was used to generate this report.", st["small"])]
    story.append(PageBreak())

    # ---------------- executive summary ----------------
    story.append(Paragraph("Executive Summary", st["h1"]))
    story.append(Paragraph(_esc(ai.get("executive_summary") or sc.get("summary", "")), st["body"]))
    if ai.get("executive_summary"):
        story.append(Paragraph("<i>Narrative generated by Claude from this scan's findings.</i>",
                               st["small"]))
    story.append(Spacer(1, 10))
    counts = sc.get("issue_counts", {})
    chip_row = [[
        Paragraph(f"<b>{counts.get('Critical', 0)}</b><br/><font size=7 color='#5A6772'>Critical</font>", st["cell"]),
        Paragraph(f"<b>{counts.get('High', 0)}</b><br/><font size=7 color='#5A6772'>High</font>", st["cell"]),
        Paragraph(f"<b>{counts.get('Medium', 0)}</b><br/><font size=7 color='#5A6772'>Medium</font>", st["cell"]),
        Paragraph(f"<b>{counts.get('Low', 0)}</b><br/><font size=7 color='#5A6772'>Low</font>", st["cell"]),
        Paragraph(f"<b>{len(recs)}</b><br/><font size=7 color='#5A6772'>Total issues</font>", st["cell"]),
    ]]
    chips = Table(chip_row, colWidths=[96] * 5)
    chips.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#FBFCFD")),
    ]))
    story.append(chips)

    # ---------------- AI priority action plan (paid reports only) ----------------
    action_plan = ai.get("action_plan") or []
    if action_plan:
        story.append(Paragraph("Priority Action Plan", st["h2"]))
        for i, step in enumerate(action_plan, 1):
            story.append(Paragraph(f"<b>{i}.</b> {_esc(step)}", st["body"]))

    # ---------------- overall score + category chart ----------------
    story.append(Paragraph("Overall AI Visibility Score", st["h2"]))
    d2 = _score_donut(overall); d2.hAlign = "LEFT"
    grade_line = Paragraph(
        f"<font size=22><b>{overall}</b></font><font color='#5A6772'>/100</font> &nbsp; "
        f"Grade <b>{sc.get('grade', '-')}</b><br/>"
        f"<font color='#5A6772' size=9>{_esc(_status_label(sc.get('status')))}</font>",
        st["body"])
    row = Table([[d2, grade_line]], colWidths=[150, 360])
    row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.append(row)

    cats = sc.get("category_scores", [])
    if cats:
        story.append(Paragraph("Category Breakdown", st["h2"]))
        chart = _category_chart(cats); chart.hAlign = "CENTER"
        story.append(chart)
        story.append(Spacer(1, 6))
        story.append(_category_table(cats, st))

    # ---------------- strengths / weaknesses ----------------
    story.append(Paragraph("Strengths & Weaknesses", st["h2"]))
    story.append(_strength_weakness(sc, st))

    # ---------------- top priorities ----------------
    top = sc.get("top_priorities", [])
    if top:
        story.append(Paragraph("Top Priorities", st["h2"]))
        story.append(_priority_table(top, st))

    # ---------------- recommendations ----------------
    story.append(PageBreak())
    story.append(Paragraph("Recommendations", st["h1"]))
    story.append(Paragraph("What's wrong, why it matters, and exactly how to fix it — "
                           "ordered by priority.", st["small"]))
    story.append(Spacer(1, 6))
    if not recs:
        story.append(Paragraph("No issues found — this site is in excellent shape for AI visibility. 🎉", st["body"]))
    for i, r in enumerate(recs, 1):
        story.append(_recommendation_block(i, r, st, ai_by_id.get(r.get("id"))))

    # ---------------- appendix ----------------
    story.append(PageBreak())
    story.append(Paragraph("Appendix — Signal Detail", st["h1"]))
    story.append(_appendix_table(sc, st))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Report metadata", st["h3"]))
    meta = [
        ("Scan ID", report.get("scan_id")),
        ("URL", report.get("url")),
        ("Scanned at", report.get("scanned_at")),
        ("Report generated", report.get("generated_at")),
        ("Report version", report.get("report_version")),
        ("Scanner version", report.get("scanner_version")),
    ]
    mt = Table([[Paragraph(k, st["label"]), Paragraph(_esc(v), st["small"])] for k, v in meta],
               colWidths=[110, 400])
    mt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story.append(mt)

    on_page = _make_on_page(domain)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def _status_label(status: str) -> str:
    return {"pass": "In strong shape for AI visibility.",
            "warn": "Partially ready — clear gaps to close.",
            "fail": "At risk of being invisible to AI engines."}.get(status, "")


def _category_table(cats: list[dict], st) -> Table:
    rows = [[Paragraph("Category", st["label"]), Paragraph("Score", st["label"]),
             Paragraph("Status", st["label"])]]
    for c in cats:
        col = "#" + _score_color(c["score"]).hexval()[2:]
        rows.append([
            Paragraph(_esc(c["category"]), st["cell"]),
            Paragraph(f"<font color='{col}'><b>{c['score']}</b></font>", st["cell"]),
            Paragraph(_esc(c["status"].upper()), st["cell"]),
        ])
    t = Table(rows, colWidths=[260, 120, 130])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _strength_weakness(sc: dict, st) -> Table:
    def _list(items, empty):
        if not items:
            return Paragraph(f"<font color='#5A6772'>{empty}</font>", st["cell"])
        bits = "".join(f"• {_esc(x['label'])} <font color='#5A6772'>({x['score']})</font><br/>"
                       for x in items[:6])
        return Paragraph(bits, st["cell"])
    header = [Paragraph("<b>Strengths</b>", st["cellb"]), Paragraph("<b>Weaknesses</b>", st["cellb"])]
    body = [_list(sc.get("strengths"), "None scored 75+ yet."),
            _list(sc.get("weaknesses"), "No critical weaknesses. 🎉")]
    t = Table([header, body], colWidths=[255, 255])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _priority_table(top: list[dict], st) -> Table:
    rows = [[Paragraph(h, st["label"]) for h in ("#", "Priority", "Category", "Issue", "Fix time", "Difficulty")]]
    for i, r in enumerate(top, 1):
        rows.append([
            Paragraph(str(i), st["cell"]),
            _badge(r["priority"], PRI_COLORS.get(r["priority"], MID)),
            Paragraph(_esc(r["category"]), st["cell"]),
            Paragraph(_esc(r["issue_title"]), st["cell"]),
            Paragraph(_esc(r["estimated_fix_time"]), st["cell"]),
            Paragraph(_esc(r["difficulty"]), st["cell"]),
        ])
    t = Table(rows, colWidths=[18, 58, 90, 180, 92, 72])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _recommendation_block(i: int, r: dict, st, ins: dict | None = None) -> KeepTogether:
    fx = r.get("fix_template", {})
    ins = ins or {}
    ai_why = []
    if ins.get("why_it_matters"):
        ai_why = [
            Paragraph("<font size=7 color='#5A6772'>WHY IT MATTERS (AI ANALYSIS)</font>", st["label"]),
            Paragraph(_esc(ins["why_it_matters"]), st["body"]),
        ]
        if ins.get("priority_rationale"):
            ai_why.append(Paragraph(f"<i>{_esc(ins['priority_rationale'])}</i>", st["small"]))
        ai_why.append(Spacer(1, 4))
    parts = [
        Spacer(1, 10),
        Table([[_badge(r["priority"], PRI_COLORS.get(r["priority"], MID)),
                Paragraph(f"<b>{i}. {_esc(r['issue_title'])}</b> "
                          f"<font size=8 color='#5A6772'>· {_esc(r['category'])} · "
                          f"score {r['score']} · {_esc(r['severity'])} severity</font>", st["h3"])]],
              colWidths=[62, 458],
              style=TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")])),
        Spacer(1, 3),
        Paragraph(_esc(r.get("description", "")), st["body"]),
        Spacer(1, 4),
        *ai_why,
        Table([[Paragraph("<font size=7 color='#5A6772'>BUSINESS IMPACT</font><br/>"
                          + _esc(r.get("business_impact")), st["cell"]),
                Paragraph("<font size=7 color='#5A6772'>AI VISIBILITY IMPACT</font><br/>"
                          + _esc(r.get("ai_visibility_impact")), st["cell"])]],
              colWidths=[255, 255],
              style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("BACKGROUND", (0, 0), (-1, -1), HexColor("#FBFCFD")),
                                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                                ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                                ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 7)])),
        Spacer(1, 3),
        Paragraph(f"<font size=7 color='#5A6772'>EST. FIX TIME</font> {_esc(r.get('estimated_fix_time'))} "
                  f"&nbsp;&nbsp; <font size=7 color='#5A6772'>DIFFICULTY</font> {_esc(r.get('difficulty'))}",
                  st["small"]),
        Spacer(1, 4),
        Paragraph("<b>Problem</b>", st["h3"]),
        Paragraph(_esc(fx.get("problem")), st["body"]),
        Paragraph("<b>Explanation</b>", st["h3"]),
        Paragraph(_esc(fx.get("explanation")), st["body"]),
        Paragraph("<b>Recommended fix</b>", st["h3"]),
    ]
    for step in (fx.get("recommended_fix") or []):
        parts.append(Paragraph(f"• {_esc(step)}", st["body"]))
    if fx.get("implementation_example"):
        parts.append(Paragraph("<b>Implementation example</b>", st["h3"]))
        example = _esc(fx["implementation_example"]).replace("\n", "<br/>").replace(" ", "&nbsp;")
        parts.append(Paragraph(example, st["code"]))
    parts.append(Paragraph("<b>Expected outcome</b>", st["h3"]))
    parts.append(Paragraph(_esc(fx.get("expected_outcome")), st["body"]))
    parts.append(Spacer(1, 4))
    parts.append(_rule())
    return KeepTogether(parts)


def _appendix_table(sc: dict, st) -> Table:
    cats = sc.get("category_scores", [])
    rows = [[Paragraph(h, st["label"]) for h in ("Category", "Score", "Status", "Weight")]]
    for c in cats:
        rows.append([Paragraph(_esc(c["category"]), st["cell"]),
                     Paragraph(str(c["score"]), st["cell"]),
                     Paragraph(_esc(c["status"].upper()), st["cell"]),
                     Paragraph(str(c.get("weight", "")), st["cell"])])
    t = Table(rows, colWidths=[230, 90, 110, 80])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t
