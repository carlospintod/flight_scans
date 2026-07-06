import { createClient, type Client } from "@libsql/client";

/**
 * One Turso client per server process — the same database the Python
 * scan pipeline writes to (lib/turso_http.py speaks the same libsql
 * protocol). The web app is a READER plus exactly one writer surface
 * (routes.config_json, via /ops).
 */
let client: Client | null = null;

export function db(): Client {
  if (client) return client;
  const url = process.env.TURSO_DATABASE_URL;
  const authToken = process.env.TURSO_AUTH_TOKEN;
  if (!url || !authToken) {
    throw new Error(
      "TURSO_DATABASE_URL / TURSO_AUTH_TOKEN not set — configure them in " +
        ".env.local (dev) or the Vercel project env (prod).",
    );
  }
  client = createClient({ url, authToken });
  return client;
}
