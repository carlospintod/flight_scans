"""Flight tracker — Run page.

The entry point in `streamlit run ui/app.py`. Page 1 of 2 (the other
is `pages/1_Explore.py`).

The whole point of this page is to make it impossible to be confused
about "did anything happen?". A big status banner at the top tells you
the system's state, one button runs the entire pipeline with live
progress, and a summary card after the run says exactly what landed
in the DB.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ui._common import (  # noqa: E402
    apply_overrides,
    load_route_from_sidebar,
    next_action_hint,
    recent_alert_count,
    recent_capture_summary,
    run_all,
    setup_page,
    status_dot_row,
    terminal_header,
    _latest_snapshot_age,
)

setup_page("Run")
base_route, conn = load_route_from_sidebar()
ALERTS_LOG = REPO / "data" / "alerts.log"

sw = base_route.search_window
terminal_header(
    f"FLIGHT_TRACKER // {base_route.name}",
    subtitle=(
        f"{', '.join(base_route.origins)} → {', '.join(base_route.destinations)}  ·  "
        f"stay {base_route.stay.min_days}-{base_route.stay.max_days}d  ·  "
        f"window {sw.earliest_departure} → {sw.latest_return}  ·  "
        f"{base_route.currency}"
    ),
)

# ---- Status banner ----------------------------------------------------------
st.markdown("## System status")

age = _latest_snapshot_age(conn, base_route) or "never"
capture = recent_capture_summary(conn, base_route)
captured_24h = capture["calendar"] + capture["curve"] + capture["point"]
alerts_7d = recent_alert_count(conn, base_route, days=7)


def _age_state(s: str) -> str:
    if s == "never":
        return "dim"
    if "<1h" in s or "h ago" in s:
        return "live"
    if "d ago" in s:
        try:
            n = int(s.split("d")[0])
            return "live" if n < 14 else "degraded"
        except ValueError:
            return "degraded"
    return "dim"


def _capture_state(n: int) -> str:
    return "live" if n > 0 else "dim"


def _alert_state(n: int) -> str:
    return "live" if n > 0 else "dim"


status_dot_row([
    ("Last sweep", age, _age_state(age)),
    ("Rows captured (24h)", f"{captured_24h:,}", _capture_state(captured_24h)),
    ("Alerts (7d)", str(alerts_7d), _alert_state(alerts_7d)),
])

st.info(next_action_hint(conn, base_route))

# ---- Override + Advanced ----------------------------------------------------
with st.expander("Advanced settings (sources, caps, overrides)", expanded=False):
    st.markdown("**Sources & caps**")
    col_a, col_b = st.columns(2)
    with col_a:
        sources = st.multiselect(
            "Data sources",
            ["searchapi", "skyscanner"],
            default=["searchapi"],
            help=(
                "Sky Scrapper free tier is small (100/mo across all calls). "
                "Default keeps it off so you don't surprise-exhaust it."
            ),
        )
        dry_run = st.checkbox(
            "Dry run (no API calls)", value=False,
            help="Plans windows + candidates without spending any budget."
        )
    with col_b:
        searchapi_cap = st.number_input(
            "Max SearchAPI calls (sweep)", min_value=0, max_value=100,
            value=20, step=5,
            help="0 = no cap. SearchAPI free tier is 100/month total.",
        )
        skyscanner_cap = st.number_input(
            "Max Sky Scrapper calls", min_value=0, max_value=100,
            value=4, step=1,
            help="0 = no cap. Sky Scrapper free tier is small.",
        )

    st.markdown("---")
    st.markdown("**Override route settings for this run only**")
    st.caption("Narrow the window to spend less, or widen it to look further out.")
    col1, col2 = st.columns(2)
    with col1:
        ov_earliest = st.date_input(
            "Earliest departure", value=base_route.search_window.earliest_departure,
        )
        ov_min_stay = st.number_input(
            "Min stay days", min_value=1, max_value=365,
            value=base_route.stay.min_days,
        )
    with col2:
        ov_latest = st.date_input(
            "Latest return", value=base_route.search_window.latest_return,
        )
        ov_max_stay = st.number_input(
            "Max stay days", min_value=1, max_value=365,
            value=base_route.stay.max_days,
        )
    ov_origins = st.multiselect(
        "Origins (subset of YAML defaults)",
        options=list(base_route.origins),
        default=list(base_route.origins),
    )
    ov_destinations = st.multiselect(
        "Destinations (subset of YAML defaults)",
        options=list(base_route.destinations),
        default=list(base_route.destinations),
    )

# Build the effective route for this run.
try:
    route = apply_overrides(
        base_route,
        earliest_departure=ov_earliest,
        latest_return=ov_latest,
        min_stay_days=int(ov_min_stay),
        max_stay_days=int(ov_max_stay),
        origins=tuple(ov_origins) if ov_origins else None,
        destinations=tuple(ov_destinations) if ov_destinations else None,
    )
except ValueError as exc:
    st.warning(f"Override invalid: {exc}. Falling back to YAML defaults.")
    route = base_route

# ---- Cost preview ----------------------------------------------------------
from lib.followup import select_candidates  # noqa: E402 — kept inline to delay import
from lib.sweep import plan_windows  # noqa: E402

windows = plan_windows(route)
candidates = select_candidates(conn, route) if not dry_run else []

n_sa_sweep = min(len(windows), searchapi_cap) if searchapi_cap else len(windows)
n_sky_sweep = len(route.origins) * len(route.destinations) if "skyscanner" in sources else 0
sa_active = "searchapi" in sources
sky_active = "skyscanner" in sources

st.markdown("## Run the pipeline")
st.markdown(
    f"**This run will use approximately:**  "
    f"`{n_sa_sweep if sa_active else 0}` SearchAPI sweep calls  +  "
    f"`{len(candidates) if sa_active else 0}` SearchAPI followup calls  +  "
    f"`{n_sky_sweep}` Sky Scrapper curve calls"
    f"{' (skyscanner off)' if not sky_active else ''}."
)
st.caption(
    f"{len(windows)} sweep windows planned · {len(candidates)} followup "
    f"candidates · over `{','.join(route.origins)}` → `{','.join(route.destinations)}`, "
    f"`{route.search_window.earliest_departure}` → `{route.search_window.latest_return}`, "
    f"stay `{route.stay.min_days}-{route.stay.max_days}d`."
)

# ---- The button ------------------------------------------------------------
run = st.button(
    "Run everything (sweep → followup → alerts)",
    type="primary", use_container_width=True,
)

if run:
    result = run_all(
        conn=conn,
        route=route,
        sources=sources,
        searchapi_cap=int(searchapi_cap),
        skyscanner_cap=int(skyscanner_cap),
        dry_run=dry_run,
        alerts_log=ALERTS_LOG,
    )
    st.session_state["last_run_result"] = result

    # ---- Summary card ----
    sw_res = result.get("sweep")
    fu_res = result.get("followup")
    alerts = result.get("alerts") or []
    errors = result.get("errors") or []
    parts: list[str] = []
    if sw_res:
        parts.append(
            f"sweep: SearchAPI {sw_res.calls_made} calls / {sw_res.entries_stored} rows; "
            f"Sky Scrapper {sw_res.curve_calls_made} calls / "
            f"{sw_res.curve_entries_stored} curve rows"
        )
    if fu_res:
        parts.append(
            f"followup: SearchAPI {fu_res.calls_made} calls / "
            f"Sky Scrapper {fu_res.skyscanner_calls} calls / "
            f"{fu_res.rows_stored} rows total"
        )
    parts.append(f"alerts: {len(alerts)} fired")
    summary = " · ".join(parts)

    if errors:
        st.error(
            f"Run finished with errors in: "
            f"{', '.join(s for s, _ in errors)}. Details in the step logs above."
        )
        st.warning(summary)
    else:
        st.success(f"Done. {summary}")
        st.markdown(
            "Open the **Explore** page (sidebar nav) to see the heatmap, "
            "alternatives table, carrier mix, and any new alerts."
        )

# ---- Previous-run log (if any) ---------------------------------------------
last = st.session_state.get("last_run_result")
if last and not run:
    with st.expander("Show last run's logs", expanded=False):
        for step in ("sweep", "followup", "alerts"):
            log = (last.get("logs") or {}).get(step)
            if log:
                st.markdown(f"**{step}**")
                st.code(log)
