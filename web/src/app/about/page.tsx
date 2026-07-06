import { Card, SectionHeading } from "@/components/Section";

export const revalidate = false; // fully static

export const metadata = {
  title: "About — flight_scans",
  description:
    "How a $0/month flexible-date flight tracker works: free data sources, " +
    "GitHub Actions scans, Turso storage, alerts.",
};

const SOURCES = [
  ["Google Flights (direct)", "free, unmetered", "Primary verification: a headless browser renders public result pages on the GitHub Actions runner and parses prices, carriers, stops."],
  ["Kiwi (RapidAPI)", "300 calls/mo", "Discovery: one range-search call sweeps a multi-week departure band and returns the ~50 cheapest itineraries."],
  ["SerpAPI", "100 searches/mo", "Managed Google Flights verification — the fallback rail that works from any IP."],
  ["Aviasales", "soft-unlimited", "Bonus signal from a different fare ecosystem."],
  ["SearchAPI.io", "one-time credits", "Break-glass: the last credits are reserved for booking-day verification."],
] as const;

export default function AboutPage() {
  return (
    <div className="max-w-3xl space-y-10">
      <section className="space-y-3">
        <h1 className="font-mono text-2xl text-fg-bright">
          Flexible on both ends
        </h1>
        <p className="leading-relaxed">
          Every flight search engine asks <em>when do you leave, when do you
          come back</em>. This tracker answers a different question:{" "}
          <span className="text-fg-bright">
            what is the cheapest round trip where the departure floats inside
            a window, the return floats inside a window, and the stay is
            bounded by a minimum and maximum?
          </span>{" "}
          For a 60–90 day trip somewhere in a four-month window, that is
          thousands of date combinations — too many to check by hand, and not
          a search any mainstream tool offers.
        </p>
        <p className="leading-relaxed">
          So this scans them automatically, 3× a week, stores every price it
          sees, and alerts when a combination drops below its own history —
          for a running cost of <span className="text-matrix">€0/month</span>.
        </p>
      </section>

      <section>
        <SectionHeading>How it works</SectionHeading>
        <Card className="font-mono text-[13px] leading-7">
          <ol className="list-inside list-decimal space-y-1 text-fg">
            <li>
              A <span className="text-cyan">GitHub Actions</span> cron job
              wakes up Mon/Wed/Sat and plans the scan: which date bands to
              discover, which known-cheap pairs to re-verify.
            </li>
            <li>
              Free sources fill the grid (table below); every observation
              lands in a <span className="text-cyan">Turso</span> database
              with its timestamp and source.
            </li>
            <li>
              Alert rules compare each itinerary to its own history — a{" "}
              <span className="text-matrix">new all-time low</span> or a{" "}
              <span className="text-cyan">15% drop</span> below the 30-day
              median fires a push notification to a phone via{" "}
              <span className="text-cyan">ntfy.sh</span>.
            </li>
            <li>
              This site reads the same database and regenerates within
              seconds of each scan.
            </li>
          </ol>
        </Card>
      </section>

      <section>
        <SectionHeading>Data sources (all free)</SectionHeading>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-mono text-[12px]">
            <thead>
              <tr className="border-b border-line-bright text-left text-[10px] uppercase tracking-wider text-fg-dim">
                <th className="py-2 pr-4">Source</th>
                <th className="py-2 pr-4">Budget</th>
                <th className="py-2">Role</th>
              </tr>
            </thead>
            <tbody>
              {SOURCES.map(([name, budget, role]) => (
                <tr key={name} className="border-b border-line align-top">
                  <td className="py-2 pr-4 whitespace-nowrap text-fg-bright">
                    {name}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap text-matrix-dim">
                    {budget}
                  </td>
                  <td className="py-2 text-fg">{role}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <SectionHeading>Honest limits</SectionHeading>
        <ul className="list-inside list-disc space-y-1 leading-relaxed text-fg">
          <li>
            This is <span className="text-fg-bright">not a booking site</span>
            . Prices are observations, sometimes hours or days old — always
            re-check on Google Flights or the airline before paying.
          </li>
          <li>
            Some sources scrape public pages politely; volumes are kept to
            tens of queries per scan.
          </li>
          <li>
            Saudia famously doesn&apos;t appear in Google Flights on this
            corridor — a known blind spot.
          </li>
        </ul>
      </section>

      <section className="font-mono text-[12px] text-fg-dim">
        Built in the open —{" "}
        <a
          href="https://github.com/carlospintod/flight_scans"
          className="text-matrix-dim hover:text-matrix"
          target="_blank"
          rel="noreferrer"
        >
          github.com/carlospintod/flight_scans
        </a>
        . Python scan pipeline + Next.js radar, one Turso database in the
        middle.
      </section>
    </div>
  );
}
