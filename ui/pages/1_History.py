"""Flight tracker — History & admin page.

The analysis-over-time companion to the Search page. Here you drill into
how a specific itinerary's price has moved, review fired alerts, inspect
the Sky Scrapper departure curve and stop distribution, and manage the
per-source API quotas. Nothing here spends budget except the explicit
quota-refresh buttons.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ui._common import (  # noqa: E402
    alerts_dataframe,
    curve_chart,
    itinerary_history_chart,
    latest_quota_for_ui,
    load_route_from_sidebar,
    quota_state,
    recent_alert_count,
    recent_capture_summary,
    recent_itinerary_options,
    refresh_aviasales_quota,
    refresh_kiwi_quota,
    refresh_searchapi_quota,
    refresh_skyscanner_quota,
    setup_page,
    status_dot_row,
    stops_distribution,
    terminal_header,
    _latest_snapshot_age,
)

setup_page("History")
route, conn = load_route_from_sidebar()

terminal_header(
    "FLIGHT_TRACKER // HISTORY",
    subtitle="Price trends, alerts, and API-quota admin. No budget spent here "
             "except the quota-refresh buttons.",
)

# ============================================================================
# System status
# ============================================================================
st.markdown("## System status")
age = _latest_snapshot_age(conn, route) or "never"
capture = recent_capture_summary(conn, route)
captured_24h = capture["calendar"] + capture["curve"] + capture["point"]
alerts_7d = recent_alert_count(conn, route, days=7)
status_dot_row([
    ("Last sweep", age, "live" if age != "never" else "dim"),
    ("Rows captured (24h)", f"{captured_24h:,}", "live" if captured_24h else "dim"),
    ("Alerts (7d)", str(alerts_7d), "live" if alerts_7d else "dim"),
])

# ============================================================================
# Itinerary drill-down: price over time for one specific combo
# ============================================================================
st.markdown("## Price history")
options = recent_itinerary_options(conn, route, limit=200)
if options.empty:
    st.info("No itineraries yet. Run a search first.")
else:
    options["label"] = (
        options["origin"] + "→" + options["destination"]
        + "  ·  " + options["departure_date"] + " → " + options["return_date"]
        + "  ·  " + options["stay_days"].astype(str) + "d"
        + "  ·  from " + options["min_price"].astype(str) + " " + route.currency
    )
    chosen = st.selectbox("Pick an itinerary", options["label"].tolist(), index=0)
    row = options[options["label"] == chosen].iloc[0]
    itinerary_history_chart(
        conn, route,
        origin=row["origin"], destination=row["destination"],
        departure_date=row["departure_date"], return_date=row["return_date"],
    )
    pq = conn.execute(
        """
        SELECT pq.* FROM point_queries pq
        JOIN (
            SELECT source, MAX(snapshot_at) AS latest FROM point_queries
            WHERE route_id = ? AND origin = ? AND destination = ?
              AND departure_date = ? AND return_date = ?
            GROUP BY source
        ) m ON m.source = pq.source AND m.latest = pq.snapshot_at
        WHERE pq.route_id = ? AND pq.origin = ? AND pq.destination = ?
          AND pq.departure_date = ? AND pq.return_date = ?
        ORDER BY pq.source, pq.rank
        """,
        (route.name, row["origin"], row["destination"],
         row["departure_date"], row["return_date"],
         route.name, row["origin"], row["destination"],
         row["departure_date"], row["return_date"]),
    ).fetchall()
    if pq:
        st.markdown("**Latest carrier detail**")
        st.dataframe(pd.DataFrame([{
            "source": r["source"], "rank": r["rank"],
            "price": f"{r['price']} {r['currency']}",
            "carriers": r["carriers"], "stops": r["stops"],
            "self_xfer": "✓" if r["is_self_transfer"] else "",
        } for r in pq]), hide_index=True, use_container_width=True)
    else:
        st.caption("No carrier detail — this itinerary hasn't been followed up.")

# ============================================================================
# Recent alerts
# ============================================================================
st.markdown("## Recent alerts")
df_alerts = alerts_dataframe(conn, route, limit=30)
if df_alerts.empty:
    st.caption(
        "No alerts yet. Each itinerary needs ≥4 prior snapshots inside a "
        "30-day baseline before a drop can fire. Keep sweeping and they'll come."
    )
else:
    st.dataframe(df_alerts, hide_index=True, use_container_width=True)

# ============================================================================
# Stops distribution + Sky Scrapper curve
# ============================================================================
col_a, col_b = st.columns(2)
with col_a:
    st.markdown("## Stops")
    df_stops = stops_distribution(
        conn, route, source=None,
        min_stay=route.stay.min_days, max_stay=route.stay.max_days,
    )
    if df_stops.empty:
        st.caption("No stops data yet.")
    else:
        import altair as alt
        df_stops = df_stops.copy()
        df_stops["label"] = df_stops["stops"].apply(
            lambda s: "nonstop" if s == 0 else f"{int(s)} stop"
            + ("s" if s != 1 else "") if pd.notna(s) else "unknown")
        st.altair_chart(
            alt.Chart(df_stops).mark_bar().encode(
                x=alt.X("label:N", sort=None, title=None),
                y=alt.Y("n:Q", title="#1-cheapest count"),
                tooltip=["label", "n"],
            ).properties(height=240),
            use_container_width=True,
        )
with col_b:
    st.markdown("## Sky Scrapper curve")
    curve_chart(conn, route)

# ============================================================================
# API quota admin
# ============================================================================
st.markdown("## API quotas")

_SRC = [
    ("searchapi", "SearchAPI", refresh_searchapi_quota,
     "Calls /me (free, doesn't count against quota)."),
    ("skyscanner", "Sky Scrapper", refresh_skyscanner_quota,
     "1 searchAirport call to read the rate-limit header (costs 1 call)."),
    ("aviasales", "Aviasales", refresh_aviasales_quota,
     "1 cheap_prices call; provider exposes no rate-limit header."),
    ("kiwi", "Kiwi", refresh_kiwi_quota,
     "1 round-trip call to read RapidAPI headers."),
]

cards = []
for key, label, _fn, _help in _SRC:
    q = latest_quota_for_ui(conn, key)
    rem = q["remaining"] if q and q.get("remaining") is not None else None
    tot = q.get("limit_total") if q else None
    value = "unknown" if rem is None else (f"{rem}/{tot}" if tot and tot > rem else str(rem))
    cards.append((label, value, quota_state(rem, tot)))
status_dot_row(cards)

cols = st.columns(len(_SRC))
for (key, label, fn, help_text), col in zip(_SRC, cols):
    with col:
        if st.button(f"Refresh {label}", key=f"rq_{key}", help=help_text,
                     use_container_width=True):
            try:
                fn(conn)
                st.toast(f"{label} quota refreshed.")
            except RuntimeError as exc:
                st.warning(f"{label} disabled: {exc}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"{label} refresh failed: {exc}")
