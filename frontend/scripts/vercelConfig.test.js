/* Regression guard for a real production incident: a prior change added
   "cleanUrls": true to serve /privacy and /terms as static files, which
   silently broke the SPA catch-all rewrite for every other route (/login,
   /register, /app/*, /r/*, /contact all returned a live Vercel 404 on direct
   navigation or refresh). The fix removed cleanUrls and instead added two
   explicit, Vercel-documented rewrites (source -> a static .html file) ahead
   of the catch-all. This test asserts that shape stays intact: no cleanUrls,
   and the catch-all SPA rewrite is present and ordered last. */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const vercelConfig = JSON.parse(
  readFileSync(path.join(__dirname, "..", "vercel.json"), "utf8"),
);

describe("vercel.json", () => {
  it("does not set cleanUrls (it disables the SPA catch-all rewrite for any route without a literal matching file)", () => {
    expect(vercelConfig.cleanUrls).not.toBe(true);
  });

  it("rewrites /privacy and /terms to their prerendered static files, each ahead of the SPA catch-all", () => {
    const sources = vercelConfig.rewrites.map((r) => r.source);
    const catchAllIndex = sources.indexOf("/(.*)");
    expect(catchAllIndex).toBeGreaterThan(-1);

    const privacyIndex = sources.indexOf("/privacy");
    const termsIndex = sources.indexOf("/terms");
    expect(privacyIndex).toBeGreaterThan(-1);
    expect(termsIndex).toBeGreaterThan(-1);
    expect(privacyIndex).toBeLessThan(catchAllIndex);
    expect(termsIndex).toBeLessThan(catchAllIndex);

    expect(vercelConfig.rewrites[privacyIndex].destination).toBe("/privacy.html");
    expect(vercelConfig.rewrites[termsIndex].destination).toBe("/terms.html");
  });

  it("keeps the SPA catch-all rewrite as the last rule, so every other route (including /contact) still falls through to index.html", () => {
    const last = vercelConfig.rewrites[vercelConfig.rewrites.length - 1];
    expect(last).toEqual({ source: "/(.*)", destination: "/index.html" });
  });
});
