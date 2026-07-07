import { LandingHero } from "@/components/LandingHero";
import { SearchRadar } from "@/components/SearchRadar";
import { getRouteWindow } from "@/lib/queries";

// ISR safety net; scans additionally ping /api/revalidate so the page is
// fresh within seconds of new data. Data changes ~3x/week — never query
// per-request here (Turso read budget).
export const revalidate = 21600;

/** Landing = terminal hero + the owner's public demo search, rendered by
 *  the same component every /s/[slug] page uses. A live marketing screen. */
export default async function RadarPage() {
  const w = await getRouteWindow();
  return (
    <>
      <LandingHero />
      <SearchRadar w={w} />
    </>
  );
}
