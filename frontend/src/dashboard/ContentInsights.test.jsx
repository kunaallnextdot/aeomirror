/* Phase F: Structure gets its own fix-first block (What to fix + Implementation) —
   real model output from the single existing AI Content Insights call, never a
   second LLM call, never fabricated when the model has nothing to fix. */
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { InsightsBody } from "./ContentInsights.jsx";

const BASE = {
  tone: { assessment: "Professional.", score_0_100: 72 },
  clarity: { assessment: "Mostly clear.", score_0_100: 65 },
  structure: { assessment: "Long service list with no introduction.", score_0_100: 40 },
  suggestions: ["Shorten the intro paragraph"],
  rewrite_example: { before: "x", after: "y" },
};

describe("InsightsBody — Structure fix-first block", () => {
  it("renders Structure's real fixes as a numbered list plus an implementation skeleton", () => {
    render(<InsightsBody data={{
      ...BASE,
      structure: {
        ...BASE.structure,
        fixes: ["Add a 2-3 sentence introduction before the service list",
                "Group related services under H2 headings"],
        implementation: "H1: Digital Marketing Services\nIntro: ...\nH2: Our Services",
      },
    }} />);

    expect(screen.getByText("Structure — what to fix")).toBeTruthy();
    expect(screen.getByText("Add a 2-3 sentence introduction before the service list")).toBeTruthy();
    expect(screen.getByText("Group related services under H2 headings")).toBeTruthy();
    expect(screen.getByText(/H1: Digital Marketing Services/)).toBeTruthy();
  });

  it("never fabricates a fix list when the model didn't return one", () => {
    render(<InsightsBody data={BASE} />);
    expect(screen.queryByText("Structure — what to fix")).toBeNull();
  });

  it("shows fixes without an implementation skeleton when the model omitted one", () => {
    render(<InsightsBody data={{
      ...BASE,
      structure: { ...BASE.structure, fixes: ["Add descriptive H2/H3 headings"], implementation: "" },
    }} />);
    expect(screen.getByText("Add descriptive H2/H3 headings")).toBeTruthy();
    expect(screen.queryByText("Implementation")).toBeNull();
  });

  it("the fix-first block renders before the generic Suggestions list", () => {
    render(<InsightsBody data={{
      ...BASE,
      structure: { ...BASE.structure, fixes: ["Add a clear H1"], implementation: "" },
    }} />);
    const fixBlock = document.querySelector(".ci-structure-fix");
    const suggestionsHeading = screen.getByText("Suggestions");
    expect(fixBlock.compareDocumentPosition(suggestionsHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
