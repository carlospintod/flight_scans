"""Shared helpers for the Streamlit UI: DB connection, charts, dataframes."""

from __future__ import annotations

import sqlite3
from dataclasses import replace as dc_replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from lib import config as config_mod
from lib import db as db_mod
from lib.searchapi_io import SOURCE_ID as SEARCHAPI_SOURCE
from lib.skyscanner_rapidapi import SOURCE_ID as SKYSCANNER_SOURCE

REPO = Path(__file__).resolve().parents[1]
ROUTES_DIR = REPO / "routes"
DEFAULT_DB = REPO / "data" / "tracker.db"


def setup_page(title: str) -> None:
    st.set_page_config(
        page_title=f"Flight tracker — {title}",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def list_routes() -> list[str]:
    return sorted(p.stem for p in ROUTES_DIR.glob("*.yaml"))


def apply_overrides(
    route: config_mod.RouteConfig,
    *,
    earliest_departure: date | None = None,
    latest_return: date | None = None,
    min_stay_days: int | None = None,
    max_stay_days: int | None = None,
    origins: tuple[str, ...] | None = None,
    destinations: tuple[str, ...] | None = None,
) -> config_mod.RouteConfig:
    """Return a copy of `route` with the given fields overridden.

    Validates the resulting object minimally — invalid combos (e.g.
    latest_return before earliest_departure) raise ValueError before
    any sweep planning would silently produce zero windows.
    """
    sw = route.search_window
    stay = route.stay
    new_earliest = earliest_departure or sw.earliest_departure
    new_latest = latest_return or sw.latest_return
    if new_latest <= new_earliest:
        raise ValueError(
            f"latest_return ({new_latest}) must be after "
            f"earliest_departure ({new_earliest})"
        )
    new_min_stay = min_stay_days if min_stay_days is not None else stay.min_days
    new_max_stay = max_stay_days if max_stay_days is not None else stay.max_days
    if new_max_stay < new_min_stay:
        raise ValueError(
            f"max_stay ({new_max_stay}) must be >= min_stay ({new_min_stay})"
        )
    new_origins = origins or route.origins
    new_destinations = destinations or route.destinations
    if not new_origins or not new_destinations:
        raise ValueError("at least one origin and one destination are required")

    return dc_replace(
        route,
        origins=tuple(new_origins),
        destinations=tuple(new_destinations),
        search_window=dc_replace(
            sw,
            earliest_departure=new_earliest,
            latest_return=new_latest,
        ),
        stay=dc_replace(stay, min_days=new_min_stay, max_days=new_max_stay),
    )


def load_route_from_sidebar() -> tuple[config_mod.RouteConfig, sqlite3.Connection]:
    """Sidebar route picker + DB connection. Both are cached per session."""
    routes = list_routes()
    if not routes:
        st.error(f"No route YAML files in {ROUTES_DIR}")
        st.stop()
    with st.sidebar:
        st.markdown("### Route")
        name = st.selectbox("Pick route", routes, index=0, key="route_name")
        st.caption(f"DB: `{DEFAULT_DB}`")
    route = config_mod.load_route(ROUTES_DIR / f"{name}.yaml")
    conn = connect_db()
    return route, conn


@st.cache_resource(show_spinner=False)
def connect_db() -> sqlite3.Connection:
    """Per-process DB connection. Streamlit reruns the script on each
    user action but `cache_resource` keeps this connection alive."""
    DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DEFAULT_DB, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    db_mod.ensure_schema(conn)
    return conn


# --- Budget gauge -----------------------------------------------------------


def budget_gauge(conn: sqlite3.Connection, route) -> None:
    """Show approximate API budget used this calendar month per source.

    SearchAPI calls aren't logged by the tracker explicitly, but each
    sweep records a snapshot per window; we approximate "calls" as
    "distinct (window, snapshot_at) groups this month". For Sky Scrapper
    we count distinct (origin, destination, snapshot_at) curve groups +
    distinct (itinerary, snapshot_at) point-query groups.

    This is a heuristic — the real source of truth is the provider's
    own dashboard. The gauge is here to remind you, not to gate.
    """
    today = datetime.now(timezone.utc)
    month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_iso = month_start.strftime("%Y-%m-%dT%H:%M:%SZ")

    sa_calls = conn.execute(
        """
        SELECT COUNT(DISTINCT snapshot_at || '|' || origin || '|' || destination)
        FROM calendar_snapshots
        WHERE route_id = ? AND source = ? AND snapshot_at >= ?
        """,
        (route.name, SEARCHAPI_SOURCE, month_start_iso),
    ).fetchone()[0]

    sa_point = conn.execute(
        """
        SELECT COUNT(DISTINCT snapshot_at || '|' || origin || '|' || destination
                               || '|' || departure_date || '|' || return_date)
        FROM point_queries
        WHERE route_id = ? AND source = ? AND snapshot_at >= ?
        """,
        (route.name, SEARCHAPI_SOURCE, month_start_iso),
    ).fetchone()[0]
    sa_total = sa_calls + sa_point

    sky_curve = conn.execute(
        """
        SELECT COUNT(DISTINCT snapshot_at || '|' || origin || '|' || destination)
        FROM departure_curves
        WHERE route_id = ? AND source = ? AND snapshot_at >= ?
        """,
        (route.name, SKYSCANNER_SOURCE, month_start_iso),
    ).fetchone()[0]
    sky_point = conn.execute(
        """
        SELECT COUNT(DISTINCT snapshot_at || '|' || origin || '|' || destination
                               || '|' || departure_date || '|' || return_date)
        FROM point_queries
        WHERE route_id = ? AND source = ? AND snapshot_at >= ?
        """,
        (route.name, SKYSCANNER_SOURCE, month_start_iso),
    ).fetchone()[0]
    # Each Sky Scrapper point query averages 1-2 calls (kickoff + 1 poll).
    sky_total = sky_curve + sky_point * 2

    col1, col2, col3 = st.columns(3)
    col1.metric("SearchAPI calls (this month)", f"{sa_total} / 100")
    col2.metric("Sky Scrapper calls (this month)", f"~{sky_total} / 100")
    col3.metric(
        "Last sweep",
        _latest_snapshot_age(conn, route) or "never",
    )


def _latest_snapshot_age(conn, route) -> str | None:
    row = conn.execute(
        """
        SELECT MAX(snapshot_at) FROM (
            SELECT snapshot_at FROM calendar_snapshots WHERE route_id = ?
            UNION ALL
            SELECT snapshot_at FROM departure_curves WHERE route_id = ?
        )
        """,
        (route.name, route.name),
    ).fetchone()[0]
    if not row:
        return None
    try:
        # Tolerate both `Z` and `+00:00` suffixes.
        s = row.replace("Z", "+00:00")
        ts = datetime.fromisoformat(s)
        # Make sure ts is tz-aware
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return row
    delta = datetime.now(timezone.utc) - ts
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return "<1h ago"
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


# --- Cheapest dataframes ----------------------------------------------------


def cheapest_recent_per_source(
    conn: sqlite3.Connection, route, *, source: str, limit: int = 8,
) -> pd.DataFrame:
    if source == SEARCHAPI_SOURCE:
        rows = db_mod.cheapest_recent_itineraries(
            conn, route.name,
            min_stay=route.stay.min_days,
            max_stay=route.stay.max_days,
            source=SEARCHAPI_SOURCE,
            limit=limit,
        )
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(
            [{
                "origin": r["origin"],
                "destination": r["destination"],
                "departure": r["departure_date"],
                "return": r["return_date"],
                "stay (d)": r["stay_days"],
                "price": f"{r['price']} {r['currency']}",
                "lowest?": "★" if r["is_lowest_price"] else "",
            } for r in rows]
        )
    # SKYSCANNER_SOURCE — pull from departure_curves
    rows = conn.execute(
        """
        SELECT * FROM departure_curves
        WHERE route_id = ?
          AND source = ?
          AND snapshot_at = (
              SELECT MAX(snapshot_at) FROM departure_curves
              WHERE route_id = ? AND source = ?
          )
        ORDER BY price ASC
        LIMIT ?
        """,
        (route.name, SKYSCANNER_SOURCE, route.name, SKYSCANNER_SOURCE, limit),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        [{
            "origin": r["origin"],
            "destination": r["destination"],
            "departure": r["departure_date"],
            "group": r["price_group"] or "",
            "price": f"{r['price']:.2f} {r['currency']}",
        } for r in rows]
    )


# --- Curve chart ------------------------------------------------------------


def curve_chart(conn: sqlite3.Connection, route) -> None:
    """Altair chart of the latest Sky Scrapper curve per origin-destination."""
    frames: list[pd.DataFrame] = []
    for origin in route.origins:
        for destination in route.destinations:
            rows = db_mod.latest_curve(
                conn, route.name,
                origin=origin, destination=destination,
                source=SKYSCANNER_SOURCE,
            )
            if not rows:
                continue
            df = pd.DataFrame([{
                "departure": r["departure_date"],
                "price": r["price"],
                "group": r["price_group"] or "n/a",
                "od": f"{r['origin']}→{r['destination']}",
            } for r in rows])
            df["departure"] = pd.to_datetime(df["departure"])
            frames.append(df)
    if not frames:
        st.info("No Sky Scrapper curve data yet — run a sweep.")
        return
    combined = pd.concat(frames, ignore_index=True)
    chart = (
        alt.Chart(combined)
        .mark_line(point=True)
        .encode(
            x=alt.X("departure:T", title="Departure date"),
            y=alt.Y("price:Q", title=f"Price ({route.currency})"),
            color=alt.Color("od:N", title="Origin → Destination"),
            tooltip=["od", "departure:T", "price", "group"],
        )
        .properties(height=320)
    )
    st.altair_chart(chart, use_container_width=True)


# --- Price-history chart ---------------------------------------------------


def itinerary_history_chart(
    conn: sqlite3.Connection, route,
    *, origin: str, destination: str,
    departure_date: str, return_date: str,
) -> None:
    rows = conn.execute(
        """
        SELECT snapshot_at, source, price FROM calendar_snapshots
        WHERE route_id = ? AND origin = ? AND destination = ?
          AND departure_date = ? AND return_date = ?
        ORDER BY snapshot_at ASC
        """,
        (route.name, origin, destination, departure_date, return_date),
    ).fetchall()
    if not rows:
        st.info("No price-history rows for this itinerary yet.")
        return
    df = pd.DataFrame([{
        "snapshot": r["snapshot_at"],
        "price": r["price"],
        "source": r["source"],
    } for r in rows])
    df["snapshot"] = pd.to_datetime(df["snapshot"], format="ISO8601")
    chart = (
        alt.Chart(df)
        .mark_line(point=True)
        .encode(
            x=alt.X("snapshot:T", title="When we saw it"),
            y=alt.Y("price:Q", title=f"Price ({route.currency})"),
            color=alt.Color("source:N"),
            tooltip=["snapshot:T", "source", "price"],
        )
        .properties(height=300)
    )
    st.altair_chart(chart, use_container_width=True)


def recent_itinerary_options(
    conn: sqlite3.Connection, route, *, limit: int = 50,
) -> pd.DataFrame:
    """All recently-observed itineraries within the stay range, both sources."""
    rows = conn.execute(
        """
        SELECT origin, destination, departure_date, return_date, stay_days,
               MIN(price) AS min_price, MAX(snapshot_at) AS latest
        FROM calendar_snapshots
        WHERE route_id = ?
          AND stay_days BETWEEN ? AND ?
        GROUP BY origin, destination, departure_date, return_date
        ORDER BY min_price ASC
        LIMIT ?
        """,
        (route.name, route.stay.min_days, route.stay.max_days, limit),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])
