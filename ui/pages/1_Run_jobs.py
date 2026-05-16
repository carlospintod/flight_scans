"""Run-jobs page: trigger sweeps, followups, alerts directly from the UI.

Each button calls the lib/ functions in-process — same code path as the
CLI. Output is captured and streamed back to the page log.
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
st.caption(
    "Each button calls the same code as the CLI. Watch the log below for output. "
    "All calls consume from your monthly free-tier budget — start with `Dry run` "
    "if you want to see what would happen."
)

with st.sidebar:
    st.markdown("### Job options")
    sources = st.multiselect(
        "Sources",
        ["searchapi", "skyscanner"],
        default=["searchapi", "skyscanner"],
    )
    dry_run = st.checkbox("Dry run (no API calls)", value=False)
    max_calls = st.number_input(
        "Max SearchAPI calls (sweep only)",
        min_value=0, max_value=100, value=0, step=1,
        help="0 = no cap. Useful for cost control on a fresh DB.",
    )


def _run_with_log(fn, *args, **kwargs) -> str:
    """Run fn under captured stdout + INFO-level logging; return the log text."""
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


col_sweep, col_followup, col_alerts = st.columns(3)

with col_sweep:
    if st.button("Run sweep", type="primary", use_container_width=True):
        sa, sky = _make_clients()
        def _go():
            result = sweep_mod.run_sweep(
                conn=conn,
                client=sa,
                route=route,
                max_calls=max_calls or None,
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

with col_followup:
    if st.button("Run followup", use_container_width=True):
        sa, sky = _make_clients()
        def _go():
            result = followup_mod.run_followup(
                conn=conn,
                client=sa,
                route=route,
                max_calls=max_calls or None,
                dry_run=dry_run,
                skyscanner_client=sky,
            )
            print(
                f"\nfollowup summary: "
                f"searchapi_calls={result.calls_made} "
                f"skyscanner_calls={result.skyscanner_calls} "
                f"itineraries_searchapi={result.itineraries_queried} "
                f"rows_stored={result.rows_stored}"
            )
        log = _run_with_log(_go)
        st.code(log or "(no output)")

with col_alerts:
    if st.button("Evaluate alerts", use_container_width=True):
        def _go():
            fired = alerts_mod.evaluate(
                conn=conn, route=route, log_path=ALERTS_LOG,
            )
            print(f"\nalerts evaluated: fired={len(fired)}")
        log = _run_with_log(_go)
        st.code(log or "(no output)")

st.markdown("---")
st.caption(
    "Note: button-triggered jobs run synchronously in the Streamlit process. "
    "A sweep with both sources enabled can take 1-3 minutes — the page will "
    "appear frozen while it runs. Don't refresh."
)
