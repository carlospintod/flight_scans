/** Terminal-styled landing hero: a fake terminal window whose "output"
 *  is a little ASCII jet flying a dashed route, plus the pitch. Sits
 *  above the live demo radar on `/`. Pure CSS animation, no JS. */
export function LandingHero() {
  return (
    <section className="mb-12">
      <div className="overflow-hidden rounded-card border border-line bg-bg-2">
        {/* terminal title bar */}
        <div className="flex items-center gap-2 border-b border-line bg-bg-3 px-3 py-2">
          <span className="h-2.5 w-2.5 rounded-full bg-danger/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-amber/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-matrix/70" />
          <span className="ml-2 font-mono text-[11px] tracking-wider text-fg-dim">
            flight_scans — departures.sh
          </span>
        </div>

        <div className="px-5 py-6 sm:px-8 sm:py-8">
          {/* boot line */}
          <p className="font-mono text-[12px] text-fg-dim">
            <span className="text-matrix">$</span> ./scan --flexible
            --cheapest <span className="cursor-blink" />
          </p>

          {/* animated flight path */}
          <div className="relative my-6 h-10 select-none">
            <div className="absolute inset-x-0 top-1/2 flex items-center justify-between font-mono text-[11px] text-fg-mid">
              <span>MAD</span>
              <span className="mx-3 flex-1 border-t border-dashed border-line-bright" />
              <span>NBO</span>
            </div>
            <pre
              aria-hidden
              className="plane absolute top-0 left-0 font-mono text-[13px] leading-[1.15] text-matrix [text-shadow:0_0_10px_rgb(0_255_65/0.5)]"
            >{String.raw`  __|__
>=(_)=>
`}</pre>
          </div>

          <h1 className="font-mono text-2xl font-semibold tracking-tight text-fg-bright sm:text-3xl">
            Cheap flights, when your{" "}
            <span className="text-matrix">dates float</span>.
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-fg">
            Every flight site asks the exact days you leave and return.
            This one doesn&apos;t. Give it a window and a trip length —
            or just a one-way window — and it hunts the single cheapest
            combination across thousands of date pairs, three times a
            week, and pings you when a real low appears. Built on free
            APIs, for €0/month.
          </p>
          <p className="mt-4 font-mono text-[11px] text-fg-dim">
            live demo below: the maker&apos;s own MAD/BCN → Nairobi hunt.
            invite-only to run your own.
          </p>
        </div>
      </div>

      <style>{`
        @keyframes fly {
          0%   { transform: translateX(-8%)  translateY(2px); opacity: 0; }
          8%   { opacity: 1; }
          92%  { opacity: 1; }
          100% { transform: translateX(720%) translateY(-2px); opacity: 0; }
        }
        .plane { animation: fly 6s linear infinite; }
        @media (prefers-reduced-motion: reduce) {
          .plane { animation: none; left: 42%; }
        }
      `}</style>
    </section>
  );
}
