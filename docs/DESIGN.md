# Phosphor — the flight_scans design system

The interface has one idea: **a flight tracker that reads like a terminal
you trust.** Dark like a shell, monospace for everything that carries
data, a single phosphor-green accent that only ever means *live, cheap,
or go* — and copy that states the mechanism and the limits instead of
selling. This document is that system, distilled from the code that
already implements it (`web/src`). Class strings here are the literal,
reproducible truth.

## Principles

1. **Mono is the data voice; sans is only for prose.** IBM Plex Mono
   carries every label, number, table, button, and status. IBM Plex
   Sans appears only when English wraps to two or more sentences
   (landing pitch, about, privacy, form intros). If it's terminal-ish or
   tabular, it's mono.
2. **Green is an accent, never body text.** `matrix` (#00ff41) marks
   live / cheap / act. It colors the hero price, section headings,
   primary-action text, link hovers, and "new low" — never a paragraph
   or a table body.
3. **Every color is a status.** There is no decorative color. Each token
   means one thing (below), and the same three-step ladder —
   green → amber → red — expresses freshness, run health, and budget.
4. **Depth is three background steps, not shadows.** `bg` (page /
   recessed input wells) < `bg-2` (cards, chrome) < `bg-3` (raised /
   hover / selected). The only shadow is the green glow, and it only
   means "live / act now."
5. **Honesty is a design element.** The product states its own limits in
   fixed places — the footer disclaimer, the "Honest limits" section,
   stale badges, self-transfer flags. Disclaimers instruct verification;
   they never apologize.
6. **No theater beyond the terminal.** Three animations exist (blinking
   cursor, one flying ASCII jet, nothing else); charts have animation
   off; feedback is an inline caption, never a toast, spinner, or modal.

## Tokens

All defined in `web/src/app/globals.css` `@theme`. Dark is the only theme.

### Color

| Token | Hex | Means | Used for |
|---|---|---|---|
| `bg` | `#0a0b10` | page floor | body, recessed input wells |
| `bg-2` | `#0f1018` | surface | cards, header, buttons, tooltips |
| `bg-3` | `#151620` | raised | terminal title bar, row hover, selected segment |
| `line` | `#1e1f32` | default rule | card borders, dividers, gridlines |
| `line-bright` | `#2c2d44` | emphasis rule | table-header rule, secondary-button border, dashed route |
| `matrix` | `#00ff41` | **live / cheap / go** | hero price, section headings, primary text, link hover, "new low" |
| `matrix-dim` | `#00b830` | green at rest | button borders, input focus, fills, ok status, price link before hover |
| `cyan` | `#00d4ff` | informational | "drop" alerts, "public demo", tech nouns in prose |
| `amber` | `#ffcc00` | aging / degraded / caution | stale-but-usable data, "degraded"/"skipped", self-transfer |
| `danger` | `#ff4455` | error / dead / destructive | fetch errors, failed status, delete actions, over-capacity |
| `fg-bright` | `#e4e6f0` | emphasis | H1s, brand, prices, input text, first table column |
| `fg` | `#c8cad8` | body | table bodies, list prose, detail values |
| `fg-mid` | `#8e91a8` | secondary | nav at rest, sub-headings, **every empty-state message** |
| `fg-dim` | `#585b72` | faintest | footnotes, form labels, table headers, key labels |

Other tokens: `--font-sans` / `--font-mono` (IBM Plex Sans / Mono),
`--radius-card: 4px`, `--shadow-glow: 0 0 14px rgb(0 255 65 / 0.18)`.

### The three semantic ladders

The same green→amber→red gradient, three contexts:

- **Freshness** (`StaleBadge`, price rows): fresh ≤4d = `matrix` dot +
  `shadow-glow`; aging >4d = `amber`; stale >8d = `danger`. Stale rows
  also fade to `opacity-55`.
- **Run health** (ops history): `ok` = `matrix-dim`; `degraded` /
  `skipped` = `amber`; failed = `danger`.
- **Budget** (quota bars): remaining >40% = `matrix-dim`; >15% = `amber`;
  else `danger`, on an `h-1` track over `bg-3`.

Success is `matrix-dim`, not `matrix` — full green is reserved for money
and alerts.

### Type scale — mono-first pixel ladder

Weights in practice: `400` default, `font-semibold` (600) for brand,
headings, prices, primary buttons. (Weight 500 is loaded but unused —
drop it.)

| Size | Role |
|---|---|
| `text-[10px]` | micro-labels: form labels, `<th>`, `<dt>`, badges — **always** uppercase + `tracking-wider` + `fg-dim` |
| `text-[11px]` | footnotes, section headings, status badges, micro-buttons |
| `text-[12px]` | dense data rows, status/error messages, secondary links |
| `text-[13px]` | standard data: table bodies, detail grids, inputs, secondary buttons |
| `text-sm` (14) | nav brand, empty states, primary buttons, sans prose body |
| `text-lg` (18) | utility-page H1s (literal ALL CAPS) |
| `text-2xl` (24) | marketing/itinerary H1s (sentence case) |
| `text-5xl` (48) | the single hero price |

Tracking: `tracking-[2px]` for brand marks, `SectionHeading`, and the
price eyebrow; `tracking-wider` for nav, labels, buttons, badges, table
headers.

### Spacing & layout

- **Shell:** `mx-auto max-w-6xl px-4`; header `py-3`, main `py-8`,
  footer `py-6`.
- **Narrowing by purpose:** `max-w-3xl` (about) > `max-w-2xl` (prose) >
  `max-w-xl` (create form) > `max-w-lg` (account) > `max-w-md` (join) >
  `max-w-sm` (login). Auth pages center with `mx-auto pt-16`.
- **Vertical rhythm:** `space-y-10` (dashboards), `space-y-8` (detail),
  `space-y-6` (short forms); `space-y-4` inside cards; `space-y-1.5/2`
  for list rows. Metadata sub-lines `mt-1`; inner card dividers
  `mt-3 border-t border-line pt-3`.
- **Grids:** `grid gap-3 sm:grid-cols-2` (forms/detail pairs),
  `sm:grid-cols-3` (option cards), `sm:grid-cols-2 lg:grid-cols-3`
  (quota tiles).
- **Wide content** (tables, charts) always wraps in `overflow-x-auto`
  with a computed `minWidth` — the page body never scrolls sideways.

## Component recipes

Copy these class strings verbatim.

**Card** — `rounded-card border border-line bg-bg-2 p-4` (`p-6` heroes,
`p-3` compact/nested).

**Section** — `<h2 class="mb-3 border-b border-line pb-1.5 font-mono
text-[11px] font-semibold uppercase tracking-[2px] text-matrix">`. The
only place `matrix` is a heading color; its border-b is the section
divider. Charts live inside a Card under it; tables/feeds sit bare.

**Primary button** — `rounded-card border border-matrix-dim bg-bg-2 px-4
py-2.5 font-mono text-sm font-semibold tracking-wider text-matrix
hover:shadow-glow disabled:opacity-40`. The glow-on-hover is exclusive to
primary CTAs. Caption is ALL CAPS with a verb glyph: `▶ ` to run,
`+ ` to create. Busy state swaps the label to a gerund + ellipsis.

**Secondary button** — `rounded-card border border-line-bright bg-bg-2
px-4 py-2 font-mono text-[13px] tracking-wider text-fg-bright
hover:border-matrix-dim`. No glow; hover brightens the border.

**Danger action** — never fires on first click. Trigger:
`border-line ... text-danger/80 hover:border-danger` with an ellipsis
label; then a confirm card `rounded-card border border-danger/50 bg-bg-2
p-3` with a blunt warning and DELETE / CANCEL.

**Input** — `w-full rounded-card border border-line bg-bg px-2.5 py-2
font-mono text-[13px] text-fg-bright outline-none focus:border-matrix-dim`.
Inputs sit on `bg` (darker than their card) as recessed wells. Focus is
border-color only, no ring. Label above:
`block font-mono text-[10px] uppercase tracking-wider text-fg-dim mb-1`.

**Status pill** — `rounded border px-1.5 py-0.5 text-[10px] uppercase
tracking-wider` with border and text in the *same* semantic color,
transparent fill.

**Segmented control** — `inline-flex overflow-hidden rounded-card border
border-line`; each option `px-4 py-2 font-mono text-[12px] tracking-wider`;
selected `bg-bg-3 text-matrix`, unselected `bg-bg-2 text-fg-mid
hover:text-fg`.

**Table** — `w-full border-collapse font-mono text-[13px]`; header row
`border-b border-line-bright text-left text-[10px] uppercase
tracking-wider text-fg-dim`, th `py-2 pr-4`; body rows `border-b
border-line`, cells `py-2 pr-4`. Numeric/status cells colored by
semantics. Wide tables wrap in `overflow-x-auto`; complex tables ship a
`sm:hidden` card-list fallback.

**Terminal window** (`LandingHero`) — outer `overflow-hidden rounded-card
border border-line bg-bg-2`; title bar `border-b border-line bg-bg-3
px-3 py-2` with three `h-2.5 w-2.5 rounded-full` dots in
`bg-danger/70 bg-amber/70 bg-matrix/70` order + a fake shell path in
`text-[11px] tracking-wider text-fg-dim`. Boot line uses a `$` prompt in
`text-matrix` and a trailing `.cursor-blink`. The ASCII jet is
`aria-hidden`, green with a text-glow, animated by a component-scoped
`fly` keyframe (6s linear) that **`prefers-reduced-motion` disables**.

**Empty state** — a single bare `<p class="font-mono text-sm
text-fg-mid">` shaped `No X yet — <what fills it, when>`. No icons, no
CTA. Placed exactly where the data would be.

**Feedback** — inline mono caption by the trigger, never a toast/modal.
Error `font-mono text-[12px] text-danger`; success `... text-matrix`;
pending = label swap + `disabled:opacity-40` (no spinners).

## Charts

All go through `EChart.tsx` (canvas, `animation:false`) and pull from the
exported `chartTheme`. Uniform recipe: tooltip `{bg: bg-2, border:
line-bright, text: fg/mono/12}`; axis labels `fg-mid` mono 10; axis/split
lines `line`; legend `fg-mid` mono 11. Series: price line = `matrix`
stroke 2 + area `rgba(0,255,65,0.06)`; bars = `matrix-dim` with `matrix`
on emphasis; price ramp cheap→expensive = `[matrix, cyan, amber,
danger]`. Per-source line colors live in a `SOURCE_COLORS` map.

## Voice

- **Case:** brand, nav, page H1s, and every button are literal UPPERCASE
  in source. Section headings / labels / badges are written sentence-case
  and transformed with the `uppercase` class. Inline metadata, hints, and
  API errors are lowercase fragments. Body prose is sentence case with
  periods.
- **The refrain:** cost is always a *guaranteed upper bound* — the `≤N`
  notation plus "a scan can spend less, never more." Contingency spend is
  footnoted "only if the primary rail fails."
- **Personification:** the system is "the tracker" and its verb is
  "hunt"; the user is "you." Bad news ships with the remedy in the same
  sentence ("SKIPPED (capacity) — budget intact, first in line next
  run").
- **Never:** emojis, exclamation marks, "please/sorry/oops/welcome," or
  marketing superlatives. Selling is done with mechanics and numbers
  ("Built on free APIs, for €0/month").
- **Glyphs:** `▶` run · `+` create · `→` route/forward · `←` back · `✓`
  pass · `✗` fail · `·` metadata separator · `—` clause pivot / null ·
  `≤` upper bound · `~` approximation · `$` prompt · `_` cursor.
- **Numbers:** prices are bare integer + space + ISO code (`434 EUR`, no
  symbol, no decimals); dates `5 Sep` / `Sat, 5 Sep 2026`; null → `—`.

## Do / Don't

- **Do** reach for a token's *meaning* — if it isn't live/cheap/go, it
  isn't `matrix`.
- **Do** wrap any wide element in `overflow-x-auto` before shipping.
- **Do** end pages and complex widgets with the fine-print closer
  (`font-mono text-[11px] text-fg-dim`).
- **Don't** color body text or a table body green.
- **Don't** add a spinner, toast, or modal — swap a label, show an inline
  caption.
- **Don't** glow anything amber, red, or cyan. Glow means "live."
- **Don't** apologize in an error; state the fact and the remedy.

## Known drift (normalize when touched)

The system is ~95% consistent; these are the deviations worth converging
on the dominant pattern:

- **Radius token bypass:** chips, selects, and micro-buttons use bare
  `rounded` instead of `rounded-card` — decoupled from `--radius-card`.
- **Chart tokens duplicated:** `chartTheme` and `Heatmap` hardcode hex
  copies of `@theme` (incl. `#0a0b10` and off-palette `#b57bff` kiwi /
  `#ff8c42` aviasales) — a token change silently desyncs charts. Add
  `kiwi`/`aviasales` series tokens and read them in one place.
- **Table micro-labels** flip between `text-[10px]` (dominant) and
  `text-[11px]` (AlternativesTable); pick 10.
- **Primary-button padding** has six variants across six CTAs; settle on
  `px-4 py-2.5`.
- **Ellipsis** mixes `…` and `...`; **"stale"** has two thresholds (8d
  badge vs 10d rows); **cadence** is written three ways ("three times a
  week" / "3× a week" / "3 scans/week"); **cost** is both `€0/month` and
  `$0/month`. Pick one of each.
- **No focus-visible style on buttons/links** — only inputs cue focus.
  Add a keyboard-visible ring for accessibility.
- **`SearchRadar` H1** is `text-sm text-fg-mid` — visually below a
  micro-label, inverting the page-title pattern.
