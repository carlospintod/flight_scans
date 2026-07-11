import type { CSSProperties } from "react";

const d = (ms: number) => ({ "--d": `${ms}ms` }) as CSSProperties;

/** Phosphor v4 landing hero (marketing tier). The old ASCII jet retired:
 *  the atmosphere is now the measuring field — a constellation behind the
 *  pitch that links to the pointer and reads out distances (phosphor.js,
 *  loaded by the page). Below the pitch, one real scan replayed in a
 *  terminal window; the numbers are real observations (532 EUR best seen,
 *  −11% vs baseline), disclaimed as a replay right underneath. */
export function LandingHero() {
  return (
    <section className="bleed stage stage--field -mt-8 mb-14 pt-16 pb-14 sm:pt-20">
      <div className="atmos" aria-hidden>
        <div className="aurora">
          <i />
          <i />
        </div>
      </div>

      <div className="mx-auto max-w-6xl px-4">
        <span className="label99 reveal block">
          flight_scans · a 99 instrument
        </span>

        <h1 className="reveal mt-4 max-w-3xl font-mono text-3xl font-semibold tracking-tight text-text-bright sm:text-5xl">
          Cheap flights, when your{" "}
          <span className="text-build glow-b">dates float</span>.
        </h1>

        <p className="reveal mt-5 max-w-2xl font-sans text-[15px] leading-relaxed text-text">
          Most flight sites ask the exact days you leave and return. This
          one doesn&apos;t. Give it a window and a trip length (or just a
          one-way window) and it hunts the single cheapest combination
          across thousands of date pairs, three times a week, and pings
          you when a real low appears. Built on free APIs, for €0/month.
        </p>

        <span className="aside reveal mt-3 block">
          built to catch a cheap flight to nairobi. still hunting, honestly.
        </span>

        <div className="reveal mt-7 flex flex-wrap gap-3">
          <a
            href="#radar"
            className="rounded-card border border-signature-dim bg-bg2 px-4 py-2.5 font-mono text-sm font-semibold tracking-wider text-signature hover:shadow-glow-sig"
          >
            ▶ SEE THE LIVE HUNT
          </a>
          <a
            href="mailto:pintodiazc@gmail.com?subject=flight%20scans%20invite"
            className="rounded-card border border-border-bright bg-bg2 px-4 py-2.5 font-mono text-[13px] tracking-wider text-text-bright hover:border-signature-dim"
          >
            → REQUEST AN INVITE
          </a>
        </div>

        <div className="term reveal mt-10 max-w-2xl">
          <div className="term__bar">
            <span className="dots">
              <i />
              <i />
              <i />
            </span>
            <span className="path">carlos@99 · ~/flight_scans</span>
          </div>
          <div className="term__body">
            <span className="ln ln--cmd" style={d(0)}>
              python run_batch.py
            </span>
            <span className="ln ln--info" style={d(140)}>
              ● reserving budget · predicted spend is a guaranteed upper bound
            </span>
            <span className="ln ln--ok" style={d(280)}>
              ✓ scan complete · every source under its cap
            </span>
            <span className="ln ln--ok" style={d(420)}>
              ▼ MAD→NBO 532 EUR · −11% vs baseline · alert pushed
            </span>
            <span className="ln ln--dim" style={d(580)}>
              a scan can spend less, never more · <span className="cursor">█</span>
            </span>
          </div>
        </div>
        <span className="aside--s aside reveal mt-2 block">
          one scan, replayed. the radar below is my own hunt, live.
          invite-only to run your own.
        </span>
      </div>
    </section>
  );
}
