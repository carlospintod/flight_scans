# Phosphor v4 migration notes — flight_scans

Session 2026-07-12, branch `phosphor-v4-landing`. Scope: theme swap (step 1
of `99tools/design-system/pages/flight-scans-web.md`) + landing rebuild
(step 2). Everything below was browser-verified on the dev server at
375/1280, dark only.

## What moved

- **`web/src/app/globals.css`** — the old GTM99-accent `@theme` replaced by
  the vendored Phosphor v4 theme (from `tailwind-theme.css` v4.4). Fonts
  kept on the `next/font` vars (`var(--font-plex-*)`), NOT the umbrella's
  hardcoded stacks (swap hazard (a)). Added: `html{overflow-x:clip}` (the
  full-bleed hero needs it), global `:focus-visible` ring (resolves the
  DESIGN.md drift item), a `:root` bridge mapping `--build/--measure/
  --text-mid/--mono/--bg` to the `--color-*` vars for the vendored
  phosphor.js (JS reads tokens, never defines them).
- **Utility rename sweep** (26 tsx files): `bg-2→bg2`, `bg-3→bg3`,
  `line→border`, `fg→text`, `fg-mid→text-mid`, `fg-bright→text-bright`,
  `fg-dim→hint`, `danger→red`, `cyan→cyan99`, bare `rounded→rounded-card`
  (same 4px, tokenized — drift item).
- **Green split by meaning** (the important one): `matrix` retired.
  - live / cheap / fresh / ok / new-low → `good` family (hero price + its
    glow, price cells and price links (hover brightens to `soft-green`),
    StaleBadge fresh dot (`shadow-glow-good`), AlertsFeed `new_low`, run
    status `ok`, budget bars >40%, capacity "fits", `€0/month`).
  - brand / act → `signature` family (nav + link hovers, SectionHeading,
    primary buttons (`border-signature-dim · text-signature ·
    hover:shadow-glow-sig`), input focus borders, segmented selected,
    minted-invite card, brand cursor, `::selection`).
  - informational action links (view run, run digest) → `cyan99` at rest,
    `signature` on hover (site link grammar).
- **Charts** — `chartTheme` in `EChart.tsx` is now the ONE place chart hues
  live, on v4 values; `kiwi`/`aviasales` are tool-specific series tokens
  (kept at their shipped hexes, also declared in `@theme`); Heatmap's last
  raw `#0a0b10` → `t.bg`; ramp = good→cyan→amber→red; CarrierBar rest
  `good`, emphasis `soft-green`.
- **Primary-button padding** normalized to `px-4 py-2.5` where touched
  (drift item; join/login/searches/new/UserAdmin).
- **Landing rebuilt** (`LandingHero.tsx` + `page.tsx`): the ASCII jet
  retired. Marketing tier: full-bleed `stage--field` hero (aurora + THE
  measuring field), display h1 with the BUILD verb glow on "dates float",
  human-voice asides, primary/secondary CTAs, bench-style scan replay in a
  canon `.term` (numbers are real observations: 532 EUR best seen, −11%
  vs baseline; the refrains are definitional), replay disclaimed in the
  aside right under it, live radar anchored at `#radar`. Devices come from
  `phosphor.css` (vendored subset of components.css) + `public/phosphor.js`
  (vendored verbatim, byte-exact UTF-8 — the first PowerShell copy mojibake'd
  the glyphs; re-vendor with a byte-preserving copy if it ever moves).
  `html.js` reveal-gate is an inline script in layout (theme-script
  pattern, `suppressHydrationWarning` on `<html>`).

## Deliberate divergences / kept-as-shipped

- **Dark-only stands** for app AND landing ("a tracker is a night tool" —
  per the migration note this is allowed; stated here). The vendored theme's
  light block was intentionally not carried over.
- **Footer em-dash** ("can be stale — verify…") and the `—` clause-pivot
  glyph stay: in-app the tool's shipped voice spec wins until the app-UI
  migration step (MASTER's em-dash ban is for umbrella marketing prose).
- **kiwi/aviasales hexes unchanged** (`#b57bff`/`#ff8c42`). kiwi sits close
  to signature mauve — acceptable inside charts where signature never
  appears; revisit if a chart ever mixes brand + kiwi.
- **App-UI surfaces beyond color mapping untouched** (tables, forms, ops):
  their own migration step comes later. The ladders (freshness/run-health/
  budget) kept their exact three-step semantics on canon hues.
- **Marketing devices scoped to the landing**: phosphor.js loads only on
  `/`; `.reveal`/`.term`/ticker classes appear only there. App views stay T1.

## Still open (next session)

- DESIGN.md rewrite onto v4 vocabulary (a banner points here meanwhile).
- Micro-label 10px vs 11px unification; ellipsis/cadence/cost wording picks.
- App-UI migration step proper (spacing/recipes), per the conflict rule.
- Umbrella cross-link in the nav once the 99tools domain settles.
