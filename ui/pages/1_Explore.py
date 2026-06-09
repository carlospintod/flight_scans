"""Flight tracker — Explore page.

All the data lives here. Top-down structure:

  A. Filters (sidebar): origin, source, stay range.
  B. Price heatmap — departure date × stay length, color = price.
  C. Top alternatives — sortable cheapest-first table. Click a row to
     drill into the price history + carrier detail for that itinerary.
  D. Carrier mix — bar chart of which airlines show up as #1 most often.
  E. Stop distribution — histogram of stops (nonstop / 1-stop / 2+-stops).
  F. Recent alerts.
  G. Sky Scrapper departure-curve (collapsed by default).
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ui._common import (  # noqa: E402
    alerts_dataframe,
    carrier_mix,
    curve_chart,
    itinerary_history_chart,
    latest_grid_for_heatmap,
    load_route_from_sidebar,
    setup_page,
    stops_distribution,
    terminal_header,
    top_alternatives,
)

setup_page("Explore")
route, conn = load_route_from_sidebar()

terminal_header(
    f"EXPLORE // {route.name}",
    subtitle="Slice the data however you want. Every chart respects the sidebar filters.",
)

# ---- Sidebar filters --------------------------------------------------------
with st.sidebar:
    st.markdown("---")
    st.markdown("### Filters")
    sel_origin = st.selectbox("Origin", list(route.origins), index=0)
    src_choice = st.selectbox(
        "Data source", ["searchapi", "skyscanner", "both"], index=0,
        help=(
            "SearchAPI has full (dep, ret) detail. Sky Scrapper has only "
            "departure-date pricing — pick it if you've been running it."
        ),
    )
    src_arg = None if src_choice == "both" else src_choice
    min_s, max_s = st.slider(
        "Stay length (days)",
        min_value=1, max_value=120,
        value=(route.stay.min_days, route.stay.max_days),
        help="Pre-filled from YAML; tighten to see only what you'd actually book.",
    )

# ---- A. Price heatmap -------------------------------------------------------
st.markdown("## A · Price heatmap")
st.caption(
    "Where the cheap pockets are. **X = departure date**, **Y = stay length** "
    "(days on the ground). Color = price (darker = cheaper). Hover for the exact value."
)

if src_choice == "skyscanner":
    st.info(
        "Sky Scrapper only provides departure-date pricing without a return "
        "date, so the heatmap can't be drawn from it. Switch to **searchapi** "
        "or **both** in the sidebar."
    )
else:
    df_heat = latest_grid_for_heatmap(
        conn, route,
        origin=sel_origin, source="searchapi",
        min_stay=min_s, max_stay=max_s,
    )
    if df_heat.empty:
        st.info(
            "No prices in this filter window yet — widen the stay range, "
            "switch origin, or run a sweep on the Run page."
        )
    else:
        df_heat["departure_date"] = pd.to_datetime(df_heat["departure_date"])
        aggregate_weekly = False
        if len(df_heat) > 500:
            aggregate_weekly = st.toggle(
                "Aggregate by week (a lot of cells — improves legibility)",
                value=True,
            )
        x_enc = alt.X(
            "departure_date:T",
            title="Departure date",
            timeUnit="yearweek" if aggregate_weekly else "yearmonthdate",
        )
        heat = (
            alt.Chart(df_heat)
            .mark_rect()
            .encode(
                x=x_enc,
                y=alt.Y("stay_days:O", title="Stay (days)"),
                color=alt.Color(
                    "price:Q",
                    title=f"Price ({route.currency})",
                    scale=alt.Scale(scheme="viridis", reverse=True),
                ),
                tooltip=[
                    alt.Tooltip("departure_date:T", title="Departure"),
                    alt.Tooltip("stay_days:O", title="Stay (days)"),
                    alt.Tooltip("price:Q", title=f"Price ({route.currency})", format=",.0f"),
                ],
            )
            .properties(height=380)
        )
        st.altair_chart(heat, use_container_width=True)
        st.caption(
            f"Lowest in view: **{int(df_heat['price'].min())} {route.currency}**  ·  "
            f"highest: **{int(df_heat['price'].max())} {route.currency}**  ·  "
            f"median: **{int(df_heat['price'].median())} {route.currency}**  ·  "
            f"{len(df_heat):,} cells"
        )

# ---- B. Top alternatives ----------------------------------------------------
st.markdown("## B · Top alternatives (sortable)")
st.caption(
    "The cheapest currently-recorded itineraries in your stay range. "
    "Click any row to see its price history + carrier detail below."
)

df_alts = top_alternatives(
    conn, route,
    source=src_arg, min_stay=min_s, max_stay=max_s,
    origin=sel_origin if src_choice != "both" else None,
    limit=20,
)
if df_alts.empty:
    st.info("No itineraries match this filter. Try widening the stay range.")
else:
    # Pretty up display columns
    display = df_alts.copy()
    if "total_minutes" in display.columns:
        display["duration"] = display["total_minutes"].apply(
            lambda m: f"{int(m)//60}h{int(m)%60:02d}m" if pd.notna(m) else ""
        )
        display = display.drop(columns=["total_minutes"])
    if "is_self_transfer" in display.columns:
        display["self_xfer"] = display["is_self_transfer"].apply(
            lambda v: "yes" if v else ""
        )
        display = display.drop(columns=["is_self_transfer"])
    selected = st.dataframe(
        display, hide_index=True, use_container_width=True,
        on_select="rerun", selection_mode="single-row",
    )
    # Drill-down
    sel_rows = (selected or {}).get("selection", {}).get("rows", []) if isinstance(selected, dict) else []
    if sel_rows:
        row = df_alts.iloc[sel_rows[0]]
        if pd.isna(row.get("return_date")):
            st.info(
                "Sky Scrapper rows don't carry a return date, so per-itinerary "
                "price history isn't available for this row. Pick a SearchAPI row instead."
            )
        else:
            st.markdown(
                f"### Drill-down: {row['origin']} → {row['destination']}  ·  "
                f"dep `{row['departure_date']}` · ret `{row['return_date']}`  ·  "
                f"stay {int(row['stay_days'])} days"
            )
            itinerary_history_chart(
                conn, route,
                origin=row["origin"], destination=row["destination"],
                departure_date=row["departure_date"],
                return_date=row["return_date"],
            )
            # Latest carrier detail per source for this itinerary
            pq = conn.execute(
                """
                SELECT pq.* FROM point_queries pq
                JOIN (
                    SELECT source, MAX(snapshot_at) AS latest
                    FROM point_queries
                    WHERE route_id = ? AND origin = ? AND destination = ?
                      AND departure_date = ? AND return_date = ?
                    GROUP BY source
                ) m ON m.source = pq.source AND m.latest = pq.snapshot_at
                WHERE pq.route_id = ? AND pq.origin = ? AND pq.destination = ?
                  AND pq.departure_date = ? AND pq.return_date = ?
                ORDER BY pq.source ASC, pq.rank ASC
                """,
                (route.name, row["origin"], row["destination"],
                 row["departure_date"], row["return_date"],
                 route.name, row["origin"], row["destination"],
                 row["departure_date"], row["return_date"]),
            ).fetchall()
            if not pq:
                st.caption(
                    "No carrier detail yet — followup hasn't picked this itinerary."
                )
            else:
                st.markdown("**Latest carrier detail**")
                st.dataframe(
                    pd.DataFrame([{
                        "source": r["source"],
                        "rank": r["rank"],
                        "price": f"{r['price']} {r['currency']}",
                        "carriers": r["carriers"],
                        "stops": r["stops"],
                        "duration": (
                            f"{int(r['total_minutes'])//60}h"
                            f"{int(r['total_minutes'])%60:02d}m"
                            if r["total_minutes"] else ""
                        ),
                        "self_xfer": "yes" if r["is_self_transfer"] else "",
                    } for r in pq]),
                    hide_index=True, use_container_width=True,
                )

# ---- C. Carrier mix --------------------------------------------------------
st.markdown("## C · Carrier mix")
st.caption(
    "Which airlines (or airline combinations) appear most often as the #1 "
    "cheapest option across recently-tracked itineraries."
)

df_carr = carrier_mix(conn, route, source=src_arg, min_stay=min_s, max_stay=max_s)
if df_carr.empty:
    st.info(
        "No carrier data yet — run a followup on the Run page to populate."
    )
else:
    chart = (
        alt.Chart(df_carr)
        .mark_bar()
        .encode(
            y=alt.Y("carriers:N", sort="-x", title=None),
            x=alt.X("n:Q", title="Times appeared as #1"),
            tooltip=["carriers", "n"],
        )
        .properties(height=max(180, 24 * len(df_carr) + 40))
    )
    st.altair_chart(chart, use_container_width=True)

# ---- D. Stops distribution -------------------------------------------------
st.markdown("## D · Stops distribution")
st.caption(
    "How often the cheapest option is nonstop vs one-stop vs two-stops."
)

df_stops = stops_distribution(conn, route, source=src_arg,
                              min_stay=min_s, max_stay=max_s)
if df_stops.empty:
    st.info("No stops data yet.")
else:
    df_stops = df_stops.copy()
    df_stops["label"] = df_stops["stops"].apply(
        lambda s: "nonstop" if s == 0 else f"{int(s)} stop" + ("s" if s != 1 else "")
        if pd.notna(s) else "unknown"
    )
    chart = (
        alt.Chart(df_stops)
        .mark_bar()
        .encode(
            x=alt.X("label:N", title=None, sort=None),
            y=alt.Y("n:Q", title="#1-cheapest itineraries"),
            tooltip=["label", "n"],
        )
        .properties(height=240)
    )
    st.altair_chart(chart, use_container_width=True)

# ---- E. Recent alerts ------------------------------------------------------
st.markdown("## E · Recent alerts")
df_alerts = alerts_dataframe(conn, route, limit=20,
                             sources=[src_arg] if src_arg else None)
if df_alerts.empty:
    st.caption(
        "No alerts fired yet. Each itinerary needs ≥4 prior snapshots inside "
        "a 30-day baseline window before the engine can compare. Keep "
        "sweeping every 2 weeks and they'll start firing once history accumulates."
    )
else:
    st.dataframe(df_alerts, hide_index=True, use_container_width=True)

# ---- F. Sky Scrapper curve -------------------------------------------------
with st.expander("Sky Scrapper departure-date pricing curve", expanded=False):
    curve_chart(conn, route)
