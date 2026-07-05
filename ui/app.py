"""Flight tracker — Search page (entry point).

`streamlit run ui/app.py`. A search-engine-style single page:

    SEARCH FORM  →  LIVE QUOTE  →  RUN  →  RESULTS

The form's values persist in session_state (seeded from the route YAML)
and can be written back with "Save as default". The quote is built by
lib.planner.build_run_plan and RUN executes that exact plan, so what you
see quoted is what you spend. Results render below the button.

Analysis extras (price history, alerts, Sky Scrapper curve, quota admin)
live on the History page (pages/1_History.py).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lib import config as config_mod  # noqa: E402
from lib.planner import Caps, build_run_plan  # noqa: E402
from ui._common import (  # noqa: E402
    apply_overrides,
    carrier_mix,
    latest_grid_for_heatmap,
    latest_quota_for_ui,
    load_route_from_sidebar,
    run_all,
    setup_page,
    terminal_header,
    top_alternatives,
    ROUTES_DIR,
)

setup_page("Search")
base_route, conn = load_route_from_sidebar()
ALERTS_LOG = REPO / "data" / "alerts.log"

terminal_header(
    "FLIGHT_TRACKER // SEARCH",
    subtitle="Set your search, check the quote, run it. Results appear below.",
)

# ============================================================================
# Search form — values persist in session_state, seeded once from the YAML.
# ============================================================================
ss = st.session_state
sw = base_route.search_window
ss.setdefault("q_origins", list(base_route.origins))
ss.setdefault("q_destinations", list(base_route.destinations))
ss.setdefault("q_earliest", sw.earliest_departure)
ss.setdefault("q_latest", sw.latest_return)
ss.setdefault("q_stay_min", base_route.stay.min_days)
ss.setdefault("q_stay_max", base_route.stay.max_days)
ss.setdefault("q_sources", ["searchapi", "aviasales"])
ss.setdefault("q_cap_sweep", 40)
ss.setdefault("q_cap_followup", 15)
ss.setdefault("q_cap_kiwi", 20)
ss.setdefault("q_dry_run", False)

st.markdown("## Search")
with st.container(border=True):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.multiselect("Origins", options=list(base_route.origins), key="q_origins")
        st.date_input("Earliest departure", key="q_earliest")
        st.number_input("Min stay (days)", min_value=1, max_value=365, key="q_stay_min")
    with c2:
        st.multiselect("Destinations", options=list(base_route.destinations),
                       key="q_destinations")
        st.date_input("Latest return", key="q_latest")
        st.number_input("Max stay (days)", min_value=1, max_value=365, key="q_stay_max")
    with c3:
        st.multiselect(
            "Data sources", ["searchapi", "skyscanner", "aviasales", "kiwi"],
            key="q_sources",
            help=(
                "SearchAPI (Google Flights) + Aviasales (Saudia/MENA) are the "
                "default. Sky Scrapper's 20/mo tier exhausts fast; Kiwi adds "
                "virtual-interlining bundles."
            ),
        )
        st.checkbox("Dry run (quote only, no API calls)", key="q_dry_run")

    with st.expander("Call budget (caps per run)", expanded=False):
        b1, b2, b3 = st.columns(3)
        b1.number_input("Max sweep calls", min_value=0, max_value=200, step=5,
                        key="q_cap_sweep")
        b2.number_input("Max followup calls", min_value=0, max_value=100, step=5,
                        key="q_cap_followup")
        b3.number_input("Max Kiwi calls", min_value=0, max_value=100, step=5,
                        key="q_cap_kiwi")

# Build the effective route from the form.
try:
    route = apply_overrides(
        base_route,
        earliest_departure=ss["q_earliest"],
        latest_return=ss["q_latest"],
        min_stay_days=int(ss["q_stay_min"]),
        max_stay_days=int(ss["q_stay_max"]),
        origins=tuple(ss["q_origins"]) if ss["q_origins"] else None,
        destinations=tuple(ss["q_destinations"]) if ss["q_destinations"] else None,
    )
    route_valid = True
except ValueError as exc:
    st.error(f"Invalid search: {exc}")
    route = base_route
    route_valid = False

save_col, _ = st.columns([1, 3])
with save_col:
    if st.button("Save as default", use_container_width=True,
                 help="Write these settings back to the route's YAML file.",
                 disabled=not route_valid):
        try:
            config_mod.save_route(ROUTES_DIR / f"{base_route.name}.yaml", route)
            st.toast("Saved to YAML — this is now the default for CLI + reloads.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Save failed: {exc}")

# ============================================================================
# Live quote — one RunPlan, rendered here and executed verbatim by RUN.
# ============================================================================
st.markdown("## Quote")
sources = list(ss["q_sources"])
caps = Caps(
    searchapi_sweep=int(ss["q_cap_sweep"]) or None,
    searchapi_followup=int(ss["q_cap_followup"]) or None,
    skyscanner=None,
    kiwi=int(ss["q_cap_kiwi"]) or None,
)

plan = None
if route_valid and sources:
    try:
        plan = build_run_plan(conn, route, sources=sources, caps=caps,
                              today=date.today())
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not build a plan: {exc}")

_SRC_LABEL = {"searchapi": "SearchAPI", "skyscanner": "Sky Scrapper",
              "aviasales": "Aviasales", "kiwi": "Kiwi"}


def _remaining(src: str) -> int | None:
    q = latest_quota_for_ui(conn, src)
    return q["remaining"] if q and q.get("remaining") is not None else None


over_budget = False
if plan is None:
    st.info("Pick at least one source and a valid window to see the quote.")
else:
    rows = []
    for src in sources:
        planned = plan.calls_by_source.get(src, 0)
        rem = _remaining(src)
        after = (rem - planned) if rem is not None else None
        if rem is not None and planned > rem:
            over_budget = True
        rows.append({
            "source": _SRC_LABEL.get(src, src),
            "planned": planned,
            "remaining": "unknown" if rem is None else rem,
            "after run": "—" if after is None else after,
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    for note in plan.notes:
        st.caption(f"• {note}")

    if over_budget:
        st.warning(
            "This run would exceed the remaining quota on a highlighted "
            "source. Lower the caps above or narrow the window before running."
        )

# ============================================================================
# RUN
# ============================================================================
run = st.button(
    "RUN SEARCH",
    type="primary", use_container_width=True,
    disabled=not (route_valid and plan is not None),
)

if run and plan is not None:
    result = run_all(
        conn=conn,
        route=route,
        sources=sources,
        searchapi_cap=0,        # caps already baked into the plan
        skyscanner_cap=0,
        dry_run=bool(ss["q_dry_run"]),
        alerts_log=ALERTS_LOG,
        plan=plan,
    )
    ss["last_run_result"] = result
    errors = result.get("errors") or []
    if errors:
        st.error(f"Run finished with errors in: {', '.join(s for s, _ in errors)}.")
    elif ss["q_dry_run"]:
        st.info("Dry run complete — no API calls were made and nothing was written.")
    else:
        st.success("Done. Results below reflect the latest data.")

# ============================================================================
# Results — cheapest combos for the current search.
# ============================================================================
st.markdown("## Results")

src_filter = None
if len(sources) == 1:
    src_filter = sources[0]

df = top_alternatives(
    conn, route,
    source=src_filter,
    min_stay=route.stay.min_days, max_stay=route.stay.max_days,
    earliest=route.search_window.earliest_departure.isoformat(),
    latest=route.search_window.latest_return.isoformat(),
    limit=10,
)

if df.empty:
    st.info(
        "No priced itineraries for this search yet. Run a search above to "
        "populate — the first sweep takes a minute."
    )
else:
    # --- Hero: the single cheapest combo ---
    best = df.iloc[0]
    dur = ""
    if pd.notna(best.get("total_minutes")):
        h, m = divmod(int(best["total_minutes"]), 60)
        dur = f"  ·  {h}h{m:02d}m"
    carrier = best.get("top_carrier")
    carrier_str = f"  ·  {carrier}" if pd.notna(carrier) and carrier else ""
    st.markdown(
        f"### Cheapest: **{best['price']} {best['currency']}**  "
        f"— {best['origin']}→{best['destination']}  "
        f"dep `{best['departure_date']}` · ret `{best['return_date']}` "
        f"({int(best['stay_days'])}d){carrier_str}{dur}"
    )

    # --- Top-10 alternatives ---
    st.markdown("**Top alternatives (cheapest per departure day)**")
    show = df.copy()
    if "total_minutes" in show.columns:
        show["duration"] = show["total_minutes"].apply(
            lambda x: f"{int(x)//60}h{int(x)%60:02d}m" if pd.notna(x) else "")
        show = show.drop(columns=["total_minutes"])
    if "is_self_transfer" in show.columns:
        show["self_xfer"] = show["is_self_transfer"].apply(lambda v: "✓" if v else "")
        show = show.drop(columns=["is_self_transfer"])
    st.dataframe(show, hide_index=True, use_container_width=True)

    # --- Heatmap + carrier mix ---
    hc1, hc2 = st.columns([3, 2])
    with hc1:
        st.markdown("**Price heatmap** (departure × stay length)")
        origin_for_heat = route.origins[0]
        grid = latest_grid_for_heatmap(
            conn, route, origin=origin_for_heat, source="searchapi",
            min_stay=route.stay.min_days, max_stay=route.stay.max_days,
            earliest=route.search_window.earliest_departure.isoformat(),
            latest=route.search_window.latest_return.isoformat(),
        )
        if grid.empty:
            st.caption(f"No grid data for {origin_for_heat} yet.")
        else:
            import altair as alt
            grid["departure_date"] = pd.to_datetime(grid["departure_date"])
            chart = (
                alt.Chart(grid).mark_rect().encode(
                    x=alt.X("departure_date:T", title="Departure"),
                    y=alt.Y("stay_days:O", title="Stay (days)"),
                    color=alt.Color("price:Q", title=f"Price ({route.currency})",
                                    scale=alt.Scale(scheme="viridis", reverse=True)),
                    tooltip=["departure_date:T", "stay_days:O", "price:Q"],
                ).properties(height=300)
            )
            st.altair_chart(chart, use_container_width=True)
            st.caption(f"{origin_for_heat}→{route.destinations[0]} · "
                       f"{len(grid):,} scanned cells")

    with hc2:
        st.markdown("**Carrier mix**")
        cm = carrier_mix(
            conn, route, source=src_filter,
            min_stay=route.stay.min_days, max_stay=route.stay.max_days,
            earliest=route.search_window.earliest_departure.isoformat(),
            latest=route.search_window.latest_return.isoformat(),
        )
        if cm.empty:
            st.caption("No carrier detail yet — run a followup.")
        else:
            import altair as alt
            chart = (
                alt.Chart(cm).mark_bar().encode(
                    y=alt.Y("carriers:N", sort="-x", title=None),
                    x=alt.X("n:Q", title="Times cheapest"),
                    tooltip=["carriers", "n"],
                ).properties(height=max(140, 26 * len(cm)))
            )
            st.altair_chart(chart, use_container_width=True)
