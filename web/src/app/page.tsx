import Script from "next/script";
import { LandingHero } from "@/components/LandingHero";
import { SearchRadar } from "@/components/SearchRadar";
import { getRouteWindow } from "@/lib/queries";

// ISR safety net; scans additionally ping /api/revalidate so the page is
// fresh within seconds of new data. Data changes ~3x/week — never query
// per-request here (Turso read budget).
export const revalidate = 21600;

/** Landing = Phosphor v4 hero (measuring field + scan replay) over the
 *  owner's public demo search, rendered by the same component every
 *  /s/[slug] page uses. A live marketing screen. phosphor.js (vendored)
 *  drives the field, reveals and the signals ticker — landing only;
 *  app views stay T1. */
export default async function RadarPage() {
  const w = await getRouteWindow();
  return (
    <>
      <LandingHero />
      <section id="radar" className="scroll-mt-6">
        <SearchRadar w={w} />
      </section>
      <Script src="/phosphor.js" strategy="afterInteractive" />
    </>
  );
}
