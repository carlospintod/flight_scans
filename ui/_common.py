"""Shared helpers for the Streamlit UI: DB connection, charts, dataframes."""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import replace as dc_replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from lib import config as config_mod
from lib import db as db_mod
from lib.searchapi_io import SOURCE_ID as SEARCHAPI_SOURCE
from lib.skyscanner_rapidapi import SOURCE_ID as SKYSCANNER_SOURCE

REPO = Path(__file__).resolve().parents[1]
ROUTES_DIR = REPO / "routes"
DEFAULT_DB = REPO / "data" / "tracker.db"

# Load .env once per Streamlit process. Explicit path so this works no
# matter what cwd Streamlit was launched from. The CLI loads .env
# from tracker.py; the UI used to load it from each page's deleted
# Run-jobs file — that import was lost in the 2-page redesign.
load_dotenv(dotenv_path=REPO / ".env")


def _bridge_streamlit_secrets_to_env() -> None:
    """Copy Streamlit Cloud secrets into os.environ.

    Our lib code reads config from os.environ (works with .env locally).
    On Streamlit Cloud there is no .env — config comes from the app's
    Secrets. Streamlit only auto-exposes TOP-LEVEL secrets as env vars,
    NOT ones nested under a [section]. To be robust to either layout we
    walk st.secrets ourselves:

      * top-level scalar  -> os.environ[KEY] = value
      * a [section] table -> os.environ[KEY] = value for each key inside

    Local dev (no secrets.toml) makes st.secrets raise; we swallow that.
    Existing os.environ entries (e.g. from .env) are NOT overwritten, so
    local .env keeps priority when both are present.
    """
    try:
        secrets = st.secrets
    except Exception:  # noqa: BLE001 — no secrets file locally is fine
        return
    try:
        items = list(secrets.items())
    except Exception:  # noqa: BLE001
        return
    for key, value in items:
        # Nested section (e.g. [env]) — Streamlit returns a Mapping.
        if hasattr(value, "items"):
            try:
                for sub_key, sub_val in value.items():
                    if sub_key not in os.environ and sub_val is not None:
                        os.environ[str(sub_key)] = str(sub_val)
            except Exception:  # noqa: BLE001
                continue
        else:
            if key not in os.environ and value is not None:
                os.environ[str(key)] = str(value)


_bridge_streamlit_secrets_to_env()


def setup_page(title: str) -> None:
    st.set_page_config(
        page_title=f"Flight tracker — {title}",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_theme()
    _password_gate()


def _password_gate() -> None:
    """Block the page until the user enters the right password.

    Active iff the env var `APP_PASSWORD` is set (or `[app] password=`
    in Streamlit's secrets.toml — Streamlit Cloud's secrets are also
    available via os.environ when configured under [env]).

    No env var set → gate is bypassed entirely. This means local
    development is unblocked without configuration; the gate only
    kicks in on Streamlit Cloud where APP_PASSWORD is set as a secret.

    Uses st.session_state to remember the auth across reruns so the
    user only types the password once per browser session.
    """
    required = (os.environ.get("APP_PASSWORD") or "").strip()
    if not required:
        return  # no password configured → bypass
    if st.session_state.get("_authed"):
        return  # already authed in this session
    st.markdown(
        "<div class='gtm-logo'>FLIGHT_TRACKER<span class='cur'>_</span></div>"
        "<div class='gtm-sub'>Authentication required.</div>",
        unsafe_allow_html=True,
    )
    pw = st.text_input("Password", type="password", key="_password_input")
    if pw:
        if pw == required:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()


# ============================================================================
# GTM99 visual theme (full terminal transformation).
# ============================================================================


# Centralised palette — referenced both in the CSS string below and in the
# Altair charts in ui/pages/1_Explore.py (kept readable defaults per the
# user's chart-color decision; this is here for future reference).
GTM99 = {
    "bg": "#0a0b10",
    "bg2": "#0f1018",
    "bg3": "#151620",
    "border": "#1e1f32",
    "border_bright": "#2c2d44",
    "green": "#00ff41",
    "green_dim": "#00b830",
    "green_bg": "rgba(0,255,65,0.07)",
    "cyan": "#00d4ff",
    "cyan_bg": "rgba(0,212,255,0.07)",
    "amber": "#ffcc00",
    "amber_bg": "rgba(255,204,0,0.07)",
    "red": "#ff4455",
    "red_bg": "rgba(255,68,85,0.07)",
    "text": "#c8cad8",
    "text_bright": "#e4e6f0",
    "text_mid": "#8e91a8",
    "dim": "#585b72",
    "hint": "#6e7190",
}


_THEME_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');

/* ---- Base font + background ---------------------------------------- */
html, body, [data-testid="stAppViewContainer"], [data-testid="stAppViewContainer"] * {{
    font-family: 'IBM Plex Mono', 'Consolas', 'Courier New', monospace !important;
    -webkit-font-smoothing: antialiased;
}}
/* Re-apply Material Symbols to Streamlit's icon elements — without this
   the `*` rule above overrides their icon font and the icon NAMES
   ('keyboard_double_arrow_left', 'arrow_right', 'check' on status,
   etc.) render as text. */
[data-testid="stIconMaterial"],
[data-testid*="Icon"][data-testid*="Material"],
[class*="material-symbols"],
[class*="material-icons"],
[class*="MaterialSymbol"],
[class*="StyledIcon"],
.material-symbols-rounded,
.material-symbols-outlined,
.material-icons,
i.material-icons,
span.material-symbols-rounded,
span.material-symbols-outlined,
/* Status widget icon: Streamlit doesn't expose a stable testid for it,
   so we target the first span inside the status widget header. */
[data-testid="stStatusWidget"] > details > summary > span:first-child,
[data-testid="stExpander"] details > summary svg + span {{
    font-family: 'Material Symbols Rounded', 'Material Symbols Outlined',
                 'Material Icons', sans-serif !important;
    font-feature-settings: 'liga' !important;
    -webkit-font-feature-settings: 'liga' !important;
    text-transform: none !important;
    letter-spacing: normal !important;
    text-rendering: optimizeLegibility !important;
}}
.stApp {{
    background-color: {GTM99["bg"]} !important;
    color: {GTM99["text"]};
}}
[data-testid="stSidebar"], [data-testid="stSidebarUserContent"] {{
    background-color: {GTM99["bg2"]} !important;
    border-right: 1px solid {GTM99["border"]};
}}
[data-testid="stHeader"] {{
    background: {GTM99["bg2"]} !important;
    border-bottom: 1px solid {GTM99["border"]};
}}

/* ---- Markdown headers --------------------------------------------- */
.stMarkdown h1 {{
    color: {GTM99["green"]} !important;
    letter-spacing: 3px;
    text-shadow: 0 0 14px rgba(0,255,65,0.2);
    font-weight: 700;
    font-size: 26px;
    margin-bottom: 4px;
}}
.stMarkdown h2 {{
    font-size: 11px !important;
    font-weight: 700 !important;
    color: {GTM99["green"]} !important;
    letter-spacing: 2px !important;
    text-transform: uppercase !important;
    padding-bottom: 8px !important;
    border-bottom: 1px solid {GTM99["border"]} !important;
    margin-bottom: 14px !important;
    margin-top: 30px !important;
}}
.stMarkdown h3 {{
    font-size: 12px !important;
    font-weight: 700 !important;
    color: {GTM99["text_bright"]} !important;
    letter-spacing: 1px !important;
    margin-top: 18px !important;
    margin-bottom: 8px !important;
}}
.stMarkdown p, .stMarkdown li {{
    color: {GTM99["text"]};
    font-size: 13px;
}}
.stMarkdown strong {{ color: {GTM99["text_bright"]}; }}
.stMarkdown code {{
    background: {GTM99["bg3"]} !important;
    border: 1px solid {GTM99["border"]} !important;
    color: {GTM99["green"]} !important;
    border-radius: 3px;
    padding: 1px 6px;
    font-size: 12px;
}}

/* ---- Captions (descriptive paragraphs) ---------------------------- */
[data-testid="stCaptionContainer"],
.stCaption, .stCaption p {{
    font-family: 'IBM Plex Sans', system-ui, sans-serif !important;
    color: {GTM99["hint"]} !important;
    font-size: 12.5px !important;
    font-weight: 400 !important;
    letter-spacing: 0.01em !important;
    line-height: 1.55;
}}

/* ---- Buttons ------------------------------------------------------- */
.stButton > button {{
    background: transparent !important;
    border: 1px solid {GTM99["border_bright"]} !important;
    color: {GTM99["text_mid"]} !important;
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    letter-spacing: 1.5px !important;
    text-transform: uppercase !important;
    border-radius: 3px !important;
    padding: 8px 18px !important;
    transition: all 0.15s !important;
}}
.stButton > button:hover {{
    border-color: {GTM99["green_dim"]} !important;
    color: {GTM99["green"]} !important;
    background: {GTM99["green_bg"]} !important;
}}
.stButton > button:focus, .stButton > button:active {{
    outline: none !important;
    box-shadow: 0 0 0 1px {GTM99["green_dim"]} !important;
}}
.stButton > button[kind="primary"] {{
    border-color: {GTM99["green_dim"]} !important;
    color: {GTM99["green"]} !important;
    text-shadow: 0 0 8px rgba(0,255,65,0.25);
}}
.stButton > button[kind="primary"]:hover {{
    background: {GTM99["green_bg"]} !important;
    box-shadow: 0 0 16px rgba(0,255,65,0.25) !important;
}}

/* ---- Metrics ------------------------------------------------------- */
[data-testid="stMetric"] {{
    background: {GTM99["bg2"]};
    border: 1px solid {GTM99["border"]};
    border-radius: 4px;
    padding: 14px 18px;
}}
[data-testid="stMetricLabel"] p {{
    font-size: 10px !important;
    font-weight: 700 !important;
    color: {GTM99["text_mid"]} !important;
    text-transform: uppercase !important;
    letter-spacing: 1.5px !important;
}}
[data-testid="stMetricValue"] {{
    color: {GTM99["text_bright"]} !important;
    font-weight: 700 !important;
    font-family: 'IBM Plex Mono', monospace !important;
}}

/* ---- Alerts (st.info / warning / error / success) ----------------- */
.stAlert {{
    font-family: 'IBM Plex Mono', monospace !important;
    border-radius: 4px !important;
    font-size: 12.5px !important;
    line-height: 1.55 !important;
    border-left-width: 3px !important;
}}
[data-testid="stNotificationContentInfo"], div[data-testid*="Info"] {{
    color: {GTM99["cyan"]} !important;
}}
[data-testid="stNotificationContentWarning"], div[data-testid*="Warning"] {{
    color: {GTM99["amber"]} !important;
}}
[data-testid="stNotificationContentError"], div[data-testid*="Error"] {{
    color: {GTM99["red"]} !important;
}}
[data-testid="stNotificationContentSuccess"], div[data-testid*="Success"] {{
    color: {GTM99["green"]} !important;
}}

/* ---- Expanders ---------------------------------------------------- */
[data-testid="stExpander"] details {{
    background: {GTM99["bg2"]} !important;
    border: 1px solid {GTM99["border"]} !important;
    border-radius: 4px !important;
}}
[data-testid="stExpander"] summary {{
    font-size: 11px !important;
    font-weight: 700 !important;
    color: {GTM99["text_mid"]} !important;
    text-transform: uppercase !important;
    letter-spacing: 1px !important;
}}
[data-testid="stExpander"] summary:hover {{
    color: {GTM99["green"]} !important;
}}

/* ---- Inputs / widgets --------------------------------------------- */
.stTextInput input, .stNumberInput input, .stDateInput input,
.stSelectbox div[data-baseweb="select"], .stMultiSelect div[data-baseweb="select"] {{
    background: {GTM99["bg3"]} !important;
    color: {GTM99["text_bright"]} !important;
    border-color: {GTM99["border"]} !important;
    font-family: 'IBM Plex Mono', monospace !important;
}}
.stCheckbox label, .stRadio label, .stToggle label {{
    color: {GTM99["text"]} !important;
}}
.stSlider [role="slider"] {{
    background-color: {GTM99["green"]} !important;
}}

/* ---- Tables / DataFrames ------------------------------------------ */
[data-testid="stDataFrame"], .stDataFrame {{
    background: {GTM99["bg2"]} !important;
    border: 1px solid {GTM99["border"]} !important;
    border-radius: 4px !important;
}}

/* ---- Status / progress -------------------------------------------- */
[data-testid="stStatusWidget"] {{
    background: {GTM99["bg2"]};
    border: 1px solid {GTM99["border"]};
    border-radius: 4px;
}}

/* ---- Code blocks (logs from run-everything) ----------------------- */
pre, code, [data-testid="stCodeBlock"] {{
    background: {GTM99["bg2"]} !important;
    border: 1px solid {GTM99["border"]} !important;
    color: {GTM99["text"]} !important;
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 11.5px !important;
}}

/* ---- Scrollbar ----------------------------------------------------- */
::-webkit-scrollbar {{ width: 6px; height: 6px; }}
::-webkit-scrollbar-track {{ background: {GTM99["bg"]}; }}
::-webkit-scrollbar-thumb {{ background: {GTM99["border_bright"]}; border-radius: 3px; }}
::-webkit-scrollbar-thumb:hover {{ background: {GTM99["dim"]}; }}

/* ---- Terminal logo header ----------------------------------------- */
.gtm-logo {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 26px;
    font-weight: 700;
    color: {GTM99["green"]};
    letter-spacing: 3px;
    text-shadow: 0 0 14px rgba(0,255,65,0.2);
    margin-bottom: 0;
    text-transform: uppercase;
}}
.gtm-logo .cur {{
    animation: gtm-blink 1s step-end infinite;
}}
@keyframes gtm-blink {{ 50% {{ opacity: 0; }} }}
.gtm-sub {{
    font-family: 'IBM Plex Sans', system-ui, sans-serif;
    color: {GTM99["hint"]};
    font-size: 12.5px;
    margin-top: 4px;
    letter-spacing: 0.01em;
}}

/* ---- Status dot metric --------------------------------------------- */
.gtm-status-row {{
    display: flex; gap: 24px; align-items: stretch; margin: 12px 0 18px;
}}
.gtm-status-card {{
    flex: 1; background: {GTM99["bg2"]};
    border: 1px solid {GTM99["border"]};
    border-radius: 4px;
    padding: 14px 18px;
}}
.gtm-status-label {{
    font-size: 10px; font-weight: 700; color: {GTM99["text_mid"]};
    text-transform: uppercase; letter-spacing: 1.5px;
}}
.gtm-status-value {{
    font-size: 22px; font-weight: 700;
    color: {GTM99["text_bright"]}; margin-top: 6px;
    font-family: 'IBM Plex Mono', monospace;
}}
.gtm-status-dot {{
    display: inline-flex; align-items: center; gap: 8px;
    font-size: 11px; font-weight: 700; letter-spacing: 1px;
    text-transform: uppercase; margin-top: 6px;
}}
.gtm-status-dot::before {{
    content: ''; width: 8px; height: 8px; border-radius: 50%;
    display: inline-block;
}}
.gtm-status-dot.live {{ color: {GTM99["green"]}; }}
.gtm-status-dot.live::before {{
    background: {GTM99["green"]};
    box-shadow: 0 0 6px {GTM99["green"]};
    animation: gtm-pulse-green 2s ease-in-out infinite;
}}
.gtm-status-dot.degraded {{ color: {GTM99["amber"]}; }}
.gtm-status-dot.degraded::before {{
    background: {GTM99["amber"]}; box-shadow: 0 0 6px {GTM99["amber"]};
}}
.gtm-status-dot.blocked {{ color: {GTM99["red"]}; }}
.gtm-status-dot.blocked::before {{
    background: {GTM99["red"]}; box-shadow: 0 0 6px {GTM99["red"]};
}}
.gtm-status-dot.dim {{ color: {GTM99["dim"]}; }}
.gtm-status-dot.dim::before {{
    background: {GTM99["dim"]};
}}
@keyframes gtm-pulse-green {{
    0%, 100% {{ box-shadow: 0 0 6px {GTM99["green"]}; }}
    50% {{ box-shadow: 0 0 16px {GTM99["green"]}; }}
}}

/* ---- Hide Streamlit's "Made with Streamlit" footer, keep toolbar -- */
/* Note: we deliberately do NOT hide [data-testid="stToolbar"] — when
   the sidebar is collapsed, the reopen button lives inside it. Hiding
   the toolbar makes the sidebar unrecoverable without a reload. */
footer {{ visibility: hidden; height: 0 !important; }}
[data-testid="stStatusWidget"] [data-testid="stToolbarActions"] {{ visibility: hidden; }}

/* ---- Force the sidebar collapse/expand control to always be visible
       and readable, even on top of our dark background ------------- */
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapseButton"],
button[kind="header"] {{
    visibility: visible !important;
    opacity: 1 !important;
    color: {GTM99["green"]} !important;
}}
[data-testid="collapsedControl"] svg,
[data-testid="stSidebarCollapseButton"] svg {{
    fill: {GTM99["green"]} !important;
    color: {GTM99["green"]} !important;
}}
</style>
"""


def apply_theme() -> None:
    """Inject the GTM99 theme CSS into the current Streamlit page.

    Called automatically by `setup_page`. Safe to call multiple times —
    repeated `st.markdown` injections just add identical <style> blocks
    that the browser deduplicates.
    """
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


def terminal_header(title: str, subtitle: str | None = None) -> None:
    """Render a GTM99-style page header with blinking cursor.

    Use instead of `st.title(...)` to get the matrix-green logo treatment
    plus an optional sans-serif subtitle line.
    """
    # Underscores look more terminal than spaces; preserve user-passed
    # case but uppercase via CSS.
    safe_title = title.replace("<", "&lt;").replace(">", "&gt;")
    safe_sub = (subtitle or "").replace("<", "&lt;").replace(">", "&gt;")
    sub_html = f'<div class="gtm-sub">{safe_sub}</div>' if subtitle else ""
    st.markdown(
        f'<div class="gtm-logo">{safe_title}<span class="cur">_</span></div>'
        f"{sub_html}",
        unsafe_allow_html=True,
    )


def status_dot_row(items: list[tuple[str, str, str]]) -> None:
    """Render a row of metric cards with colored status dots.

    `items` is a list of (label, value, state) where state is one of
    'live', 'degraded', 'blocked', 'dim'. Use 'live' (pulsing green) for
    fresh / healthy state, 'degraded' (amber) for borderline, 'blocked'
    (red) for error, 'dim' (grey) for "nothing to see here yet".
    """
    cards = []
    for label, value, state in items:
        s_label = label.replace("<", "&lt;").replace(">", "&gt;")
        s_value = str(value).replace("<", "&lt;").replace(">", "&gt;")
        cards.append(
            f'<div class="gtm-status-card">'
            f'<div class="gtm-status-label">{s_label}</div>'
            f'<div class="gtm-status-value">{s_value}</div>'
            f'<div class="gtm-status-dot {state}">{state}</div>'
            f"</div>"
        )
    st.markdown(
        f'<div class="gtm-status-row">{"".join(cards)}</div>',
        unsafe_allow_html=True,
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
def connect_db():
    """Per-process DB connection. Streamlit reruns the script on each
    user action but `cache_resource` keeps this connection alive.

    Backend selected by env vars (same logic as lib.db.connect):

    * TURSO_DATABASE_URL + TURSO_AUTH_TOKEN set → libSQL embedded
      replica syncing to Turso. The local file becomes a cache; writes
      propagate to remote. Survives Streamlit Cloud container restarts.
    * Neither set → plain sqlite3 against the local file (local dev).
    """
    DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
    turso_url = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
    turso_token = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
    if turso_url and turso_token:
        from lib import turso_http
        conn = turso_http.connect(turso_url, turso_token)
        conn.row_factory = sqlite3.Row
        db_mod.ensure_schema(conn)
        return conn

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


# ============================================================================
# Helpers added for the redesigned UI (Run page + Explore page).
# ============================================================================


# --- Status banner helpers --------------------------------------------------


def quota_state(remaining: int | None, limit_total: int | None) -> str:
    """Heuristic for the status dot below a quota number.

    With a known total: percent-based — live ≥30%, degraded 10-30%,
    blocked <10%. With only `remaining` known: absolute-thresholds —
    live >20, degraded 5-20, blocked ≤5. Returns 'dim' when remaining
    is None.
    """
    if remaining is None:
        return "dim"
    # When the limit equals remaining (the API didn't report a separate
    # allowance), fall through to the absolute heuristic.
    if limit_total and limit_total > remaining:
        pct = remaining / limit_total * 100
        if pct >= 30:
            return "live"
        if pct >= 10:
            return "degraded"
        return "blocked"
    # Absolute fallback.
    if remaining > 20:
        return "live"
    if remaining > 5:
        return "degraded"
    return "blocked"


def latest_quota_for_ui(conn: sqlite3.Connection, source: str) -> dict | None:
    """Return latest quota observation for `source`, with a freshness hint.

    Output: {'remaining', 'limit_total', 'checked_at', 'age_hint'} or None.
    """
    row = db_mod.latest_quota(conn, source=source)
    if not row:
        return None
    return {
        "remaining": row["remaining"],
        "limit_total": row["limit_total"],
        "checked_at": row["checked_at"],
        "age_hint": _format_age(row["checked_at"]),
    }


def _format_age(iso: str) -> str:
    """'2h ago' / '3d ago' from an ISO timestamp; '?' if unparseable."""
    try:
        s = iso.replace("Z", "+00:00")
        ts = datetime.fromisoformat(s)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return "?"
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def refresh_searchapi_quota(conn: sqlite3.Connection) -> dict | None:
    """Call SearchAPI's /me to refresh quota, persist a snapshot.

    Returns the normalized quota dict, or None if the key isn't set.
    Raises on network/auth errors so the UI can surface them.
    """
    import json as _json
    from lib.searchapi_io import SearchApiClient
    try:
        client = SearchApiClient.from_env()
    except RuntimeError:
        return None  # key not set
    q = client.check_quota()
    db_mod.record_quota(
        conn, source=SEARCHAPI_SOURCE,
        remaining=q["remaining"], limit_total=q["limit_total"],
        raw_json=_json.dumps(q.get("raw") or {}),
    )
    return q


def refresh_aviasales_quota(conn: sqlite3.Connection) -> dict | None:
    """Make one cheap Aviasales call to learn the (soft) rate-limit.

    Travelpayouts doesn't expose a /me endpoint and (based on the
    2026-06-10 probe) doesn't return X-RateLimit-* headers either —
    so this function almost always returns None even on a fully
    successful call. The caller distinguishes 'token missing' (raises
    RuntimeError) from 'no headers returned' (returns None) properly.
    """
    import json as _json
    from lib.aviasales_api import AviasalesClient, AviasalesError
    # Propagate RuntimeError so the UI can distinguish missing-token
    # from successful-but-headerless. Do NOT swallow it.
    client = AviasalesClient.from_env()
    try:
        client.check_quota()
    except AviasalesError:
        pass
    if client.latest_quota:
        db_mod.record_quota(
            conn, source="aviasales",
            remaining=client.latest_quota.get("remaining"),
            limit_total=client.latest_quota.get("limit_total"),
            raw_json=_json.dumps(client.latest_quota.get("raw") or {}),
        )
    return client.latest_quota


def refresh_kiwi_quota(conn: sqlite3.Connection) -> dict | None:
    """Make one cheap Kiwi call to capture the RapidAPI rate-limit headers.

    Raises RuntimeError if RAPIDAPI_KEY is not set so the UI can
    distinguish 'key missing' from 'call succeeded but no headers'.
    """
    from datetime import date as _d
    from lib.kiwi_rapidapi import KiwiClient, KiwiError
    client = KiwiClient.from_env(db_conn=conn)
    # Use a date safely in the future so Kiwi can answer; the call's
    # response is irrelevant — we just need the headers.
    probe_dep = _d.today().replace(day=1)
    if probe_dep.month == 12:
        probe_ret = probe_dep.replace(year=probe_dep.year + 1, month=1, day=15)
    else:
        probe_ret = probe_dep.replace(month=probe_dep.month + 1, day=15)
    try:
        client.round_trip_search(
            origin="MAD", destination="NBO",
            depart_date=probe_dep, return_date=probe_ret,
            currency="EUR", limit=1,
        )
    except KiwiError:
        pass  # headers captured regardless
    return client.latest_quota


def refresh_skyscanner_quota(conn: sqlite3.Connection) -> dict | None:
    """Make one cheap Sky Scrapper call to capture rate-limit headers.

    Sky Scrapper has no /me-style endpoint, so the only way to learn
    the quota is to make a real API call and read the response headers.
    This helper makes a single `searchAirport` lookup for 'MAD' (which
    is already in our airport_cache, so the result is discarded — we
    just need the headers).

    Costs 1 RapidAPI call. The client's _request() persists a quota
    snapshot to the DB as a side effect.

    Returns the latest_quota dict (also accessible via
    `latest_quota_for_ui(conn, 'skyscanner')` after this completes).
    Returns None if RAPIDAPI_KEY isn't set; raises on network errors.
    """
    from lib.skyscanner_rapidapi import SkyScrapperClient
    try:
        client = SkyScrapperClient.from_env(db_conn=conn)
    except RuntimeError:
        return None  # key not set
    # Direct call to _request — bypasses the cache that resolve_airport
    # would hit. We don't care about the response, only the headers.
    try:
        client._request("searchAirport", {"query": "MAD", "locale": "en-US"})  # noqa: SLF001
    except Exception:
        # Even an error response carries the rate-limit headers; the
        # client persists them before raising. Propagate so the UI can
        # surface auth errors etc., but the quota snapshot was already
        # written if the request reached the server at all.
        raise
    return client.latest_quota


def recent_capture_summary(conn: sqlite3.Connection, route) -> dict[str, int]:
    """Rows captured for this route in the last 24h, per table.

    Returns keys 'calendar', 'curve', 'point' so the Run page can display
    something like 'Captured today: 1,960 prices, 0 curve days, 30 carriers'.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    calendar = conn.execute(
        "SELECT COUNT(*) FROM calendar_snapshots WHERE route_id = ? AND snapshot_at >= ?",
        (route.name, since),
    ).fetchone()[0]
    curve = conn.execute(
        "SELECT COUNT(*) FROM departure_curves WHERE route_id = ? AND snapshot_at >= ?",
        (route.name, since),
    ).fetchone()[0]
    point = conn.execute(
        "SELECT COUNT(*) FROM point_queries WHERE route_id = ? AND snapshot_at >= ?",
        (route.name, since),
    ).fetchone()[0]
    return {"calendar": calendar, "curve": curve, "point": point}


def recent_alert_count(conn: sqlite3.Connection, route, *, days: int) -> int:
    """Number of alerts fired in the last `days` days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE route_id = ? AND fired_at >= ?",
        (route.name, since),
    ).fetchone()[0]


def next_action_hint(conn: sqlite3.Connection, route) -> str:
    """One-line guidance for what the user should do next.

    Heuristic, not a hard rule. Drives the info banner on the Run page.
    """
    # Has any sweep ever happened?
    total = conn.execute(
        "SELECT COUNT(*) FROM calendar_snapshots WHERE route_id = ?",
        (route.name,),
    ).fetchone()[0]
    if total == 0:
        return (
            "No data yet for this route. Click **Run everything** below to "
            "fetch the first batch of prices."
        )
    # When was the most recent sweep?
    latest_age = _latest_snapshot_age(conn, route)
    # Recent alerts?
    if recent_alert_count(conn, route, days=7) > 0:
        return (
            "Alerts fired in the last 7 days — open the Explore page to "
            "review them."
        )
    if latest_age and ("d ago" in latest_age):
        # Parse "Nd ago"
        try:
            n = int(latest_age.split("d")[0])
            if n >= 14:
                return (
                    f"Last sweep was {n} days ago. Recommended cadence is "
                    "every 2 weeks — click **Run everything** to refresh."
                )
        except ValueError:
            pass
    return (
        "Up to date. Open the **Explore** page to browse alternatives, "
        "the price heatmap, and carrier mix."
    )


# --- Explore page: heatmap, alternatives, carrier mix, stops ----------------


def latest_grid_for_heatmap(
    conn: sqlite3.Connection,
    route,
    *,
    origin: str,
    source: str,
    min_stay: int,
    max_stay: int,
) -> pd.DataFrame:
    """One row per (departure_date, stay_days) with the cheapest most-recent
    price from `calendar_snapshots`. Sky Scrapper has no return_date, so this
    helper is meaningful only for `source='searchapi'`.
    """
    rows = conn.execute(
        """
        SELECT cs.departure_date, cs.stay_days, MIN(cs.price) AS price
        FROM calendar_snapshots cs
        JOIN (
            SELECT origin, destination, departure_date, return_date,
                   MAX(snapshot_at) AS latest
            FROM calendar_snapshots
            WHERE route_id = ? AND source = ? AND origin = ?
            GROUP BY origin, destination, departure_date, return_date
        ) m
          ON m.origin = cs.origin
         AND m.destination = cs.destination
         AND m.departure_date = cs.departure_date
         AND m.return_date = cs.return_date
         AND m.latest = cs.snapshot_at
        WHERE cs.route_id = ? AND cs.source = ? AND cs.origin = ?
          AND cs.stay_days BETWEEN ? AND ?
        GROUP BY cs.departure_date, cs.stay_days
        ORDER BY cs.departure_date ASC, cs.stay_days ASC
        """,
        (route.name, source, origin,
         route.name, source, origin, min_stay, max_stay),
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["departure_date", "stay_days", "price"])
    return pd.DataFrame([dict(r) for r in rows])


def top_alternatives(
    conn: sqlite3.Connection,
    route,
    *,
    source: str | None,
    min_stay: int,
    max_stay: int,
    origin: str | None = None,
    limit: int = 20,
) -> pd.DataFrame:
    """Cheapest most-recent itineraries within the stay range.

    `source`: 'searchapi' | 'skyscanner' | None (both). Sky Scrapper has no
    return_date so its rows only show up when source='skyscanner' AND we
    fall back to departure_curves.
    """
    if source == SKYSCANNER_SOURCE:
        # Curve-only: no return date, just departure + price
        rows = conn.execute(
            """
            SELECT origin, destination, departure_date,
                   NULL AS return_date, NULL AS stay_days,
                   price, currency,
                   ? AS source,
                   NULL AS top_carrier,
                   NULL AS stops,
                   NULL AS total_minutes,
                   0 AS is_self_transfer
            FROM departure_curves dc
            WHERE route_id = ? AND source = ?
              AND snapshot_at = (
                  SELECT MAX(snapshot_at) FROM departure_curves
                  WHERE route_id = ? AND source = ?
              )
            ORDER BY price ASC
            LIMIT ?
            """,
            (SKYSCANNER_SOURCE, route.name, SKYSCANNER_SOURCE,
             route.name, SKYSCANNER_SOURCE, limit),
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])

    # SearchAPI (or both). point_queries are source-tagged but the cheapest
    # cs row already determines the source we're showing; we just look up
    # the most recent point query for that itinerary regardless of source.
    where_extra: list[str] = []
    bind: list = [route.name, route.name]
    if source:
        where_extra.append("AND cs.source = ?")
        bind.append(source)
    if origin:
        where_extra.append("AND cs.origin = ?")
        bind.append(origin)
    where_extra.append("AND cs.stay_days BETWEEN ? AND ?")
    bind.extend([min_stay, max_stay])
    rows = conn.execute(
        f"""
        SELECT cs.origin, cs.destination, cs.departure_date, cs.return_date,
               cs.stay_days, cs.price, cs.currency, cs.source,
               (SELECT carriers FROM point_queries pq
                WHERE pq.route_id = cs.route_id AND pq.origin = cs.origin
                  AND pq.destination = cs.destination
                  AND pq.departure_date = cs.departure_date
                  AND pq.return_date = cs.return_date AND pq.rank = 0
                ORDER BY pq.snapshot_at DESC LIMIT 1) AS top_carrier,
               (SELECT stops FROM point_queries pq
                WHERE pq.route_id = cs.route_id AND pq.origin = cs.origin
                  AND pq.destination = cs.destination
                  AND pq.departure_date = cs.departure_date
                  AND pq.return_date = cs.return_date AND pq.rank = 0
                ORDER BY pq.snapshot_at DESC LIMIT 1) AS stops,
               (SELECT total_minutes FROM point_queries pq
                WHERE pq.route_id = cs.route_id AND pq.origin = cs.origin
                  AND pq.destination = cs.destination
                  AND pq.departure_date = cs.departure_date
                  AND pq.return_date = cs.return_date AND pq.rank = 0
                ORDER BY pq.snapshot_at DESC LIMIT 1) AS total_minutes,
               (SELECT is_self_transfer FROM point_queries pq
                WHERE pq.route_id = cs.route_id AND pq.origin = cs.origin
                  AND pq.destination = cs.destination
                  AND pq.departure_date = cs.departure_date
                  AND pq.return_date = cs.return_date AND pq.rank = 0
                ORDER BY pq.snapshot_at DESC LIMIT 1) AS is_self_transfer
        FROM calendar_snapshots cs
        JOIN (
            SELECT source, origin, destination, departure_date, return_date,
                   MAX(snapshot_at) AS latest
            FROM calendar_snapshots
            WHERE route_id = ?
            GROUP BY source, origin, destination, departure_date, return_date
        ) m
          ON m.source = cs.source AND m.origin = cs.origin
         AND m.destination = cs.destination
         AND m.departure_date = cs.departure_date
         AND m.return_date = cs.return_date
         AND m.latest = cs.snapshot_at
        WHERE cs.route_id = ? {' '.join(where_extra)}
        ORDER BY cs.price ASC
        LIMIT ?
        """,
        bind + [limit],
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def carrier_mix(
    conn: sqlite3.Connection, route, *, source: str | None,
    min_stay: int, max_stay: int,
) -> pd.DataFrame:
    """Count of best-flight (rank=0) point queries grouped by carrier string.

    Each carrier string is preserved verbatim ('KLM + Kenya Airways' is
    its own bucket — multi-carrier itineraries are a distinct signal).
    """
    where_extra = ["AND pq.rank = 0"]
    bind: list = [route.name, route.name, min_stay, max_stay]
    if source:
        where_extra.append("AND pq.source = ?")
        bind.append(source)
    # EXISTS (not JOIN) so multiple snapshots for the same itinerary
    # don't multi-count the underlying point query.
    rows = conn.execute(
        f"""
        SELECT pq.carriers AS carriers, COUNT(*) AS n
        FROM point_queries pq
        WHERE pq.route_id = ?
          AND EXISTS (
              SELECT 1 FROM calendar_snapshots cs
              WHERE cs.route_id = ?
                AND cs.origin = pq.origin AND cs.destination = pq.destination
                AND cs.departure_date = pq.departure_date
                AND cs.return_date = pq.return_date
                AND cs.stay_days BETWEEN ? AND ?
          )
          {' '.join(where_extra)}
        GROUP BY pq.carriers
        ORDER BY n DESC
        """,
        bind,
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["carriers", "n"])
    return pd.DataFrame([dict(r) for r in rows])


def stops_distribution(
    conn: sqlite3.Connection, route, *, source: str | None,
    min_stay: int, max_stay: int,
) -> pd.DataFrame:
    """Histogram of stops across best-flight (rank=0) point queries."""
    where_extra = ["AND pq.rank = 0"]
    bind: list = [route.name, route.name, min_stay, max_stay]
    if source:
        where_extra.append("AND pq.source = ?")
        bind.append(source)
    rows = conn.execute(
        f"""
        SELECT pq.stops AS stops, COUNT(*) AS n
        FROM point_queries pq
        WHERE pq.route_id = ?
          AND EXISTS (
              SELECT 1 FROM calendar_snapshots cs
              WHERE cs.route_id = ?
                AND cs.origin = pq.origin AND cs.destination = pq.destination
                AND cs.departure_date = pq.departure_date
                AND cs.return_date = pq.return_date
                AND cs.stay_days BETWEEN ? AND ?
          )
          {' '.join(where_extra)}
        GROUP BY pq.stops
        ORDER BY pq.stops ASC
        """,
        bind,
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["stops", "n"])
    return pd.DataFrame([dict(r) for r in rows])


def alerts_dataframe(
    conn: sqlite3.Connection, route, *, limit: int = 20,
    sources: list[str] | None = None,
) -> pd.DataFrame:
    if not sources:
        sources = [SEARCHAPI_SOURCE, SKYSCANNER_SOURCE]
    placeholders = ",".join("?" * len(sources))
    rows = conn.execute(
        f"""
        SELECT * FROM alerts
        WHERE route_id = ? AND source IN ({placeholders})
        ORDER BY fired_at DESC LIMIT ?
        """,
        (route.name, *sources, limit),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
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


# --- Run orchestration ------------------------------------------------------


def _run_aviasales_sweep(conn, av_client, route, *, dry_run: bool) -> int:
    """One latest_prices call per (origin, destination); persist rows.

    Aviasales' /v2/prices/latest returns the cheapest recently-cached
    prices on a route, including carriers Google Flights skips
    (especially Saudia). We persist each returned itinerary as a
    `calendar_snapshots` row tagged source='aviasales' — same shape
    as SearchAPI calendar entries, just from a different aggregator.

    Returns the number of rows stored (0 in dry-run).
    """
    if dry_run or av_client is None:
        return 0
    from lib.aviasales_api import AviasalesError, SOURCE_ID as AV_SOURCE
    from lib.db import CalendarRow, insert_calendar_rows
    snapshot_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stored = 0
    # Per the 2026-06-10 probe, /v2/prices/latest returns empty for
    # MAD->NBO on most months — Aviasales' cache is sparse on this
    # corridor. /v1/prices/cheap is more reliable: 1 call returns the
    # currently-cheapest cached observations per destination, including
    # carriers Google Flights skips (especially SV/Saudia).
    #
    # We call cheap_prices with no date filter and accept whatever the
    # cache currently knows. This is cheap (1 call per O-D pair) and
    # broad. We do call it once per route.origin × route.destination —
    # so 2 origins × 1 destination = 2 calls for spain-nairobi.
    for origin in route.origins:
        for destination in route.destinations:
            month_total = 0
            for m in [None]:  # single pass — no month filter
                try:
                    resp = av_client.cheap_prices(
                        origin=origin, destination=destination,
                        depart_date=None, return_date=None,
                        currency=route.currency,
                    )
                except AviasalesError as exc:
                    LOG.warning(
                        "aviasales %s->%s err=%s",
                        origin, destination, exc,
                    )
                    continue
                rows: list[CalendarRow] = []
                for q in resp.quotes:
                    if not q.return_date:
                        continue  # need round-trip for calendar_snapshots
                    try:
                        from datetime import date as _d
                        d_dep = _d.fromisoformat(q.departure_date)
                        d_ret = _d.fromisoformat(q.return_date)
                    except ValueError:
                        continue
                    stay_days = (d_ret - d_dep).days
                    if stay_days <= 0:
                        continue
                    rows.append(CalendarRow(
                        snapshot_at=snapshot_at,
                        route_id=route.name,
                        source=AV_SOURCE,
                        origin=q.origin or origin,
                        destination=q.destination or destination,
                        departure_date=q.departure_date,
                        return_date=q.return_date,
                        stay_days=stay_days,
                        price=q.price,
                        currency=q.currency or route.currency,
                        is_lowest_price=False,
                    ))
                stored += insert_calendar_rows(conn, rows)
                month_total += len(rows)
            LOG.info(
                "aviasales sweep %s->%s rows=%d",
                origin, destination, month_total,
            )
    return stored


def _run_kiwi_followup(conn, kw_client, route, *, dry_run: bool) -> int:
    """For each follow-up candidate, run one Kiwi round-trip search.

    Stores top results in `point_queries` tagged source='kiwi', with
    `is_self_transfer` set to True when Kiwi flagged virtual interlining.
    Returns rows stored.
    """
    if dry_run or kw_client is None:
        return 0
    from datetime import date as _d
    from lib.db import PointRow, insert_point_rows
    from lib.followup import select_candidates
    from lib.kiwi_rapidapi import KiwiError, SOURCE_ID as KW_SOURCE
    candidates = select_candidates(conn, route)
    snapshot_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stored = 0
    MAX_RANKS = 3
    for c in candidates:
        try:
            resp = kw_client.round_trip_search(
                origin=c["origin"], destination=c["destination"],
                depart_date=_d.fromisoformat(c["departure_date"]),
                return_date=_d.fromisoformat(c["return_date"]),
                currency=route.currency,
            )
        except KiwiError as exc:
            LOG.warning(
                "kiwi %s->%s dep=%s ret=%s err=%s",
                c["origin"], c["destination"],
                c["departure_date"], c["return_date"], exc,
            )
            continue
        rows: list[PointRow] = []
        for i, opt in enumerate(resp.options[:MAX_RANKS]):
            rows.append(PointRow(
                snapshot_at=snapshot_at,
                route_id=route.name,
                source=KW_SOURCE,
                origin=c["origin"],
                destination=c["destination"],
                departure_date=c["departure_date"],
                return_date=c["return_date"],
                rank=i,
                price=opt.price,
                currency=opt.currency or route.currency,
                carriers=opt.carriers,
                total_minutes=opt.total_minutes,
                stops=opt.stops,
                is_self_transfer=opt.is_virtual_interlining,
            ))
        stored += insert_point_rows(conn, rows)
        LOG.info(
            "kiwi point %s->%s dep=%s ret=%s ranks=%d vi=%s",
            c["origin"], c["destination"],
            c["departure_date"], c["return_date"], len(rows),
            any(r.is_self_transfer for r in rows),
        )
    return stored


def _months_between(start, end) -> list:
    """Return first-of-month dates from start_month to end_month inclusive."""
    from datetime import date as _d
    out = []
    cur = _d(start.year, start.month, 1)
    last = _d(end.year, end.month, 1)
    while cur <= last:
        out.append(cur)
        # Step to next month
        yr = cur.year + (cur.month // 12)
        mo = (cur.month % 12) + 1
        cur = _d(yr, mo, 1)
    return out


def _make_clients(sources: list[str], dry_run: bool, conn: sqlite3.Connection):
    """Build API clients per the source list.

    Returns dict {source_id: client_or_None}. A None entry means the key
    isn't set in .env (st.warning emitted) — caller skips that source.
    In dry_run all entries are None.
    """
    out: dict[str, object | None] = {
        "searchapi": None, "skyscanner": None,
        "aviasales": None, "kiwi": None,
    }
    if dry_run:
        return out
    if "searchapi" in sources:
        from lib.searchapi_io import SearchApiClient
        try:
            out["searchapi"] = SearchApiClient.from_env()
        except RuntimeError as exc:
            st.warning(f"SearchAPI disabled: {exc}")
    if "skyscanner" in sources:
        from lib.skyscanner_rapidapi import SkyScrapperClient
        try:
            out["skyscanner"] = SkyScrapperClient.from_env(db_conn=conn)
        except RuntimeError as exc:
            st.warning(f"Sky Scrapper disabled: {exc}")
    if "aviasales" in sources:
        from lib.aviasales_api import AviasalesClient
        try:
            out["aviasales"] = AviasalesClient.from_env()
        except RuntimeError as exc:
            st.warning(f"Aviasales disabled: {exc}")
    if "kiwi" in sources:
        from lib.kiwi_rapidapi import KiwiClient
        try:
            out["kiwi"] = KiwiClient.from_env(db_conn=conn)
        except RuntimeError as exc:
            st.warning(f"Kiwi disabled: {exc}")
    return out


def run_all(
    conn: sqlite3.Connection,
    route,
    *,
    sources: list[str],
    searchapi_cap: int,
    skyscanner_cap: int,
    dry_run: bool,
    alerts_log: Path,
) -> dict:
    """Sweep → followup → alerts in sequence.

    Each step is independent — failure in one does NOT block the next.
    Returns a dict with per-step results, errors, and captured logs.
    """
    import io
    import logging
    from contextlib import redirect_stdout

    from lib import alerts as alerts_mod
    from lib import followup as followup_mod
    from lib import sweep as sweep_mod

    out: dict = {"sweep": None, "followup": None, "alerts": [],
                 "aviasales_rows": 0, "kiwi_rows": 0,
                 "errors": [], "logs": {}}
    clients = _make_clients(sources, dry_run, conn)
    sa = clients["searchapi"]
    sky = clients["skyscanner"]
    av = clients["aviasales"]
    kw = clients["kiwi"]
    if all(c is None for c in clients.values()) and not dry_run:
        st.error(
            "No API clients available. Either tick **Dry run** in Advanced "
            "settings or add the relevant key/token to your .env "
            "(SEARCHAPI_KEY / RAPIDAPI_KEY / TRAVELPAYOUTS_TOKEN)."
        )
        return out

    def _capture(fn):
        """Run fn under captured stdout + INFO-level logging."""
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))
        root = logging.getLogger()
        prev_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        result = None
        err = None
        try:
            with redirect_stdout(buf):
                try:
                    result = fn()
                except Exception as exc:  # noqa: BLE001 — surface everything
                    err = exc
                    buf.write(f"\n[ERROR] {type(exc).__name__}: {exc}\n")
        finally:
            root.removeHandler(handler)
            root.setLevel(prev_level)
        return result, err, buf.getvalue()

    # --- Step 1: sweep ---
    with st.status("Step 1/3 — Sweeping prices", expanded=True) as s:
        def _sweep():
            return sweep_mod.run_sweep(
                conn=conn, client=sa, route=route,
                max_calls=searchapi_cap or None,
                dry_run=dry_run,
                skyscanner_client=sky,
                skyscanner_planned="skyscanner" in sources,
            )
        result, err, log = _capture(_sweep)
        out["sweep"] = result
        out["logs"]["sweep"] = log
        st.code(log or "(no output)")
        if err:
            out["errors"].append(("sweep", err))
            s.update(label=f"Sweep failed: {err}", state="error")
        elif result is None:
            s.update(label="Sweep skipped (no client)", state="error")
        elif dry_run:
            # In dry-run, entries_stored is always 0. Surface the plan instead.
            sky_pairs = (len(route.origins) * len(route.destinations)
                         if "skyscanner" in sources else 0)
            s.update(
                label=(
                    f"Sweep dry-run — {result.windows_planned} SearchAPI windows "
                    f"would be called, {sky_pairs} Sky Scrapper curve calls planned. "
                    f"No data written."
                ),
                state="complete",
            )
        else:
            s.update(
                label=(
                    f"Sweep done — {result.entries_stored} grid rows, "
                    f"{result.curve_entries_stored} curve rows"
                ),
                state="complete",
            )

    # --- Sub-step: Aviasales latest cached prices per (origin, dest) ---
    if av is not None or (dry_run and "aviasales" in sources):
        with st.status(
            "Step 1b — Aviasales (Saudia + MENA coverage)", expanded=True,
        ) as s:
            def _av_sweep():
                return _run_aviasales_sweep(conn, av, route, dry_run=dry_run)
            result, err, log = _capture(_av_sweep)
            out["aviasales_rows"] = result or 0
            out["logs"]["aviasales"] = log
            st.code(log or "(no output)")
            if err:
                out["errors"].append(("aviasales", err))
                s.update(label=f"Aviasales failed: {err}", state="error")
            elif dry_run:
                planned = len(route.origins) * len(route.destinations)
                s.update(
                    label=(
                        f"Aviasales dry-run — {planned} (origin,destination) "
                        f"calls planned. No data written."
                    ),
                    state="complete",
                )
            else:
                s.update(
                    label=f"Aviasales done — {result or 0} cached price rows stored",
                    state="complete",
                )

    # --- Step 2: followup ---
    with st.status("Step 2/3 — Following up on candidates", expanded=True) as s:
        def _follow():
            return followup_mod.run_followup(
                conn=conn, client=sa, route=route,
                max_calls=searchapi_cap or None,
                dry_run=dry_run,
                skyscanner_client=sky,
            )
        result, err, log = _capture(_follow)
        out["followup"] = result
        out["logs"]["followup"] = log
        st.code(log or "(no output)")
        if err:
            out["errors"].append(("followup", err))
            s.update(label=f"Followup failed: {err}", state="error")
        elif result is None:
            s.update(label="Followup skipped (no client)", state="error")
        elif result.candidates == 0:
            s.update(label="No followup candidates — nothing matched the watch threshold.",
                     state="complete")
        elif dry_run:
            s.update(
                label=(
                    f"Followup dry-run — {result.candidates} candidate itineraries "
                    f"would be point-queried. No data written."
                ),
                state="complete",
            )
        else:
            s.update(
                label=(
                    f"Followup done — {result.itineraries_queried} "
                    f"itineraries point-queried, {result.rows_stored} rows stored"
                ),
                state="complete",
            )

    # --- Sub-step: Kiwi virtual-interlining check on followup candidates ---
    if kw is not None or (dry_run and "kiwi" in sources):
        with st.status(
            "Step 2b — Kiwi (virtual interlining bundles)", expanded=True,
        ) as s:
            def _kw_sweep():
                return _run_kiwi_followup(conn, kw, route, dry_run=dry_run)
            result, err, log = _capture(_kw_sweep)
            out["kiwi_rows"] = result or 0
            out["logs"]["kiwi"] = log
            st.code(log or "(no output)")
            if err:
                out["errors"].append(("kiwi", err))
                s.update(label=f"Kiwi failed: {err}", state="error")
            elif dry_run:
                from lib.followup import select_candidates
                planned = len(select_candidates(conn, route))
                s.update(
                    label=(
                        f"Kiwi dry-run — would query up to {planned} candidates "
                        f"for virtual-interlining bundles. No data written."
                    ),
                    state="complete",
                )
            else:
                s.update(
                    label=f"Kiwi done — {result or 0} point-query rows stored",
                    state="complete",
                )

    # Refresh SearchAPI quota now that the calls have completed. This is a
    # /me call (free, doesn't count against quota). Sky Scrapper's quota is
    # captured passively from response headers during the run itself.
    if not dry_run and sa is not None:
        try:
            refresh_searchapi_quota(conn)
        except Exception as exc:  # noqa: BLE001 — quota refresh must never break a run
            LOG.warning("post-run SearchAPI quota refresh failed: %s", exc)

    # --- Step 3: alerts ---
    with st.status("Step 3/3 — Evaluating alerts", expanded=True) as s:
        def _alerts():
            return alerts_mod.evaluate(
                conn=conn, route=route, log_path=alerts_log,
            )
        result, err, log = _capture(_alerts)
        out["alerts"] = result or []
        out["logs"]["alerts"] = log
        st.code(log or "(no output)")
        if err:
            out["errors"].append(("alerts", err))
            s.update(label=f"Alerts failed: {err}", state="error")
        else:
            s.update(
                label=(
                    f"Alerts done — {len(out['alerts'])} fired "
                    "(alerts evaluation is pure SQL — same behavior in dry-run)"
                    if dry_run else
                    f"Alerts done — {len(out['alerts'])} fired"
                ),
                state="complete",
            )

    return out
