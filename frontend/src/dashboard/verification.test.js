/* Pure deterministic comparison engine — the ONE engine shared by Compare and Fix
   Verification (see verification.js docstring). No React, no network — direct
   function tests covering the ticket's BASIC/VERIFICATION/EVIDENCE test matrix. */
import { describe, it, expect } from "vitest";
import { diffSignals, verificationStatus } from "./verification.js";

const scan = (id, sections, status = "completed") => ({ scan_id: id, status, sections });
const section = (over) => ({ id: "schema", label: "Schema", score: 40, status: "fail",
  issues: [], evidence: {}, ...over });

describe("verification.js — diffSignals", () => {
  it("matches signals by id, not label or position", () => {
    const before = scan("s1", [section({ id: "schema", label: "Old label", score: 40 })]);
    const after = scan("s2", [section({ id: "schema", label: "New label", score: 80 })]);
    const [d] = diffSignals(before, after);
    expect(d.signal_id).toBe("schema");
    expect(d.score_before).toBe(40);
    expect(d.score_after).toBe(80);
  });

  it("marks a signal not comparable when absent from the before scan", () => {
    const before = scan("s1", []);
    const after = scan("s2", [section({ id: "schema" })]);
    const [d] = diffSignals(before, after);
    expect(d.comparable).toBe(false);
  });
});

describe("verification.js — verificationStatus (status transitions)", () => {
  const st = (b, a, resolved = 0, remaining = 0, added = 0) =>
    verificationStatus({ status_before: b, status_after: a, resolvedCount: resolved, remainingCount: remaining, newCount: added });

  it("FAIL -> PASS with nothing remaining = verified", () => {
    expect(st("fail", "pass", 1, 0, 0)).toBe("verified");
  });
  it("WARN -> PASS with nothing remaining = verified", () => {
    expect(st("warn", "pass", 1, 0, 0)).toBe("verified");
  });
  it("FAIL -> WARN = partially_improved (never verified from a mid-tier status)", () => {
    expect(st("fail", "warn", 1, 1, 0)).toBe("partially_improved");
  });
  it("WARN -> WARN with issue improvement = partially_improved", () => {
    expect(st("warn", "warn", 1, 1, 0)).toBe("partially_improved");
  });
  it("FAIL -> FAIL with the same issue = unchanged", () => {
    expect(st("fail", "fail", 0, 1, 0)).toBe("unchanged");
  });
  it("WARN -> FAIL = regressed", () => {
    expect(st("warn", "fail", 0, 1, 0)).toBe("regressed");
  });
  it("PASS -> FAIL = regressed", () => {
    expect(st("pass", "fail", 0, 1, 0)).toBe("regressed");
  });
  it("missing status on either side = not_comparable", () => {
    expect(st(undefined, "pass", 0, 0, 0)).toBe("not_comparable");
    expect(st("fail", undefined, 0, 0, 0)).toBe("not_comparable");
  });
  it("a score/status improvement to PASS with an issue still remaining is NOT verified", () => {
    // guards against "score increase without issue resolution = verified"
    expect(st("fail", "pass", 1, 1, 0)).toBe("partially_improved");
  });
  it("a same-tier improvement that clears every issue but never reaches PASS stays partially_improved, never verified", () => {
    expect(st("warn", "warn", 2, 0, 0)).toBe("partially_improved");
  });
  it("regression outranks a partially resolved issue — the overall verdict is honest about getting worse", () => {
    expect(st("warn", "fail", 1, 1, 0)).toBe("regressed");
  });
});
