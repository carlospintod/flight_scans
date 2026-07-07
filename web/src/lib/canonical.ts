/** Canonical JSON for routes.config_json: stable key order matching
 *  Python's json.dumps(..., sort_keys=True), so route_store's self-heal
 *  comparison stays a no-op. JSON.stringify's replacer-array applies to
 *  nested objects too — collect every key at every depth. */
export function canonicalJson(obj: unknown): string {
  const keys = new Set<string>();
  (function walk(o: unknown) {
    if (o && typeof o === "object" && !Array.isArray(o)) {
      for (const [k, v] of Object.entries(o)) {
        keys.add(k);
        walk(v);
      }
    } else if (Array.isArray(o)) {
      for (const v of o) walk(v);
    }
  })(obj);
  return JSON.stringify(obj, [...keys].sort());
}
