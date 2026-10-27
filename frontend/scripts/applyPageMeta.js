/* Pure string-templating helper used by prerender-legal.mjs to turn the SPA's built
   index.html into a page-specific static snapshot: swap in the rendered markup, and
   retarget the title/description/canonical/OG tags to the actual route instead of the
   homepage's. Kept dependency-free and side-effect-free so it's unit-testable without
   running a full Vite build. */

const SITE_ORIGIN = "https://aeomirror.com";

export function applyPageMeta(html, { markup, routePath, title, description }) {
  let out = html;

  out = out.replace(
    '<div id="root"></div>',
    `<div id="root">${markup}</div>`,
  );
  out = out.replace(
    /<title>.*?<\/title>/s,
    `<title>${title} · AEOMirror</title>`,
  );
  out = out.replace(
    /<meta name="description" content="[^"]*" \/>/,
    `<meta name="description" content="${description}" />`,
  );
  out = out.replace(
    /<link rel="canonical" href="[^"]*" \/>/,
    `<link rel="canonical" href="${SITE_ORIGIN}${routePath}" />`,
  );
  out = out.replace(
    /<meta property="og:url" content="[^"]*" \/>/,
    `<meta property="og:url" content="${SITE_ORIGIN}${routePath}" />`,
  );
  out = out.replace(
    /<meta property="og:title" content="[^"]*" \/>/,
    `<meta property="og:title" content="${title} · AEOMirror" />`,
  );
  out = out.replace(
    /<meta property="og:description" content="[^"]*" \/>/,
    `<meta property="og:description" content="${description}" />`,
  );
  out = out.replace(
    /<meta name="twitter:title" content="[^"]*" \/>/,
    `<meta name="twitter:title" content="${title} · AEOMirror" />`,
  );
  out = out.replace(
    /<meta name="twitter:description" content="[^"]*" \/>/,
    `<meta name="twitter:description" content="${description}" />`,
  );

  return out;
}
