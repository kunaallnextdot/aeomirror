/* CHANGE 2 — identical provider samples collapse to one block labelled "N identical
   responses"; differing samples render both and are labelled "Response A/B"; the raw
   sample index never appears. CHANGE 3 — the empty-recommendation line is replaced in
   priority order (gap > informational > fallback). */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { groupByProvider, ProviderGroup } from "./AnswerTracking.jsx";

const sample = (over) => ({
  provider: "anthropic", run_index: 0, brand_mentioned: false, extraction_failed: false,
  recommended_entities: [], brand_urls_cited: [], search_enabled: true, ...over,
});

describe("groupByProvider identical detection (CHANGE 2)", () => {
  it("marks two agreeing samples (same verdict + recommendations) identical", () => {
    const [g] = groupByProvider([
      sample({ brand_mentioned: true, recommended_entities: [{ name: "Acme" }] }),
      sample({ brand_mentioned: true, recommended_entities: [{ name: "Acme" }] }),
    ]);
    expect(g.identical).toBe(true);
    expect(g.total).toBe(2);
  });

  it("marks samples that DISAGREE on the verdict as not identical", () => {
    const [g] = groupByProvider([
      sample({ brand_mentioned: true }),
      sample({ brand_mentioned: false }),
    ]);
    expect(g.identical).toBe(false);
  });

  it("marks samples that agree on verdict but differ on recommendations as not identical", () => {
    const [g] = groupByProvider([
      sample({ brand_mentioned: false, recommended_entities: [{ name: "Rival" }] }),
      sample({ brand_mentioned: false, recommended_entities: [{ name: "Other" }] }),
    ]);
    expect(g.identical).toBe(false);
  });
});

describe("ProviderGroup rendering (CHANGE 2)", () => {
  it("labels identical responses and never shows a sample index", () => {
    const [g] = groupByProvider([
      sample({ brand_mentioned: true, recommended_entities: [{ name: "Acme" }] }),
      sample({ brand_mentioned: true, recommended_entities: [{ name: "Acme" }] }),
    ]);
    const { container } = render(<ProviderGroup g={g} ctx={{}} />);
    expect(screen.getByText(/2 identical responses/)).toBeTruthy();
    fireEvent.click(screen.getByText(/View full response/));
    // no "sample 0" / "sample 1" anywhere
    expect(container.textContent).not.toMatch(/sample \d/i);
  });

  it("labels differing responses and shows Response A / Response B", () => {
    const [g] = groupByProvider([
      sample({ brand_mentioned: true }),
      sample({ brand_mentioned: false }),
    ]);
    render(<ProviderGroup g={g} ctx={{}} />);
    expect(screen.getByText(/responses differed/)).toBeTruthy();
    fireEvent.click(screen.getByText(/View full responses/));
    expect(screen.getByText(/Response A/)).toBeTruthy();
    expect(screen.getByText(/Response B/)).toBeTruthy();
  });
});

describe("empty-recommendation replacement (CHANGE 3)", () => {
  const emptyGroup = () => groupByProvider([
    sample({ brand_mentioned: false, recommended_entities: [] }),
    sample({ brand_mentioned: false, recommended_entities: [] }),
  ])[0];

  it("points to the gap-to-action when a gap exists (priority a)", () => {
    render(<ProviderGroup g={emptyGroup()} ctx={{ hasGap: true }} />);
    expect(screen.getByText(/Why not you/)).toBeTruthy();
    expect(screen.queryByText(/No specific brands were recommended/)).toBeNull();
  });

  it("explains an informational prompt when nothing was recommended anywhere (priority b)", () => {
    render(<ProviderGroup g={emptyGroup()} ctx={{ informational: true }} />);
    expect(screen.getByText(/informational question/)).toBeTruthy();
  });

  it("falls back to the plain line only when neither applies (priority c)", () => {
    render(<ProviderGroup g={emptyGroup()} ctx={{}} />);
    expect(screen.getByText(/No specific brands were recommended/)).toBeTruthy();
  });
});
