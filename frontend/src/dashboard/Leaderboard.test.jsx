/* "Who's winning your category" leaderboard panel: ranked rows, a visually-distinct always-
   present brand row, a head-to-head column, click-to-filter, and an empty state that names the
   excluded count. */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { Leaderboard } from "./AnswerTracking.jsx";

const SUMMARY = {
  leaderboard_min_appearances: 2,
  leaderboard_excluded: 1,
  leaderboard: [
    { rank: 1, name: "Profound", appearances: 4, prompt_coverage: 3, appearance_rate: 80.0,
      average_position: 1.5, head_to_head: 2, is_you: false, rank_delta: 1,
      prompt_ids: ["p1", "p2", "p3"] },
    { rank: 2, name: "Acme", appearances: 3, prompt_coverage: 2, appearance_rate: 60.0,
      average_position: 2.0, head_to_head: 0, is_you: true, rank_delta: -1,
      prompt_ids: ["p1", "p2"] },
  ],
};

describe("Leaderboard", () => {
  it("renders ranked rows including the tracked brand marked as 'you'", () => {
    render(<Leaderboard summary={SUMMARY} filterEntity={null} onSelect={() => {}} />);
    expect(screen.getByText("Profound")).toBeTruthy();
    expect(screen.getByText("Acme")).toBeTruthy();
    expect(screen.getByText("you")).toBeTruthy();            // brand row is distinguished
    expect(screen.getByText("80%")).toBeTruthy();            // appearance rate rendered
    expect(screen.getByText("60%")).toBeTruthy();
  });

  it("shows head-to-head: a competitor's count, and '—' for the brand's own row", () => {
    render(<Leaderboard summary={SUMMARY} filterEntity={null} onSelect={() => {}} />);
    expect(screen.getByText("—")).toBeTruthy();              // brand's own head-to-head is 0
  });

  it("calls onSelect with the entity when a row is clicked (drives the prompt filter)", () => {
    const onSelect = vi.fn();
    render(<Leaderboard summary={SUMMARY} filterEntity={null} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("Profound"));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].name).toBe("Profound");
    expect(onSelect.mock.calls[0][0].prompt_ids).toEqual(["p1", "p2", "p3"]);
  });

  it("renders an empty state naming the excluded count when nothing clears the threshold", () => {
    const empty = { leaderboard: [], leaderboard_excluded: 3, leaderboard_min_appearances: 2 };
    render(<Leaderboard summary={empty} filterEntity={null} onSelect={() => {}} />);
    expect(screen.getByText(/at least 2 samples/i)).toBeTruthy();
    expect(screen.getByText(/3 entities appeared too rarely/i)).toBeTruthy();
  });
});
