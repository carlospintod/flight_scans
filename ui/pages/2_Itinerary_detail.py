"""Itinerary detail page: price history + carrier breakdown for one
(origin, destination, departure_date, return_date) tuple.
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
    itinerary_history_chart,
    load_route_from_sidebar,
    recent_itinerary_options,
    setup_page,
)

setup_page("Itinerary detail")
route, conn = load_route_from_sidebar()

st.title("Itinerary detail")

itineraries = recent_itinerary_options(conn, route, limit=200)
if itineraries.empty:
    st.info("No itineraries in the DB yet. Run a sweep first.")
    st.stop()

# Build a friendly selectbox label.
itineraries["label"] = (
    itineraries["origin"] + " → " + itineraries["destination"]
    + "  •  " + itineraries["departure_date"] + " → " + itineraries["return_date"]
    + "  •  stay=" + itineraries["stay_days"].astype(str) + "d"
    + "  •  from " + itineraries["min_price"].astype(str) + " " + route.currency
)
chosen_label = st.selectbox(
    "Pick an itinerary",
    itineraries["label"].tolist(),
    index=0,
)
chosen = itineraries[itineraries["label"] == chosen_label].iloc[0]

origin = chosen["origin"]
destination = chosen["destination"]
dep = chosen["departure_date"]
ret = chosen["return_date"]

st.markdown(
    f"### {origin} → {destination}  ·  dep `{dep}`  ·  ret `{ret}`  ·  "
    f"stay {chosen['stay_days']} days"
)

# Price history chart (both sources).
st.subheader("Price history")
itinerary_history_chart(
    conn, route,
    origin=origin, destination=destination,
    departure_date=dep, return_date=ret,
)

# Most recent point-query results per source.
st.subheader("Latest carrier detail (point queries)")
st.caption(
    "Each row is one flight option the API returned for this exact "
    "(departure, return) pair. `rank 0` is the source's top-pick (usually "
    "cheapest); `rank 1` and `rank 2` are alternatives, ordered by the same "
    "source's relevance score. `self-transfer` means a virtual-interlining "
    "bundle (e.g. Ryanair + Kenya Airways sold as one ticket) — only Sky "
    "Scrapper flags these."
)
rows = conn.execute(
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
    (route.name, origin, destination, dep, ret,
     route.name, origin, destination, dep, ret),
).fetchall()

def _fmt_minutes(m: int | None) -> str:
    if m is None:
        return ""
    h, rem = divmod(int(m), 60)
    return f"{h}h{rem:02d}m"


if not rows:
    st.info(
        "No point-query rows for this itinerary yet. Run `followup` to get "
        "carrier, stops, and duration detail."
    )
else:
    df = pd.DataFrame([{
        "source": r["source"],
        "rank": r["rank"],
        "price": f"{r['price']} {r['currency']}",
        "carriers": r["carriers"],
        "stops": r["stops"],
        "duration": _fmt_minutes(r["total_minutes"]),
        "self-transfer": "+" if r["is_self_transfer"] else "",
    } for r in rows])
    st.dataframe(df, hide_index=True, use_container_width=True)
