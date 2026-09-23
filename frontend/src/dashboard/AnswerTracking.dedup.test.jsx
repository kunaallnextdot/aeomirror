/* Decision 2 regression guard: the Answer Tracking page must stay the operational
   workspace for individual prompt runs/results and must not repeat the aggregate
   leaderboard / competitor share-of-voice / provider-breakdown / gap-to-action content
   that now lives exclusively in AI Visibility (AIVisibility.jsx).

   `ResultsPanel` fetches its own data via polling effects and isn't split into a pure,
   data-driven view component the way AIVisibilityView is (this codebase's established
   pattern for component tests — see AIVisibility.test.jsx / ReportView.readonly.test.jsx
   — is to render a pure view with a data prop, not a page that fetches for itself).
   Building a full fetch/auth/router harness just to re-render this page would be a much
   larger addition than the actual product fix, so this guards the same regression at the
   source level: the removed duplicate sections must not come back, and AIVisibilityPanel
   must still be the one component rendering that aggregate/competitive intelligence. */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";

const SRC = readFileSync(path.join(__dirname, "AnswerTracking.jsx"), "utf8");

describe("AnswerTracking does not duplicate AI Visibility content", () => {
  it("still mounts AIVisibilityPanel as the aggregate/competitive intelligence layer", () => {
    expect(SRC).toMatch(/import\s*\{\s*AIVisibilityPanel\s*\}\s*from\s*"\.\/AIVisibility\.jsx"/);
    expect(SRC).toMatch(/<AIVisibilityPanel\s+runId={targetId}\s*\/>/);
  });

  it("no longer renders the aggregate provider-breakdown / share-of-voice / leaderboard blocks", () => {
    expect(SRC).not.toMatch(/Share of voice/);
    expect(SRC).not.toMatch(/By provider<\/div>/);
    // The `Leaderboard` component itself is kept (exported + still unit-tested by
    // Leaderboard.test.jsx) — only its invocation inside ResultsPanel is removed.
    expect(SRC).not.toMatch(/<Leaderboard\s/);
  });

  it("no longer renders the grounded gap-to-action text inline (now only in AI Visibility)", () => {
    expect(SRC).not.toMatch(/<GapToAction/);
    expect(SRC).not.toMatch(/function GapToAction/);
  });

  it("keeps the operational per-prompt table (mention rate / whether the brand appeared)", () => {
    expect(SRC).toMatch(/By prompt/);
    expect(SRC).toMatch(/PromptResultRow/);
  });

  it("gates the cited-URL list instead of silently dropping the Pro upsell", () => {
    expect(SRC).toMatch(/locked_cited_url_count/);
    expect(SRC).toMatch(/unlock with Pro/);
  });
});
