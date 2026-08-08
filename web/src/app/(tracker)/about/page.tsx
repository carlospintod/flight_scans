import { Card, SectionHeading } from "@/components/Section";

export const revalidate = false; // fully static

export const metadata = {
  title: "About — flight_scans",
  description:
    "How a €0/month flexible-date flight tracker works: the date rectangle, " +
    "four free data layers at different frequencies, a spend ledger, and " +
    "alerts when a price drops below its own history.",
};

/** The engine's four layers, in plain language. Budgets are the real
 *  per-tier numbers; keep in sync with lib/sources.py when the roster
 *  changes (the /ops health panel is the live view; this page is the
 *  explainer). */
const LAYERS = [
  {
    name: "The map",
    source: "SearchAPI.io · calendar engine",
    budget: "finite credits, metered",
    what:
      "One API call prices an entire 175-combination block of the rectangle " +
      "at once. About 28 calls photograph the whole thing — every departure " +
      "day at every stay length. Runs every other Saturday, because the " +
      "credits are finite and the ledger makes sure a sweep never runs " +
      "unless it can afford the full picture.",
  },
  {
    name: "The pulse",
    source: "SerpApi · managed Google Flights",
    budget: "250 searches/mo, free",
    what:
      "Every scan, it prices a small rotating sample of dates live — " +
      "different dates and different stay lengths each time, chosen so the " +
      "samples walk the whole rectangle over a few weeks. It also asks " +
      "Google which seller has each cheapest fare: sometimes an online " +
      "agency undercuts the airline's own price.",
  },
  {
    name: "The tracker",
    source: "Google Flights · headless browser",
    budget: "free, best-effort",
    what:
      "Every combination that has ever been seen cheap goes on a watchlist. " +
      "Three times a week a headless browser re-prices up to 25 of them, " +
      "building the price-over-time history the alerts need. When Google " +
      "blocks the robot (it happens), the other layers carry the scan and " +
      "the health monitor says so — nothing fails silently.",
  },
  {
    name: "The rumor mill",
    source: "Aviasales cache · Travelpayouts",
    budget: "free, unmetered",
    what:
      "Cached prices left behind by other travelers' searches. Cheap to " +
      "read, never trusted as bookable — a rumor is a lead to verify on a " +
      "live layer, not an answer.",
  },
] as const;

/** Tiny illustrative rectangle: departure day × stay length. Static and
 *  stylized — the real heatmap on the radar is the live version. */
function MiniRectangle() {
  // A hand-tuned price surface: one cheap pocket, edges pricier.
  const rows = [
    [3, 3, 2, 2, 3, 4, 4, 5],
    [3, 2, 1, 1, 2, 3, 4, 4],
    [2, 1, 0, 1, 2, 3, 3, 4],
    [3, 2, 1, 2, 3, 3, 4, 5],
    [4, 3, 2, 3, 3, 4, 5, 5],
  ];
  const shades = [
    "bg-good text-bg2 font-semibold",
    "bg-good/60",
    "bg-good/30",
    "bg-bg3",
    "bg-bg3/60",
    "bg-bg3/30",
  ];
  return (
    <div className="font-mono text-[10px] text-hint">
      <div className="mb-1 text-[11px]">
        stay length ↓ · departure day →
      </div>
      <div className="space-y-0.5">
        {rows.map((cells, ri) => (
          <div key={ri} className="flex gap-0.5">
            {cells.map((v, ci) => (
              <div
                key={ci}
                className={`flex h-6 w-9 items-center justify-center rounded-[3px] ${shades[v]}`}
              >
                {v === 0 ? "555" : ""}
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="mt-1.5 text-[11px]">
        every cell is one bookable trip · the green pocket is what the
        engine exists to find
      </div>
    </div>
  );
}

export default function AboutPage() {
  return (
    <div className="max-w-3xl space-y-10">
      <section className="space-y-3">
        <h1 className="font-mono text-2xl text-text-bright">
          Flexible on both ends
        </h1>
        <p className="leading-relaxed">
          Every flight search engine asks <em>when do you leave, when do you
          come back</em>. This tracker answers a different question:{" "}
          <span className="text-text-bright">
            what is the cheapest round trip where the departure floats inside
            a window, the return floats inside a window, and the stay is
            bounded by a minimum and maximum?
          </span>{" "}
          No mainstream tool offers that search — so this one runs it
          automatically, three times a week, for a running cost of{" "}
          <span className="text-good">€0/month</span>.
        </p>
      </section>

      <section className="space-y-3">
        <SectionHeading>The date rectangle</SectionHeading>
        <p className="leading-relaxed">
          Picture a grid. Every column is a possible departure day; every row
          is a trip length you would accept. Each cell of that grid is one
          concrete, bookable trip — depart on <em>this</em> day, come back{" "}
          <em>that</em> many days later — with its own price, moving
          independently of its neighbours.
        </p>
        <Card>
          <MiniRectangle />
        </Card>
        <p className="leading-relaxed">
          For a real search — say a two-month window of departures and stays
          between 60 and 90 days — that rectangle holds{" "}
          <span className="text-text-bright">
            a few thousand cells per origin airport
          </span>
          . The cheapest one is rarely where you would guess: it might be a
          75-day stay departing on a random Tuesday. Checking every cell
          every day would take tens of thousands of API calls — impossible on
          free tiers. Checking only a few obvious cells means the deal hides
          in the ones you skipped. The whole design of the engine is an
          answer to that tension.
        </p>
      </section>

      <section className="space-y-3">
        <SectionHeading>Four instruments, four frequencies</SectionHeading>
        <p className="leading-relaxed">
          The trick is that no single (free) data source can watch the whole
          rectangle all the time — but four of them, each doing what it is
          cheap at, can. A wide slow camera, a fast narrow probe, a patient
          tracker, and a gossip column:
        </p>
        <div className="space-y-3">
          {LAYERS.map((l) => (
            <Card key={l.name}>
              <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-mono text-[13px] font-semibold text-text-bright">
                  {l.name}
                </span>
                <span className="font-mono text-[11px] text-hint">
                  {l.source} · <span className="text-good">{l.budget}</span>
                </span>
              </div>
              <p className="text-[13px] leading-relaxed text-text">{l.what}</p>
            </Card>
          ))}
        </div>
        <p className="leading-relaxed">
          Together: the <span className="text-text-bright">map</span> finds
          the cheap pockets wherever they hide, the{" "}
          <span className="text-text-bright">pulse</span> keeps fresh live
          prices flowing between sweeps, the{" "}
          <span className="text-text-bright">tracker</span> watches every
          known-cheap cell closely enough to catch the moment it dips, and
          the <span className="text-text-bright">rumor mill</span> tips off
          the others for free. Retired from the roster: a Kiwi proxy that
          silently hit a payment wall — the incident that led to the health
          monitor below.
        </p>
      </section>

      <section className="space-y-3">
        <SectionHeading>Spending discipline</SectionHeading>
        <Card className="font-mono text-[13px] leading-7">
          <ol className="list-inside list-decimal space-y-1 text-text">
            <li>
              Before any scan spends anything, a{" "}
              <span className="text-cyan99">quota ledger</span> plans and
              reserves every API call. The predicted cost is a{" "}
              <span className="text-text-bright">guaranteed upper bound</span>{" "}
              — a scan can spend less, never more.
            </li>
            <li>
              If a budget can&apos;t cover a layer, that layer is dropped and
              the scan runs on the rest — degraded, announced, never dead.
            </li>
            <li>
              A health monitor classifies every source after every scan
              (live, degraded, dark, payment-walled…) and pushes a phone
              alert on any transition.{" "}
              <span className="text-text-bright">
                Nothing fails silently
              </span>{" "}
              — a lesson paid for once.
            </li>
            <li>
              Alerts compare each cell to its own history: a{" "}
              <span className="text-good">new all-time low</span> or a{" "}
              <span className="text-cyan99">15% drop</span> below the 30-day
              median pings a phone via ntfy.sh. This site reads the same
              database, seconds after each scan.
            </li>
          </ol>
        </Card>
      </section>

      <section>
        <SectionHeading>Honest limits</SectionHeading>
        <ul className="list-inside list-disc space-y-1 leading-relaxed text-text">
          <li>
            This is <span className="text-text-bright">not a booking site</span>
            . Prices are observations, sometimes hours or days old — every row
            links out to check the fare live before you pay.
          </li>
          <li>
            When an online agency shows the cheapest price, treat it as a
            teaser: fees and baggage can close the gap at checkout. The
            airline&apos;s own price is the honest reference.
          </li>
          <li>
            Some layers scrape public pages politely; volumes stay at tens of
            queries per scan.
          </li>
          <li>
            Saudia famously doesn&apos;t appear in Google Flights on this
            corridor — a known blind spot.
          </li>
        </ul>
      </section>

      <section className="font-mono text-[12px] text-hint">
        Built in the open —{" "}
        <a
          href="https://github.com/carlospintod/flight_scans"
          className="text-text-mid hover:text-signature"
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
