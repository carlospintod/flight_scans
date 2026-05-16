"""Alerts page: chronological list of fired alerts with filters."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ui._common import load_route_from_sidebar, setup_page  # noqa: E402

setup_page("Alerts")
route, conn = load_route_from_sidebar()

st.title("Alerts")
st.caption(
    "Fires when a price is at least drop_threshold_pct% below the "
    "trailing-baseline_window_days median for the same itinerary "
    "(per source), provided we have ≥ min_observations prior snapshots."
)

with st.sidebar:
    st.markdown("### Filters")
    sources = st.multiselect(
        "Source", ["searchapi", "skyscanner"],
        default=["searchapi", "skyscanner"],
    )
    limit = st.slider("Max rows", 10, 200, 50)

rows = conn.execute(
    f"""
    SELECT * FROM alerts
    WHERE route_id = ?
      AND source IN ({','.join('?' * len(sources)) or "''"})
    ORDER BY fired_at DESC
    LIMIT ?
    """,
    (route.name, *sources, limit),
).fetchall()

if not rows:
    st.info("No alerts in the DB. Either no drops have fired or there's not enough history yet.")
    st.stop()

df = pd.DataFrame([{
    "fired_at": r["fired_at"],
    "source": r["source"],
    "origin": r["origin"],
    "destination": r["destination"],
    "departure": r["departure_date"],
    "return": r["return_date"],
    "price": f"{r['price']} {r['currency']}",
    "baseline": r["baseline_median"],
    "drop %": round(r["drop_pct"], 1),
} for r in rows])

st.dataframe(df, hide_index=True, use_container_width=True)
