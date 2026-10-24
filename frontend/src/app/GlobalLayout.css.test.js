/* Global footer/layout fix — static assertions against the three actual shipped app
   shells (.sp-dash: authenticated app, .sp-site: public marketing/contact, .sp-pub:
   public shared report). jsdom does not apply imported CSS in this project's test setup
   (no `test.css` config), so "not fixed/sticky" and "content can grow before the footer"
   are facts only the CSS source itself can state — read and asserted directly here
   rather than as brittle pixel/computed-style checks. */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(__dirname, rel), "utf8");

const APP_LAYOUT_CSS = read("AppLayout.spectrum.css");
const APP_CSS = read("../App.spectrum.css");
const SHARED_SPECTRUM_CSS = read("../shared/spectrum.css");

function block(css, selector) {
  const re = new RegExp(selector.replace(/[.]/g, "\\.") + "\\{[^}]*\\}");
  return css.match(re)?.[0] || "";
}

describe.each([
  {
    name: "authenticated app shell (.sp-dash)",
    css: APP_LAYOUT_CSS,
    shell: ".sp-dash", column: ".sp-dash-main", content: ".sp-dash-content", footer: ".sp-dash-footer",
  },
  {
    name: "public marketing/contact shell (.sp-site)",
    css: APP_CSS,
    shell: ".sp-site", column: ".sp-site", content: ".sp-site-content", footer: ".sp-site-footer",
  },
  {
    name: "public shared-report shell (.sp-pub)",
    css: SHARED_SPECTRUM_CSS,
    shell: ".sp-pub", column: ".sp-pub", content: ".sp-pub-main", footer: ".sp-pub-foot",
  },
])("$name", ({ css, shell, column, content, footer }) => {
  it("the shell fills at least the viewport height", () => {
    expect(block(css, shell)).toMatch(/min-height\s*:\s*100vh/);
  });

  it("the column is a vertical flex container (footer follows content in normal flow)", () => {
    const b = block(css, column);
    expect(b).toMatch(/display\s*:\s*flex/);
    expect(b).toMatch(/flex-direction\s*:\s*column/);
  });

  it("the content area can grow to push a short footer to the bottom", () => {
    expect(block(css, content)).toMatch(/flex\s*:\s*1/);
  });

  it("the footer is never position:fixed or position:sticky", () => {
    const b = block(css, footer);
    expect(b).toBeTruthy();
    expect(b).not.toMatch(/position\s*:\s*fixed/);
    expect(b).not.toMatch(/position\s*:\s*sticky/);
  });
});
