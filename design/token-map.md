# Aurora migration — token map

Living ledger for the **incremental token aliasing** migration (strategy A).
Reference design: `design/aurora-reference.html` (canonical copy of Prototype-C,
sha256 `8a172977e93edd0a7339d36d19d719d68dd27d0ea25f5c8657c449eb2503b367`).

## Rules (confirmed)
- Aurora tokens (`--au-*`) live **alongside** the dark tokens on `.root` in
  `frontend/src/App.jsx`. **No dark token is touched** until the final cleanup phase.
- **No global flip** — not colours, not fonts. Fonts are defined as tokens and the webfonts
  are loaded, but font faces are applied **per screen**, exactly like colour. A global font
  swap is rejected: it changes type on un-reviewed screens, breaks independent rollback, and
  shifts line-heights/wrap points on panels tuned for Inter/Hanken.
- **Bare names are labels only.** Nothing bare-named (`line`, `line-2`, `primary`, `ink`, …)
  is defined in `:root`/`.root`. `--line` / `--line-2` stay untouched dark tokens.
- A screen migrates by scoping its Aurora styles under a **`.aurora-screen` wrapper** on that
  screen's root (see "Injected-style blocker" below) — one screen at a time.
- **Deletion of dark tokens + the injected `.root` base block is its own final phase**, gated
  on this map showing **zero** remaining references. No opportunistic mid-migration deletion.
- **Admin (`/admin/*`) is out of scope** — never repointed, no aliases added for it.

## Token table — spec label → `--au-*` token → status

Aurora is a light/glass theme; mappings are semantic, not 1:1 hue swaps. "Installed" = the
`--au-*` token is defined (Phase 1). "Consumed by" is filled in as screens are repointed.

| Spec label | `--au-*` token | Value | Status |
|---|---|---|---|
| app background | `--au-app` | `#F7F9FB` | installed |
| panel background | `--au-panel` | `#FBFCFD` | installed |
| solid | `--au-solid` | `#FFFFFF` | installed |
| glass | `--au-glass` | `rgba(255,255,255,.72)` | installed |
| ink | `--au-ink` | `#141E33` | installed |
| ink-2 | `--au-ink-2` | `#3E4A66` | installed |
| muted | `--au-muted` | `#7A8499` | installed |
| line | `--au-line` | `rgba(20,30,51,.08)` | installed |
| line-2 | `--au-line-2` | `rgba(20,30,51,.05)` | installed |
| primary | `--au-primary` | `#0E7A6B` | installed |
| pop | `--au-pop` | `#FF7A59` | installed |
| mint / mint-d | `--au-mint` / `--au-mint-d` | `#DFF6EE` / `#0E7A6B` | installed |
| lav / lav-d | `--au-lav` / `--au-lav-d` | `#EAE6FE` / `#6C4BF0` | installed |
| peach / peach-d | `--au-peach` / `--au-peach-d` | `#FFEDE4` / `#E8663A` | installed |
| sky / sky-d | `--au-sky` / `--au-sky-d` | `#E2F1FD` / `#1E7FC2` | installed |
| lemon / lemon-d | `--au-lemon` / `--au-lemon-d` | `#FFF6DC` / `#B98407` | installed |
| success (=mint) | `--au-success` / `--au-success-d` | → mint pair | installed |
| warning (=lemon) | `--au-warning` / `--au-warning-d` | → lemon pair | installed |
| danger/alert (=peach) | `--au-danger` / `--au-danger-d` | → peach pair | installed |
| neutral/other (=sky) | `--au-neutral` / `--au-neutral-d` | → sky pair | installed |
| r-s / r-m / r-l | `--au-r-s` / `--au-r-m` / `--au-r-l` | `14px` / `22px` / `30px` | installed |
| pill | `--au-r-pill` | `999px` | installed |
| sh-s / sh / sh-l | `--au-sh-s` / `--au-sh` / `--au-sh-l` | (prototype values) | installed |
| display font | `--au-font-heading` | `'Outfit',system-ui,sans-serif` | installed (webfont loaded) |
| body font | `--au-font-body` | `'Plus Jakarta Sans',system-ui,sans-serif` | installed (webfont loaded) |
| numeric/mono font | `--au-font-numeric` | `'DM Mono',ui-monospace,monospace` | installed (webfont loaded) |
| standard easing | `--au-ease` | `cubic-bezier(.2,.9,.28,1)` | installed |
| reveal | `@keyframes au-reveal` | opacity 0→1, translateY(20px) scale(.985)→none | installed (unused) |
| reduced-motion guard | `@media(prefers-reduced-motion:reduce)` | verbatim from prototype | installed (global, a11y-only) |

### Dark token ↔ Aurora intent (for the per-screen phases)
| Role | Dark token | Aurora token |
|---|---|---|
| App ground | `--bg` #0B0F14 | `--au-app` |
| Card/panel | `--panel` #111922 | `--au-panel` / `--au-solid` |
| Raised surface | `--panel-2` #16202B | `--au-glass` |
| Hairline / border | `--line` / `--line-2` | `--au-line` / `--au-line-2` |
| Primary / secondary / dim text | `--txt` / `--txt-mid` / `--txt-dim` | `--au-ink` / `--au-ink-2` / `--au-muted` |
| Accent / CTA | `--accent` | `--au-primary` (+ `--au-pop` sparingly) |
| good / warn / bad | `--good` / `--warn` / `--bad` | `--au-success-d` / `--au-warning-d` / `--au-danger-d` |
| headings / body / numbers | Hanken / Inter / IBM Plex Mono | `--au-font-heading` / `--au-font-body` / `--au-font-numeric` |

## Injected-style blocker (App.jsx `<style>{CSS}</style>` on `.root`)
`App.jsx` injects a `<style>` at runtime that sets, at **element specificity**, on `.root`:
- `background:var(--bg)` (dark), `color:var(--txt)`, `font-family:'Inter'`, `font-size:14px`
- `.mono{font-family:'IBM Plex Mono'}`
- `h1,h2,h3,.brand,.topbar-title,.gauge-num,.cta-title{font-family:'Hanken Grotesk'}`

Because this is injected at render (after the bundled CSS), a plain `:root`/`body` rule can lose
on source order → unpredictable partial flip. **Do not fight it globally.** Per-screen migration
scopes Aurora under a `.aurora-screen` wrapper with specificity that beats these element rules
(e.g. `.aurora-screen h1`, `.aurora-screen.dash-content`). Removing this injected block is part
of the **final cleanup phase**, with dark-token deletion — not before.

## Migration order + status  ← SINGLE SOURCE OF TRUTH for what's left
Re-cut order (supersedes the earlier 9-item list entirely). Status: ⬜ not started · 🟡 in progress · ✅ done · ⛔ out of scope.

**Foundation (pre-phases, done):**
| Foundation | What | Files | Status |
|---|---|---|---|
| F1 | Token install — Aurora fonts loaded, `--au-*` tokens, radius/shadow/semantic, motion + reduced-motion guard | `index.html`, `src/App.jsx` (`.root`) | ✅ done |
| F2 | Primitives + signature visualisations (`Shell`/`TopNav`/`PageHead`/`Bento`/`Cell`/`Metric`/`Tag`/`Button`/`Ring`/`ProgressBar`/`Skeleton`/`StepList`/`EngineCard` + `Podium`/`Radar`/`PromptRowList`) + `.aurora-screen` wrapper | `src/dashboard/aurora.jsx`, `aurora.css` | ✅ done |
| F3 | `/ui-kit` dev-only review surface (extend as new primitives appear; NOT a phase) | `src/dashboard/aurora-uikit.jsx` | ✅ done |

**Screen migration:**
| # | Screen(s) / route(s) | Files | Status |
|---|---|---|---|
| 1 | ScanDetails — `/app/scans/:scanId` (all states) | `src/dashboard/ScanDetails.jsx`, `ScanDetails.aurora.css`, `src/app/routes.jsx` (`ScanDetailRoute`), `aurora.css` (`.aurora-screen`) | ✅ done |
| 2 | ScansTable — `/app/scans` | `src/dashboard/ScansTable.jsx`, `ScansTable.aurora.css`, `src/app/routes.jsx` (`ScansRoute`) | ✅ done |
| 3 | ReportView — `/app/report` | `src/dashboard/ReportView.jsx`, `ReportView.aurora.css`, `routes.jsx` (`ReportRoute`+`AuroraGated`) | ✅ done |
| 4 | PublicReport — `/r/:token` (noindex kept) | `src/dashboard/PublicReport.jsx`, `aurora.css` (`.au-pub-*`) | ✅ done |
| 5 | DashboardHome — `/app/dashboard` | `src/dashboard/DashboardHome.jsx`, `charts.jsx` (opt-in `aurora` prop), `routes.jsx` (`AuroraGated`) | ✅ done |
| 6 | AnswerTracking — `/app/answer-tracking[…]` (incl. inline "Analysing responses…") | `src/dashboard/AnswerTracking.jsx`, `AnswerTracking.aurora.css` | ✅ done |
| 7 | Monitoring + MonitorDetail | `src/dashboard/{Monitoring,MonitorDetail}.jsx`, `Monitoring.aurora.css` | ✅ done |
| 8 | Compare — `/app/compare` | `src/dashboard/Compare.jsx`, `routes.jsx` (AuroraGated) | ✅ done |
| 9 | WebsiteSummary — `/app/website-summary` | `src/app/routes.jsx` (`WebsiteSummary`, AuroraGated) | ✅ done |
| 10 | Settings cluster — `/app/billing`, `/app/profile`, `/app/team`, `/app/organization` (one phase, shared form primitives) | `src/dashboard/BillingView.jsx` (+`UpgradeModal`/`SharePanel`), `src/auth/pages/{Profile,Team,Organization}Page.jsx`; new Aurora form primitives (`AuField`/`AuTextInput`/`AuPasswordInput`/`AuPasswordStrength`/`AuAlert`/`AuAvatar`/`AuRoleBadge`) in `aurora.jsx` + `au-form`/`au-alert`/`au-mem-*`/`au-bill-*`/`au-up-*`/`au-share-*` in `aurora.css` | ✅ done |
| 11 | **AppLayout shell** — sidebar, topbar, nav states, `/app` redirect, `NotFoundRoute`. App-wide flip (cannot be `.aurora-screen`-scoped) → **full regression pass across every migrated screen at 390px + desktop**, not just a diff. Last among `/app/*` work. | `src/app/AppLayout.jsx` (+ new `AppLayout.aurora.css`), `src/app/routes.jsx` (`NotFoundRoute`), `src/app/RouteErrorBoundary.jsx` (`InLayoutErrorState` only) | ✅ done (code); **⚠️ awaiting user 390px+desktop QA** |
| 12 | Auth pages — `/login`, `/register`, `/forgot-password`, `/reset-password`, `/verify-email`, `/accept-invitation` (auth LOGIC untouched) | `src/auth/pages/{Login,Register,ForgotPassword,ResetPassword,VerifyEmail,AcceptInvitation}.jsx`; new Aurora `AuAuthShell`/`AuSubmitButton` in `aurora.jsx` + `au-auth-*`/`au-spin` in `aurora.css`. **`auth/ui.jsx` + `auth.css` LEFT INTACT** (App.jsx's landing still imports the dark `Avatar`; `.btn`/`.alert` shared with landing + `ContentInsights`) → retire in #13/#14 | ✅ done |
| 13a | Landing chrome (`TopBar`/`SiteFooter`) + `Marketing` hero/strip + single-page `FreeScanner` (`Gauge`/`CrawlerStrip`/`SignalBars`/`IssueList`/report card + idle/scanning/done/402-gate states) | `src/App.jsx` (+ new `App.aurora.css`) | ✅ done; **⚠️ awaiting user QA** |
| 13b | `BulkScanPanel` + `SkippedList` (signed-in Bulk tab) + **Contact** page body | `src/App.jsx` (`BulkScanPanel`/`SkippedList` → `au-bulk-*` in `App.aurora.css`), `src/pages/Contact.jsx` (self-contained `ct-*` CSS block converted to `--au-*` in place) | ✅ done; **⚠️ awaiting user QA** |
| 14 | **Cleanup — re-scoped to SAFE DEAD-CODE ONLY** (user decision). The original goal (delete dark tokens + injected `<style>` block + `.aurora-screen` wrappers) is **NOT achievable**: the dark tokens are defined only on `.root` in App.jsx and Admin (out of scope), ContentInsights (never migrated), and the error-boundary defaults still consume them — so the token block + injected base rules STAY. Removed only provably-dead code. | `App.jsx` (injected CSS), `dashboard.css`, `auth.css`, `dashboard/ui.jsx`, `auth/ui.jsx`, `routes.jsx`, `RouteErrorBoundary.jsx` | ✅ done (safe scope); **⚠️ awaiting user QA** |
| — | Admin — `/admin/*` | `src/admin/*` | ⛔ out of scope (stays dark; step 14 must preserve tokens it references) |

Notes:
- Shell (11) is deliberately after the eight `/app/*` screens: a dark shell around one Aurora screen is a bounded, shippable mismatch; an Aurora shell around dark screens is wrong everywhere. A per-route allowlist toggle in the shell was **explicitly rejected** (conditional chrome logic / temporary code path).
- `NotFoundState` in `routes.jsx` became unused after #1 (its only caller was `ScanDetailRoute`); leave it until #11/#14 cleanup.
- #13b added `au-bulk-*` to `App.aurora.css` and switched `BulkScanPanel`/`SkippedList` onto them (+ the shared `au-scan-go`/`au-scan-error`/`au-scan-hint`); `Contact.jsx` keeps its own injected `<style>` block but its `ct-*` rules + the inline `msgColor` now use `--au-*` (values converted in place — class names unchanged since `ct-*` is self-scoped). Landing is now fully Aurora; App.jsx's shared injected `CSS` block is still dark (legacy app-shell/table/etc. rules) and removed in #14.
- #14 (safe dead-code scope) — audit findings + what was removed vs kept:
  - **KEPT (live dark, cannot remove):** the `.root` dark-token block + injected base rules (`.root *{box-sizing}`, `.mono`, `h1-h3` typography, reduced-motion, `au-reveal` keyframe) + `.spin-slow`; **Admin** (admin.css + AdminApp, defines no tokens of its own — depends on `.root`); **ContentInsights** (`.ci-*` + `.btn`/`.d-panel`/`.d-mono`/`.d-iconbtn`/`.d-btn`, still dark, never migrated); the **RouteErrorBoundary** outer defaults (`Centered`/`NewVersionNotice`/`DefaultRuntimeError` — dark, render on `.root` before the shell); the still-dark charts.jsx dark branch + `.d-chart`/`.d-fail*`; `scoreColor`/`fmtDate`/`fmtDuration` (dashboard/ui.jsx) and `passwordStrength` (auth/ui.jsx).
  - **REMOVED (dead):** `routes.jsx` dark `Gated` + `NotFoundState`; `dashboard/ui.jsx` dead components (`ScoreRing`/`StatusBadge`/`StatCard`/`Skeleton`/`TableSkeleton`/`StatsSkeleton`/`EmptyState`/`MiniEmpty`/`ErrorState`/`band`/`statusLabel`/`statusColor`); `auth/ui.jsx` dead primitives (`AuthShell`/`Field`/`TextInput`/`PasswordInput`/`PasswordStrength`/`Alert`/`SubmitButton`/`Avatar`/`RoleBadge`); `App.jsx` injected CSS dead marketing + walking-skeleton blocks (~23 KB); `auth.css` trimmed to just `.btn*`; `dashboard.css` **52.9 KB → ~2 KB of rules** — removed the fully-migrated dark families (`dash*`, `bill-*`, `up-*`, `rep-*`, `rep-share*`, `mon-*`, `at-*`, `ca-*`, `cha-*`, `pub*`, `ts-*`, `site-prog*`, `bulk-chip/lock*`, `side-meter*`, and the dead `d-*` families: `d-ring`/`d-card`/`d-skel*`/`d-stat*`/`d-empty*`/`d-error`/`d-retry`/`d-sig*`/`d-ev*`/`d-cmp*`/`d-grid*`/`d-table*`/`d-search`/`d-select`/`d-input`/`d-toolbar`/`d-up/down/flat`/`d-diff`/`d-rec`/`d-found*`) + dead keyframes. Verified against the JSX class-usage set (only `au-`/`ad-` counterparts are referenced) + git-HEAD diff (zero original `ci-*` lost). Main index chunk 113.9 → 90.8 KB.
  - **NOT done (needs a design decision + QA, deferred):** removing the dark-token block (blocked by Admin + ContentInsights) and dropping the `.aurora-screen` wrappers (they carry each screen's blob backdrop; the shell content area has none, so removal is a visual change, not a mechanical delete).
- #13a ported the landing to `App.aurora.css` (au-prefixed `au-topbar`/`au-site-footer`/`au-mkt`/`au-scanner`/`au-gauge`/`au-strip`/`au-fam`/`au-issues`/`au-report`), added `auBandColor`/`AU_STATUS_COLOR` (same thresholds as `bandColor`/`STATUS_COLOR`, au tokens), and wrapped `MarketingRoot`/`ContactRoot` in a light `au-site` ground. The injected dark `CSS` block in App.jsx is UNTOUCHED (removed in #14); `bandColor`/`STATUS_COLOR` are now unused in App.jsx but left for #14. `TopBar` now uses `AuAvatar` (so App.jsx no longer imports the dark `Avatar` — `auth/ui.jsx`'s `Avatar` is finally dead). **13b remainder:** `BulkScanPanel`/`SkippedList` still use dark `bulk-*`/`scan-*` classes and `Contact.jsx` is dark — both render inside the light `au-site`, a known bounded gap.
- #12 rewrote the 6 auth pages onto the #10 Aurora primitives + new `AuAuthShell`/`AuSubmitButton`; the pure `passwordStrength()` is imported from `auth/ui.jsx` (logic, unchanged). `auth/ui.jsx`'s presentational primitives (`AuthShell`/`Field`/`TextInput`/`PasswordInput`/`PasswordStrength`/`Alert`/`SubmitButton`) are now dead code EXCEPT `Avatar` (App.jsx landing) + `passwordStrength`; `auth.css` `.btn`/`.alert`/`.f-*` still power the dark landing + `ContentInsights`. Both retire in #13 (landing)/#14 (cleanup). The `VerifyEmail`/`AcceptInvitation` loading rows render a raw `au-alert au-alert-info` + spinning `Loader2` (not `AuAlert`, which would force its own icon).
- #11 flipped the shell by adding NEW `au-dash*`/`au-side-meter*` classes (in `AppLayout.aurora.css`) and swapping AppLayout's class names — the dark `.dash*` rules in `dashboard.css` are LEFT INTACT (still used by the leftover shared dark `EmptyState`/`MiniEmpty` via `.dash-newscan`; Admin never used `.dash*`). Sidebar kept as a left rail (NOT converted to the prototype's horizontal TopNav — that would be a structural change); glass sidebar + sticky glass topbar on `--au-app`. Active nav = mint tint + primary text (the app's existing pill pattern), not the prototype's dark-fill. The shell provides ONE light ground; each screen STILL brings its own `.aurora-screen` + Shell blobs (redundant nesting → collapsed in #14). `RouteErrorBoundary`: only `InLayoutErrorState` (always rendered inside the light shell as AppLayout's fallback) went Aurora; `Centered`/`NewVersionNotice`/`DefaultRuntimeError` stay dark because the OUTER boundary (App.jsx wraps `<AppRoot>`, no fallback) renders them on the dark `.root` before the shell mounts (chunk-load failures). `NotFoundState` in routes.jsx is now fully unused → delete in #14.
- #10 rewrote the account pages onto NEW Aurora form primitives in `aurora.jsx` (`AuField`/`AuTextInput`/`AuPasswordInput`/`AuPasswordStrength`/`AuAlert`/`AuAvatar`/`AuRoleBadge`) rather than restyling the shared dark `auth/ui.jsx` — so un-migrated auth screens keep rendering dark. **#12 (Auth pages) should reuse these primitives** and retire the dark `auth/ui.jsx` equivalents at that point. The pure `passwordStrength()` policy is imported from `auth/ui.jsx` (logic, unchanged), not duplicated. `UpgradeModal` renders outside any `.aurora-screen` (overlay) so `.au-up-*` carries its own light surface + fonts; `SharePanel` renders inside ReportView's `.aurora-screen`.

## Known hardcoded dark colours that will clash later (logged, NOT fixed)
- `#04222a` — text-on-accent (App.jsx `.tb-primary` / `.dash-newscan` / `.footer-cta`)
- `#0d141b` — report code-block bg (`dashboard.css` `.rep-code`)
- `html,body{background:#0B0F14}` (App.jsx CSS string)
- `theme-color #0B0F14` (index.html meta)
- admin `--ad-accent:#E6A94A` — ⛔ out of scope

## Deferred tickets (logged, not built)
- **Radar / spider viz**: does not exist today; the Aurora bento may imply one. Deferred as its
  own feature. If a grid cell needs filling mid-phase, leave it out or use an existing component.
