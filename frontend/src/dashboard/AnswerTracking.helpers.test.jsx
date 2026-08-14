import { describe, it, expect } from "vitest";
import { groupByProvider, gapCountLabel } from "./AnswerTracking";

/* FIX 1 — samples must be GROUPED by provider (one block per provider per prompt), with a
   fraction verdict when samples disagree, deduped recommendations/citations, and named-
   providers ordered first. */
describe("groupByProvider (FIX 1)", () => {
  it("collapses N samples of the same provider into ONE group", () => {
    const groups = groupByProvider([
      { provider: "anthropic", run_index: 0, brand_mentioned: true },
      { provider: "anthropic", run_index: 1, brand_mentioned: true },
      { provider: "openai", run_index: 0, brand_mentioned: false },
    ]);
    expect(groups.map((g) => g.provider)).toEqual(["anthropic", "openai"]);
    expect(groups[0].total).toBe(2);
    expect(groups[1].total).toBe(1);
  });

  it("reports the fraction when samples DISAGREE (no lossy yes/no)", () => {
    const [g] = groupByProvider([
      { provider: "anthropic", brand_mentioned: true },
      { provider: "anthropic", brand_mentioned: false },
      { provider: "anthropic", brand_mentioned: true },
    ]);
    expect(g.namedCount).toBe(2);
    expect(g.total).toBe(3);
    expect(g.anyNamed).toBe(true);
  });

  it("reports 'not named in any' when no sample named the brand", () => {
    const [g] = groupByProvider([
      { provider: "openai", brand_mentioned: false },
      { provider: "openai", brand_mentioned: false },
    ]);
    expect(g.namedCount).toBe(0);
    expect(g.anyNamed).toBe(false);
  });

  it("takes the mention sentence from a sample where the brand WAS named", () => {
    const [g] = groupByProvider([
      { provider: "anthropic", brand_mentioned: false, mention_context: null },
      { provider: "anthropic", brand_mentioned: true, mention_context: "Acme is great." },
    ]);
    expect(g.sentence).toBe("Acme is great.");
  });

  it("dedupes recommended entities, preserving order from the highest-ranked sample", () => {
    const [g] = groupByProvider([
      { provider: "openai", brand_mentioned: true, position: 3,
        recommended_entities: [{ name: "Zeta" }, { name: "Beta" }] },
      { provider: "openai", brand_mentioned: true, position: 1,
        recommended_entities: [{ name: "Acme" }, { name: "Beta" }] },
    ]);
    // The position=1 sample is the reference; its order leads, then new names appended.
    expect(g.recommended.map((e) => e.name)).toEqual(["Acme", "Beta", "Zeta"]);
  });

  it("falls back to the first sample's order when the brand was never named", () => {
    const [g] = groupByProvider([
      { provider: "openai", brand_mentioned: false,
        recommended_entities: [{ name: "Beta" }, { name: "Zeta" }] },
      { provider: "openai", brand_mentioned: false,
        recommended_entities: [{ name: "Acme" }] },
    ]);
    expect(g.recommended.map((e) => e.name)).toEqual(["Beta", "Zeta", "Acme"]);
  });

  it("dedupes citations across samples, first-seen order", () => {
    const [g] = groupByProvider([
      { provider: "anthropic", brand_mentioned: true, brand_urls_cited: ["https://a.com", "https://b.com"] },
      { provider: "anthropic", brand_mentioned: true, brand_urls_cited: ["https://b.com", "https://c.com"] },
    ]);
    expect(g.citations).toEqual(["https://a.com", "https://b.com", "https://c.com"]);
  });

  it("orders providers where the brand was named BEFORE not-named ones", () => {
    const groups = groupByProvider([
      { provider: "openai", brand_mentioned: false },
      { provider: "anthropic", brand_mentioned: true },
    ]);
    expect(groups.map((g) => g.provider)).toEqual(["anthropic", "openai"]);
  });

  it("flags when a provider's samples ran without web search", () => {
    const [g] = groupByProvider([
      { provider: "openai", brand_mentioned: false, search_enabled: false },
    ]);
    expect(g.searchOff).toBe(true);
  });
});

/* FIX 3 — the "By prompt" header must show a COUNT, and read correctly at zero. */
describe("gapCountLabel (FIX 3)", () => {
  it("counts prompts that never mention you", () => {
    expect(gapCountLabel([{ is_gap: true }, { is_gap: false }, { is_gap: true }, { is_gap: false }]))
      .toBe("2 of 4 prompts never mention you");
  });

  it("reads correctly when there are zero gaps (not '0% are your gaps')", () => {
    const label = gapCountLabel([{ is_gap: false }, { is_gap: false }]);
    expect(label).toBe("you're mentioned in all 2 prompts");
    expect(label).not.toMatch(/0%/);
  });

  it("singularizes for a single prompt", () => {
    expect(gapCountLabel([{ is_gap: true }])).toBe("1 of 1 prompt never mention you");
  });

  it("handles an empty set without dividing by zero", () => {
    expect(gapCountLabel([])).toBe("no prompts yet");
  });
});
