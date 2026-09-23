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

describe("citation provenance (Phase H) — never label an LLM-extracted URL as provider-reported", () => {
  it("a native provider citation renders labeled 'Provider citation'", () => {
    const [g] = groupByProvider([
      sample({ brand_mentioned: true, citation_source: "provider", brand_urls_cited: ["https://acme.example/pricing"] }),
    ]);
    render(<ProviderGroup g={g} ctx={{}} />);
    expect(screen.getByText("https://acme.example/pricing")).toBeTruthy();
    expect(screen.getByText("Provider citation")).toBeTruthy();
    expect(screen.queryByText("Detected from answer")).toBeNull();
  });

  it("an LLM-extracted URL renders labeled 'Detected from answer', never as a provider citation", () => {
    const [g] = groupByProvider([
      sample({ brand_mentioned: true, citation_source: "llm_extracted", brand_urls_cited: ["https://acme.example/about"] }),
    ]);
    render(<ProviderGroup g={g} ctx={{}} />);
    expect(screen.getByText("https://acme.example/about")).toBeTruthy();
    expect(screen.getByText("Detected from answer")).toBeTruthy();
    expect(screen.queryByText("Provider citation")).toBeNull();
  });

  it("both provenances can coexist on the same page across different results", () => {
    const groups = groupByProvider([
      sample({ provider: "perplexity", brand_mentioned: true, citation_source: "provider", brand_urls_cited: ["https://acme.example/a"] }),
    ]).concat(groupByProvider([
      sample({ provider: "anthropic", brand_mentioned: true, citation_source: "llm_extracted", brand_urls_cited: ["https://acme.example/b"] }),
    ]));
    render(<>{groups.map((g) => <ProviderGroup key={g.provider} g={g} ctx={{}} />)}</>);
    expect(screen.getByText("Provider citation")).toBeTruthy();
    expect(screen.getByText("Detected from answer")).toBeTruthy();
  });

  it("an unlabeled/unknown citation_source defaults to the honest 'Detected from answer', never claims provider provenance", () => {
    const [g] = groupByProvider([
      sample({ brand_mentioned: true, brand_urls_cited: ["https://acme.example/x"] }),   // no citation_source at all
    ]);
    render(<ProviderGroup g={g} ctx={{}} />);
    expect(screen.getByText("Detected from answer")).toBeTruthy();
    expect(screen.queryByText("Provider citation")).toBeNull();
  });
});

describe("empty-recommendation replacement (CHANGE 3)", () => {
  const emptyGroup = () => groupByProvider([
    sample({ brand_mentioned: false, recommended_entities: [] }),
    sample({ brand_mentioned: false, recommended_entities: [] }),
  ])[0];

  it("points to AI Visibility's content gaps when a gap exists (priority a)", () => {
    render(<ProviderGroup g={emptyGroup()} ctx={{ hasGap: true }} />);
    expect(screen.getByText(/Content gaps/)).toBeTruthy();
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
