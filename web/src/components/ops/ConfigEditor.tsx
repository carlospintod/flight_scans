"use client";

import { useState } from "react";
import { routeConfigSchema, type RouteConfigJson } from "@/lib/config-schema";

/** Edits the fields an operator actually changes between scans; the
 *  full canonical JSON round-trips underneath so nothing is lost. */
export function ConfigEditor({ initial }: { initial: RouteConfigJson }) {
  const [cfg, setCfg] = useState(initial);
  const [state, setState] = useState<
    "idle" | "busy" | "saved" | { error: string }
  >("idle");

  function patch(fn: (draft: RouteConfigJson) => void) {
    const next = structuredClone(cfg);
    fn(next);
    setCfg(next);
    setState("idle");
  }

  async function save() {
    const parsed = routeConfigSchema.safeParse(cfg);
    if (!parsed.success) {
      setState({
        error: parsed.error.issues
          .map((i) => `${i.path.join(".")}: ${i.message}`)
          .join("; "),
      });
      return;
    }
    setState("busy");
    const r = await fetch("/api/route-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(parsed.data),
    });
    if (r.ok) {
      setState("saved");
    } else {
      const body = await r.json().catch(() => ({}));
      setState({ error: body.error ?? `HTTP ${r.status}` });
    }
  }

  const label =
    "block font-mono text-[10px] uppercase tracking-wider text-hint mb-1";
  const input =
    "w-full rounded-card border border-border bg-bg px-2.5 py-2 font-mono " +
    "text-[13px] text-text-bright outline-none focus:border-signature-dim";

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className={label}>earliest departure</label>
          <input
            type="date"
            className={input}
            value={cfg.search_window.earliest_departure}
            onChange={(e) =>
              patch((d) => (d.search_window.earliest_departure = e.target.value))
            }
          />
        </div>
        <div>
          <label className={label}>latest return</label>
          <input
            type="date"
            className={input}
            value={cfg.search_window.latest_return}
            onChange={(e) =>
              patch((d) => (d.search_window.latest_return = e.target.value))
            }
          />
        </div>
        <div>
          <label className={label}>min stay (days)</label>
          <input
            type="number"
            className={input}
            value={cfg.stay_preferences.min_days}
            onChange={(e) =>
              patch((d) => (d.stay_preferences.min_days = Number(e.target.value)))
            }
          />
        </div>
        <div>
          <label className={label}>max stay (days)</label>
          <input
            type="number"
            className={input}
            value={cfg.stay_preferences.max_days}
            onChange={(e) =>
              patch((d) => (d.stay_preferences.max_days = Number(e.target.value)))
            }
          />
        </div>
        <div>
          <label className={label}>watch below (alert bar, {cfg.currency})</label>
          <input
            type="number"
            className={input}
            value={cfg.followup?.watch_below_price ?? ""}
            onChange={(e) =>
              patch((d) => {
                d.followup = d.followup ?? {};
                d.followup.watch_below_price = e.target.value
                  ? Number(e.target.value)
                  : null;
              })
            }
          />
        </div>
        <div>
          <label className={label}>rescan only under ({cfg.currency})</label>
          <input
            type="number"
            className={input}
            value={cfg.followup?.drop_above_price ?? ""}
            onChange={(e) =>
              patch((d) => {
                d.followup = d.followup ?? {};
                d.followup.drop_above_price = e.target.value
                  ? Number(e.target.value)
                  : null;
              })
            }
          />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={state === "busy"}
          className="rounded-card border border-border-bright bg-bg2 px-4 py-2 font-mono text-[13px] font-semibold tracking-wider text-text-bright hover:border-signature-dim disabled:opacity-40"
        >
          {state === "busy" ? "SAVING..." : "SAVE CONFIG"}
        </button>
        {state === "saved" && (
          <span className="font-mono text-[12px] text-good">
            saved — next scan uses these settings
          </span>
        )}
        {typeof state === "object" && (
          <span className="font-mono text-[12px] text-red">
            {state.error}
          </span>
        )}
      </div>
      <p className="font-mono text-[11px] leading-5 text-hint">
        Saved to the cloud DB (routes.config_json). Both the scheduled scans
        and this site read it — origins, alert thresholds and cadence can be
        edited via the JSON in the repo if ever needed.
      </p>
    </div>
  );
}
