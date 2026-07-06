import { z } from "zod";

/** Client+server validation of the canonical route-config shape —
 *  mirrors the invariants of lib/config._from_dict (the Python side
 *  re-validates on every load, so this is UX, not the security line). */

const isoDate = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, "YYYY-MM-DD");

const iata = z
  .string()
  .regex(/^[A-Z]{3}$/, "3-letter IATA code");

export const routeConfigSchema = z
  .object({
    route: z.object({
      name: z.string().min(1),
      origins: z.array(iata).min(1),
      destinations: z.array(iata).min(1),
    }),
    search_window: z.object({
      earliest_departure: isoDate,
      latest_return: isoDate,
    }),
    stay_preferences: z.object({
      min_days: z.number().int().positive(),
      max_days: z.number().int().positive(),
    }),
    currency: z.string().regex(/^[A-Z]{3}$/),
    sweep: z
      .object({
        cadence_days: z.number().int().positive().optional(),
        skip_if_min_above: z.number().int().positive().nullish(),
        skip_grace_days: z.number().int().nonnegative().nullish(),
      })
      .passthrough(),
    followup: z
      .object({
        watch_below_price: z.number().int().positive().nullish(),
        drop_above_price: z.number().int().positive().nullish(),
      })
      .optional(),
    alerts: z.object({
      drop_threshold_pct: z.number().positive(),
      baseline_window_days: z.number().int().positive(),
      min_observations: z.number().int().positive(),
    }),
  })
  .refine(
    (c) => c.search_window.latest_return > c.search_window.earliest_departure,
    { message: "latest_return must be after earliest_departure" },
  )
  .refine((c) => c.stay_preferences.max_days >= c.stay_preferences.min_days, {
    message: "max stay must be >= min stay",
  })
  .refine(
    (c) =>
      c.followup?.watch_below_price == null ||
      c.followup?.drop_above_price == null ||
      c.followup.drop_above_price >= c.followup.watch_below_price,
    { message: "drop_above_price must be >= watch_below_price" },
  );

export type RouteConfigJson = z.infer<typeof routeConfigSchema>;
