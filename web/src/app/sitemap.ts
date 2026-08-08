// Sitemap (M4b): static pages + hubs + ONLY gate-passing route pages
// (seo_pages.status = 'published' — D6's quality gate; noindex routes
// never enter the sitemap).

import type { MetadataRoute } from "next";
import { db } from "@/lib/db";
import { HUBS, hubByIata } from "@/lib/hubs";

const BASE = process.env.SITE_URL ?? "https://vuelazo.es";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const now = new Date();
  const entries: MetadataRoute.Sitemap = [
    { url: `${BASE}/vuelazo`, lastModified: now, priority: 1 },
    { url: `${BASE}/vuelazos`, lastModified: now, priority: 0.9 },
    { url: `${BASE}/unete`, lastModified: now, priority: 0.9 },
    ...Object.keys(HUBS).map((slug) => ({
      url: `${BASE}/vuelos-baratos/${slug}`,
      lastModified: now,
      priority: 0.8,
    })),
    { url: `${BASE}/aviso-legal`, lastModified: now, priority: 0.1 },
    { url: `${BASE}/privacidad`, lastModified: now, priority: 0.1 },
    { url: `${BASE}/condiciones`, lastModified: now, priority: 0.1 },
  ];
  try {
    const rs = await db().execute(
      "SELECT origin, dest FROM seo_pages WHERE status = 'published'",
    );
    for (const r of rs.rows) {
      const hub = hubByIata(String(r["origin"]));
      if (!hub) continue;
      entries.push({
        url: `${BASE}/vuelos-baratos/${hub[0]}/${String(r["dest"]).toLowerCase()}`,
        lastModified: now,
        priority: 0.7,
      });
    }
  } catch {
    /* fresh DB: sitemap ships without route pages */
  }
  return entries;
}
