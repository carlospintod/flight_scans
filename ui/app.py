"""Flight tracker Streamlit dashboard — entry point.

Run from the repo root:

    streamlit run ui/app.py

The app exposes four pages via Streamlit's built-in multi-page mechanism
(see `ui/pages/*.py`). This file is the landing page (Overview).
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Make the repo's `lib/` importable when streamlit launches us from
# ui/app.py — we're one level deep so the repo root is parent[1].
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ui._common import (  # noqa: E402
    budget_gauge,
    cheapest_recent_per_source,
    connect_db,
    curve_chart,
    load_route_from_sidebar,
    setup_page,
)

setup_page("Overview")
route, conn = load_route_from_sidebar()

st.title(f"Flight tracker — {route.name}")
st.caption(
    f"Origins {', '.join(route.origins)} → destinations "
    f"{', '.join(route.destinations)}. "
    f"Stay {route.stay.min_days}-{route.stay.max_days} days. "
    f"Search window {route.search_window.earliest_departure} "
    f"to {route.search_window.latest_return}. "
    f"Currency {route.currency}."
)

# --- Budget cards -------------------------------------------------------
st.subheader("API budget this month")
budget_gauge(conn, route)

# --- Cheapest deals across sources --------------------------------------
st.subheader("Cheapest deals right now (top 8 per source)")
col_a, col_b = st.columns(2)
with col_a:
    st.markdown("**SearchAPI grid (with return dates)**")
    df_sa = cheapest_recent_per_source(conn, route, source="searchapi", limit=8)
    if df_sa.empty:
        st.info("No SearchAPI data yet — run a sweep.")
    else:
        st.dataframe(df_sa, hide_index=True, use_container_width=True)

with col_b:
    st.markdown("**Sky Scrapper curve (departure-date only)**")
    df_sky = cheapest_recent_per_source(conn, route, source="skyscanner", limit=8)
    if df_sky.empty:
        st.info("No Sky Scrapper data yet — run a sweep.")
    else:
        st.dataframe(df_sky, hide_index=True, use_container_width=True)

# --- Departure curve charts ---------------------------------------------
st.subheader("Sky Scrapper departure-date pricing curve")
curve_chart(conn, route)

st.caption("Use the sidebar to navigate to other pages: Run jobs · Itinerary detail · Alerts.")
