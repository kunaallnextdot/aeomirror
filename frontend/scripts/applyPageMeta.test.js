import { describe, it, expect } from "vitest";
import { applyPageMeta } from "./applyPageMeta.js";

const FIXTURE = `<!doctype html>
<html lang="en">
  <head>
    <title>AEOMirror — AI Visibility Scanner for ChatGPT, Claude, Gemini & Perplexity</title>
    <meta name="description" content="Homepage description." />
    <meta name="robots" content="index, follow, max-image-preview:large" />
    <link rel="canonical" href="https://aeomirror.com/" />
    <meta property="og:url" content="https://aeomirror.com/" />
    <meta property="og:title" content="AEOMirror — AI Visibility Scanner" />
    <meta property="og:description" content="Homepage OG description." />
    <meta name="twitter:title" content="AEOMirror — AI Visibility Scanner" />
    <meta name="twitter:description" content="Homepage twitter description." />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
`;

describe("applyPageMeta", () => {
  it("injects the rendered markup into the empty root div", () => {
    const out = applyPageMeta(FIXTURE, {
      markup: "<div><h1>Privacy Policy</h1></div>",
      routePath: "/privacy",
      title: "Privacy Policy",
      description: "desc",
    });
    expect(out).toContain('<div id="root"><div><h1>Privacy Policy</h1></div></div>');
    expect(out).not.toContain('<div id="root"></div>');
  });

  it("sets the correct canonical URL for the route, not the homepage", () => {
    const out = applyPageMeta(FIXTURE, {
      markup: "<p>x</p>",
      routePath: "/privacy",
      title: "Privacy Policy",
      description: "desc",
    });
    expect(out).toContain('<link rel="canonical" href="https://aeomirror.com/privacy" />');
    expect(out).not.toContain('href="https://aeomirror.com/"');
  });

  it("retargets title, description, og:*, and twitter:* tags to the route's own content", () => {
    const out = applyPageMeta(FIXTURE, {
      markup: "<p>x</p>",
      routePath: "/terms",
      title: "Terms of Service",
      description: "Our terms description.",
    });
    expect(out).toContain("<title>Terms of Service · AEOMirror</title>");
    expect(out).toContain('<meta name="description" content="Our terms description." />');
    expect(out).toContain('<meta property="og:url" content="https://aeomirror.com/terms" />');
    expect(out).toContain('<meta property="og:title" content="Terms of Service · AEOMirror" />');
    expect(out).toContain('<meta property="og:description" content="Our terms description." />');
    expect(out).toContain('<meta name="twitter:title" content="Terms of Service · AEOMirror" />');
    expect(out).toContain('<meta name="twitter:description" content="Our terms description." />');
  });

  it("leaves the robots meta tag untouched (page must stay indexable)", () => {
    const out = applyPageMeta(FIXTURE, {
      markup: "<p>x</p>",
      routePath: "/privacy",
      title: "Privacy Policy",
      description: "desc",
    });
    expect(out).toContain('<meta name="robots" content="index, follow, max-image-preview:large" />');
  });
});
