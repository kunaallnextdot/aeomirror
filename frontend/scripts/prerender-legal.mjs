/* Post-build step (run after `vite build`, see package.json's "build" script).
   AEOMirror's frontend is a client-rendered SPA — the base dist/index.html ships only
   `<div id="root"></div>` and lets React fill it in after the JS bundle loads. That is
   fine for most routes, but Google's OAuth verification review (and crawlers in
   general) must be able to read the Privacy Policy and Terms of Service without
   executing JavaScript. This script renders those two pages to static markup at
   build time with react-dom/server and writes a real, self-contained FLAT file —
   dist/privacy.html, dist/terms.html — not a nested dist/privacy/index.html.

   That flat shape is deliberate, not a stylistic choice: Vercel's Build Output API
   docs state static files are served literally, with "neither their contents, nor
   their file name or extension ... modified in any way" — there is no documented
   guarantee that a request to /privacy resolves to a nested privacy/index.html.
   What Vercel DOES explicitly document is cleanUrls (see vercel.json): "a static
   file named about.html will be served when visiting the /about path." So this
   script's output must stay a flat <route>.html file, paired with "cleanUrls": true
   in vercel.json, for /privacy and /terms to reliably resolve to these files ahead
   of the SPA's catch-all rewrite (filesystem matches take precedence over rewrites,
   per Vercel's own docs on the `rewrites` property).

   A real browser still loads the normal SPA shell afterwards and React just
   re-renders the same content into #root (createRoot, not hydrateRoot, so there is
   no hydration mismatch to worry about).

   Keep this script's surface small: it only prerenders these two legal pages, not
   the whole site — introducing full SSR for the entire SPA is out of scope here. */
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { applyPageMeta } from "./applyPageMeta.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const distDir = path.join(root, "dist");

const PAGES = [
  {
    entry: "src/pages/PrivacyPolicy.jsx",
    routePath: "/privacy",
    name: "privacy",
    title: "Privacy Policy",
    description:
      "How AEOMirror collects, uses, stores, and shares data, including Google " +
      "Analytics and Google user data handling under Google's API Services User " +
      "Data Policy and Limited Use requirements.",
  },
  {
    entry: "src/pages/TermsOfService.jsx",
    routePath: "/terms",
    name: "terms",
    title: "Terms of Service",
    description:
      "The terms that govern your use of AEOMirror, including plans, billing, and " +
      "the optional Google Analytics integration.",
  },
];

async function renderPage({ entry, routePath, name, title, description }) {
  const result = await build({
    entryPoints: [path.join(root, entry)],
    bundle: true,
    write: false,
    format: "esm",
    jsx: "transform",
    external: ["react", "react-dom", "react-dom/*", "lucide-react"],
  });

  const tempFile = path.join(root, `.prerender-${name}.mjs`);
  writeFileSync(tempFile, result.outputFiles[0].text);
  try {
    const mod = await import(pathToFileURL(tempFile).href);
    const Component = mod.default;
    const markup = renderToStaticMarkup(createElement(Component));

    const template = readFileSync(path.join(distDir, "index.html"), "utf8");
    const finalHtml = applyPageMeta(template, { markup, routePath, title, description });

    writeFileSync(path.join(distDir, `${name}.html`), finalHtml);

    console.log(`Prerendered ${routePath} -> dist/${name}.html (${markup.length} chars of markup)`);
  } finally {
    unlinkSync(tempFile);
  }
}

for (const page of PAGES) {
  await renderPage(page);
}
