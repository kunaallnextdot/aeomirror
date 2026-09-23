/* DiagnosisCard — the shared Problem/Evidence/Why/Fix/Implementation/Verify shell
   used by ReportView's Technical SEO/Schema/Entity/Content Intelligence/Crawl Graph
   sections (Phase C). Pure presentation: these tests only confirm each step renders
   (or is honestly omitted) from the props it's given — never that it computes/invents
   anything, since it has no data logic of its own. */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import DiagnosisCard, { SupportingEvidenceNote } from "./DiagnosisCard.jsx";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function renderCard(props) {
  return render(<MemoryRouter><DiagnosisCard {...props} /></MemoryRouter>);
}

describe("DiagnosisCard — each step is independently optional, never forced", () => {
  it("renders only title when no other step is given (never fabricates content)", () => {
    renderCard({ title: "Something is wrong" });
    expect(screen.getByText("Something is wrong")).toBeTruthy();
    expect(screen.queryByText("Evidence")).toBeNull();
    expect(screen.queryByText("Why it matters")).toBeNull();
    expect(screen.queryByText("How to fix")).toBeNull();
    expect(screen.queryByText("Implementation")).toBeNull();
    expect(screen.queryByText(/Verify after re-scan/)).toBeNull();
  });

  it("renders a severity badge distinct from the title text", () => {
    renderCard({ title: "Pages are blocked from indexing", severity: "High" });
    expect(screen.getByText("High")).toBeTruthy();
    expect(screen.getByText("Pages are blocked from indexing")).toBeTruthy();
  });

  it("a custom badge label overrides the plain severity text", () => {
    renderCard({ title: "x", severity: "High", badge: "Critical issue" });
    expect(screen.getByText("Critical issue")).toBeTruthy();
    expect(screen.queryByText("High")).toBeNull();
  });

  it("problem, whyItMatters, fix, and implementation each render only when passed", () => {
    renderCard({
      title: "x", problem: "The real problem.", whyItMatters: "The real why.",
      fix: "The real fix.", implementation: "<script>real example</script>",
    });
    expect(screen.getByText("The real problem.")).toBeTruthy();
    expect(screen.getByText("Why it matters")).toBeTruthy();
    expect(screen.getByText("The real why.")).toBeTruthy();
    expect(screen.getByText("How to fix")).toBeTruthy();
    expect(screen.getByText("The real fix.")).toBeTruthy();
    expect(screen.getByText("Implementation")).toBeTruthy();
    expect(screen.getByText("<script>real example</script>")).toBeTruthy();
  });

  it("evidence previews the first 3 real lines, with a 'Show N more' expand for the rest — never truncated silently", () => {
    renderCard({ title: "x", evidence: ["one", "two", "three", "four", "five"] });
    expect(screen.getByText("one")).toBeTruthy();
    expect(screen.getByText("two")).toBeTruthy();
    expect(screen.getByText("three")).toBeTruthy();
    expect(screen.queryByText("four")).toBeNull();
    expect(screen.queryByText("five")).toBeNull();
    fireEvent.click(screen.getByText("Show 2 more"));
    expect(screen.getByText("four")).toBeTruthy();
    expect(screen.getByText("five")).toBeTruthy();
  });

  it("evidence of 3 or fewer lines never shows a 'Show more' toggle", () => {
    renderCard({ title: "x", evidence: ["one", "two"] });
    expect(screen.queryByText(/Show .* more/)).toBeNull();
  });

  it("affectedPages is always fully visible, never subject to the Evidence preview/expand limit", () => {
    renderCard({
      title: "x", evidence: ["a", "b", "c"],
      affectedPages: <div><span>page-1</span><span>page-2</span><span>page-3</span><span>page-4</span></div>,
    });
    // even though evidence already has 3 items (no "show more" needed there), all 4
    // affectedPages entries are visible regardless — a separate, always-visible slot.
    expect(screen.getByText("page-1")).toBeTruthy();
    expect(screen.getByText("page-4")).toBeTruthy();
  });

  it("verifyHref renders 'Verify after re-scan' linking there; omitting it renders no Verify affordance at all", () => {
    const { unmount } = renderCard({ title: "x", verifyHref: "/app/scans/s1" });
    const link = screen.getByText(/Verify after re-scan/).closest("a");
    expect(link.getAttribute("href")).toBe("/app/scans/s1");
    unmount();

    renderCard({ title: "y" });
    expect(screen.queryByText(/Verify after re-scan/)).toBeNull();
  });

  it("children render after the standard steps (e.g. a section-specific expand)", () => {
    renderCard({ title: "x", children: <div>extra section content</div> });
    expect(screen.getByText("extra section content")).toBeTruthy();
  });
});

describe("SupportingEvidenceNote — never a duplicate action, only a jump-link to the real recommendation", () => {
  it("renders nothing when there are no real items (never an empty fabricated note)", () => {
    const { container } = render(<SupportingEvidenceNote recommendationTitle="x" items={[]} onJump={() => {}} />);
    expect(container.textContent).toBe("");
  });

  it("renders the real items and calls onJump when the recommendation link is clicked", () => {
    const onJump = vi.fn();
    render(<SupportingEvidenceNote recommendationTitle="Add structured data" items={["Missing FAQPage schema"]} onJump={onJump} />);
    expect(screen.getByText("Missing FAQPage schema")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /"Add structured data" recommendation/ }));
    expect(onJump).toHaveBeenCalledTimes(1);
  });
});

describe("Phase C mobile-safety (static — jsdom does not apply real CSS; verifies the shipped rules directly)", () => {
  const REPORT_CSS = readFileSync(path.join(__dirname, "ReportView.aurora.css"), "utf8");
  const block = (selector) => REPORT_CSS.match(new RegExp(selector.replace(/[.]/g, "\\.") + "\\{[^}]*\\}"))?.[0] || "";

  it("URLs wrap instead of causing horizontal overflow (.au-tseo-url)", () => {
    expect(block(".au-tseo-url")).toMatch(/word-break\s*:\s*break-all/);
  });

  it("the badge+title row wraps to one column on narrow viewports (.au-tseo-row)", () => {
    expect(block(".au-tseo-row")).toMatch(/flex-wrap\s*:\s*wrap/);
  });

  it("the new scan-coverage block has no fixed/min width that could overflow on mobile", () => {
    const b = block(".au-tseo-coverage");
    expect(b).not.toMatch(/width\s*:\s*\d/);
    expect(b).not.toMatch(/min-width/);
  });
});
