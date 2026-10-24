/* Shared layout for standalone legal pages (/privacy, /terms). Mirrors Contact.jsx's
   conventions: self-contained `lg-*` CSS, now on the Spectrum `--sp-*` design tokens
   (class names unchanged — self-scoped, no collision risk, only the token values
   were converted in place), no new dependency. Sets document.title and a canonical
   <link> the same lightweight, dependency-free way AppLayout.jsx already sets
   document.title for authenticated routes — this app has no react-helmet or
   per-route meta library. */
import React, { useEffect } from "react";
import { Scale } from "lucide-react";

const SITE_ORIGIN = "https://aeomirror.com";

function useDocumentMeta(title, path) {
  useEffect(() => {
    const prevTitle = document.title;
    document.title = `${title} · AEOMirror`;

    let link = document.querySelector('link[rel="canonical"]');
    const hadLink = !!link;
    const prevHref = link?.getAttribute("href");
    if (!link) {
      link = document.createElement("link");
      link.setAttribute("rel", "canonical");
      document.head.appendChild(link);
    }
    link.setAttribute("href", `${SITE_ORIGIN}${path}`);

    return () => {
      document.title = prevTitle;
      if (hadLink && prevHref) link.setAttribute("href", prevHref);
    };
  }, [title, path]);
}

export default function LegalPage({ title, path, effectiveDate, intro, children }) {
  useDocumentMeta(title, path);
  return (
    <div className="lg-wrap">
      <style>{CSS}</style>
      <div className="lg-eyebrow"><Scale size={13} /> LEGAL</div>
      <h1 className="lg-title">{title}</h1>
      <div className="lg-updated">Last updated: {effectiveDate}</div>
      {intro && <p className="lg-intro">{intro}</p>}
      <div className="lg-body">{children}</div>
    </div>
  );
}

export function Section({ id, title, children }) {
  return (
    <section id={id} className="lg-section">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

const CSS = `
.lg-wrap{max-width:780px;margin:0 auto;padding:48px 24px 80px}
.lg-eyebrow{display:inline-flex;align-items:center;gap:7px;color:var(--sp-primary);font-size:11px;
  letter-spacing:.12em;margin-bottom:18px;border:1px solid var(--sp-line);padding:5px 11px;border-radius:var(--sp-r-pill);
  font-family:var(--sp-font-numeric)}
.lg-title{font-family:var(--sp-font-heading);font-size:36px;line-height:1.1;font-weight:700;letter-spacing:-.02em;margin:0 0 10px;color:var(--sp-ink)}
.lg-updated{font-size:12.5px;color:var(--sp-muted);font-family:var(--sp-font-numeric);margin-bottom:22px}
.lg-intro{color:var(--sp-ink-2);font-size:15px;line-height:1.65;margin:0 0 32px;padding-bottom:28px;border-bottom:1px solid var(--sp-line)}

.lg-body{display:flex;flex-direction:column;gap:30px}
.lg-section h2{font-family:var(--sp-font-heading);font-size:19px;font-weight:700;color:var(--sp-ink);margin:0 0 10px}
.lg-section p{color:var(--sp-ink-2);font-size:14.5px;line-height:1.7;margin:0 0 10px}
.lg-section p:last-child{margin-bottom:0}
.lg-section ul{margin:0 0 10px;padding-left:20px;color:var(--sp-ink-2);font-size:14.5px;line-height:1.7}
.lg-section ul:last-child{margin-bottom:0}
.lg-section li{margin-bottom:4px}
.lg-section strong{color:var(--sp-ink);font-weight:600}
.lg-section a{color:var(--sp-primary);text-decoration:none;font-weight:500}
.lg-section a:hover{text-decoration:underline}
.lg-note{background:var(--sp-t-cyan);border:1px solid var(--sp-cyan-d);border-radius:var(--sp-r-s);
  padding:12px 14px;font-size:13.5px;color:var(--sp-cyan-d);line-height:1.6}
`;
