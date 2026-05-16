"""Run-jobs page — numbered workflow with cost previews before each click.

Workflow:
  Step 1 — Sweep    (discovers cheap dates, populates the calendar tables)
  Step 2 — Followup (point-queries the cheapest itineraries for carriers)
  Step 3 — Alerts   (evaluates price drops, no API calls)

Each step shows an estimated cost preview before you run it.
"""

from __future__ import annotations

import io
import logging
import sys
from contextlib import redirect_stdout
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lib import alerts as alerts_mod  # noqa: E402
from lib import followup as followup_mod  # noqa: E402
from lib import sweep as sweep_mod  # noqa: E402
from lib.searchapi_io import SearchApiClient  # noqa: E402
from lib.skyscanner_rapidapi import SkyScrapperClient  # noqa: E402
from ui._common import REPO as REPO_PATH, load_route_from_sidebar, setup_page  # noqa: E402

setup_page("Run jobs")
load_dotenv()
route, conn = load_route_from_sidebar()
ALERTS_LOG = REPO_PATH / "data" / "alerts.log"

st.title("Run jobs")

with st.expander("How this page works", expanded=False):
    st.markdown(
        """
**Recommended order: 1 → 2 → 3 → repeat every 2 weeks.**

1. **Sweep** asks the APIs for current prices across your whole search window.
   Sky Scrapper covers the year in 1 call per origin. SearchAPI covers it in
   ~40 calls (one per 14-day rectangle).
2. **Followup** picks the most promising itineraries from the sweep and asks
   for carrier / stops / duration detail.
3. **Alerts** compares each itinerary's current price to its own price history
   and fires a notification when it's dropped significantly. No API calls —
   pure SQL on your local DB.

Each step shows an estimated cost in API calls before you click. Sky Scrapper's
free tier on RapidAPI is small (you found out the hard way — apologies). The
defaults are conservative.
        """
    )

with st.sidebar:
    st.markdown("### Job options")
    sources = st.multiselect(
        "Sources",
        ["searchapi", "skyscanner"],
        default=["searchapi", "skyscanner"],
        help="Untick a source you've used up the quota on.",
    )
    dry_run = st.checkbox(
        "Dry run (no API calls)", value=False,
        help="Tick to see what would happen without spending budget.",
    )
    st.markdown("---")
    st.markdown("### Caps")
    searchapi_cap = st.number_input(
        "Max SearchAPI calls",
        min_value=0, max_value=100, value=10, step=1,
        help="0 = no cap. SearchAPI free tier is 100/mo.",
    )
    skyscanner_cap = st.number_input(
        "Max Sky Scrapper calls",
        min_value=0, max_value=100, value=4, step=1,
        help="0 = no cap. Sky Scrapper's free tier is small — start conservative.",
    )


def _run_with_log(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    prev_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        with redirect_stdout(buf):
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                buf.write(f"\n[ERROR] {type(exc).__name__}: {exc}\n")
    finally:
        root.removeHandler(handler)
        root.setLevel(prev_level)
    return buf.getvalue()


def _make_clients():
    sa = None
    sky = None
    if not dry_run:
        if "searchapi" in sources:
            try:
                sa = SearchApiClient.from_env()
            except RuntimeError as e:
                st.warning(f"SearchAPI disabled: {e}")
        if "skyscanner" in sources:
            try:
                sky = SkyScrapperClient.from_env(db_conn=conn)
            except RuntimeError as e:
                st.warning(f"Sky Scrapper disabled: {e}")
    return sa, sky


# ---------------------------------------------------------------- Step 1
st.markdown("---")
st.header("Step 1 · Sweep")
st.caption(
    "Discover cheap dates across the whole search window. Run every 2 weeks."
)

# Estimated cost for sweep.
windows = sweep_mod.plan_windows(route)
n_origins = len(route.origins)
n_dests = len(route.destinations)
n_pairs = n_origins * n_dests
# Sky Scrapper: 1 curve call per origin-dest pair + 1 airport-cache lookup
# per uncached IATA code on first run.
from lib import db as db_mod
uncached_iatas = sum(
    1 for code in {*route.origins, *route.destinations}
    if db_mod.lookup_airport(conn, code) is None
)
sky_cost_est = n_pairs + uncached_iatas
# SearchAPI: capped above; default 10 of ~40 possible.
sa_cost_est = min(searchapi_cap, len(windows)) if searchapi_cap else len(windows)

st.markdown(
    f"**Estimated cost:** ~{sa_cost_est} SearchAPI + ~{sky_cost_est} Sky Scrapper calls. "
    f"Plan has **{len(windows)} windows** total; SearchAPI cap above lets it run "
    f"only **{searchapi_cap or 'unlimited'}**."
)
if st.button("▶ Run sweep", type="primary", use_container_width=True, key="run_sweep"):
    sa, sky = _make_clients()
    def _go():
        result = sweep_mod.run_sweep(
            conn=conn,
            client=sa,
            route=route,
            max_calls=(searchapi_cap or None),
            dry_run=dry_run,
            skyscanner_client=sky,
            skyscanner_planned="skyscanner" in sources,
        )
        print(
            f"\nsweep summary: searchapi_calls={result.calls_made} "
            f"grid_rows={result.entries_stored} "
            f"skyscanner_calls={result.curve_calls_made} "
            f"curve_rows={result.curve_entries_stored}"
        )
    log = _run_with_log(_go)
    st.code(log or "(no output)")

# ---------------------------------------------------------------- Step 2
st.markdown("---")
st.header("Step 2 · Followup")
st.caption(
    "Drill into the cheapest itineraries: which carrier, how many stops, "
    "is it a virtual-interlining bundle."
)

# Count candidates before running.
candidates = followup_mod.select_candidates(conn, route)
n_candidates = len(candidates)
# SearchAPI: 1 call per candidate. Sky Scrapper: 1-2 per candidate.
sa_followup_est = min(searchapi_cap, n_candidates) if searchapi_cap else n_candidates
sky_followup_est = min(skyscanner_cap, n_candidates * 2) if skyscanner_cap else n_candidates * 2
st.markdown(
    f"**Candidates qualifying right now:** {n_candidates}. "
    f"**Estimated cost:** ~{sa_followup_est} SearchAPI + ~{sky_followup_est} Sky Scrapper calls."
)
if n_candidates == 0:
    st.info(
        "Zero candidates. Either run Sweep first, or your "
        "`followup.watch_below_price` threshold (currently "
        f"`{route.followup.watch_below_price} {route.currency}`) is below the "
        "cheapest price we've seen. Lower it in `routes/{route_name}.yaml` if "
        "you want followups regardless."
    )
elif n_candidates > 20:
    st.warning(
        f"⚠ {n_candidates} candidates is a lot. Without the Sky Scrapper cap "
        f"(currently {skyscanner_cap}), this would burn ~{n_candidates * 2} "
        "Sky Scrapper calls."
    )
if st.button("▶ Run followup", use_container_width=True, key="run_followup"):
    sa, sky = _make_clients()
    def _go():
        result = followup_mod.run_followup(
            conn=conn,
            client=sa,
            route=route,
            max_calls=(searchapi_cap or None),
            dry_run=dry_run,
            skyscanner_client=sky,
            skyscanner_max_calls=(skyscanner_cap or None),
        )
        print(
            f"\nfollowup summary: "
            f"candidates_total={result.candidates} "
            f"searchapi_calls={result.calls_made} "
            f"skyscanner_calls={result.skyscanner_calls} "
            f"itineraries_searchapi={result.itineraries_queried} "
            f"rows_stored={result.rows_stored}"
        )
    log = _run_with_log(_go)
    st.code(log or "(no output)")

# ---------------------------------------------------------------- Step 3
st.markdown("---")
st.header("Step 3 · Evaluate alerts")
st.caption("Local-only SQL. No API calls. Safe to run as often as you like.")

if st.button("▶ Evaluate alerts", use_container_width=True, key="run_alerts"):
    def _go():
        fired = alerts_mod.evaluate(
            conn=conn, route=route, log_path=ALERTS_LOG,
        )
        print(f"\nalerts evaluated: fired={len(fired)}")
    log = _run_with_log(_go)
    st.code(log or "(no output)")

st.markdown("---")
st.caption(
    "Jobs run synchronously in the Streamlit process — the page will appear "
    "frozen while a long sweep is in flight. Don't refresh."
)
