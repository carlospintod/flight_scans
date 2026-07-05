"""Tier 2: point-query followups on itineraries flagged by Tier 1.

Two candidate-selection modes:

* **Price-threshold mode** (active when both `followup.watch_below_price`
  and `followup.drop_above_price` are set in the route config). An
  itinerary is a candidate iff:
    1. Stay length falls within the configured range.
    2. The itinerary has been observed at or below `watch_below_price`
       at some point in its history.
    3. Its most recent observed price is at or below `drop_above_price`.
  This is the "track itineraries we've seen cheap, abandon them when
  they price out" strategy.

* **Legacy baseline-trigger mode** (fallback when either threshold is
  unset). An itinerary is a candidate when its most-recent snapshot
  either was flagged `is_lowest_price` or sits below the trailing
  baseline by `alerts.drop_threshold_pct`.

For each candidate we capture up to three `best_flights` from the
google_flights engine and store one row per rank.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .config import RouteConfig
from .db import (
    PointRow,
    calendar_history_for_itinerary,
    insert_point_rows,
    latest_calendar_snapshot_per_itinerary,
)
from .searchapi_io import SearchApiClient, SearchApiError, SOURCE_ID as SEARCHAPI_SOURCE
from .skyscanner_rapidapi import (
    SkyScrapperClient,
    SkyScrapperError,
    SOURCE_ID as SKYSCANNER_SOURCE,
)

LOG = logging.getLogger(__name__)

MAX_RANKS_TO_STORE = 3


@dataclass
class FollowupResult:
    candidates: int
    calls_made: int             # SearchAPI calls
    itineraries_queried: int
    rows_stored: int            # rows across both sources
    skyscanner_calls: int = 0
    skyscanner_rows: int = 0
    empty_results: int = 0      # SearchAPI queries that returned no flights


def select_candidates(conn, route: RouteConfig, *, today: date | None = None) -> list[dict]:
    """Return a list of candidate-itinerary dicts ready for point queries.

    Reads from the SearchAPI source of calendar_snapshots specifically
    (Sky Scrapper's departure curve doesn't carry return-date info, so
    we can't drive (dep, ret) candidate selection from it).

    Mode is selected by config: price-threshold if both
    `followup.watch_below_price` and `followup.drop_above_price` are
    set, otherwise legacy baseline-trigger.
    """
    today = today or date.today()
    sw = route.search_window
    min_stay = route.stay.min_days
    max_stay = route.stay.max_days
    watch_below = route.followup.watch_below_price
    drop_above = route.followup.drop_above_price
    price_mode = watch_below is not None and drop_above is not None
    # Latest feasible departure = latest_return - min_stay. Anything later
    # can't complete even the shortest acceptable trip inside the window.
    latest_dep = sw.latest_return - timedelta(days=min_stay)

    # Price mode only needs each itinerary's all-time minimum, which we
    # can fetch for EVERY itinerary in one GROUP BY — instead of one
    # history query per row. On the Turso HTTP backend each query is a
    # network round trip; with ~180 itineraries the per-row version made
    # quote recomputation take ~20s.
    min_by_itin: dict[tuple, int] = {}
    if price_mode:
        for r in conn.execute(
            """
            SELECT origin, destination, departure_date, return_date,
                   MIN(price) AS min_price
            FROM calendar_snapshots
            WHERE route_id = ? AND source = ?
            GROUP BY origin, destination, departure_date, return_date
            """,
            (route.name, SEARCHAPI_SOURCE),
        ).fetchall():
            key = (r["origin"], r["destination"],
                   r["departure_date"], r["return_date"])
            min_by_itin[key] = r["min_price"]

    out: list[dict] = []
    # Source-filter to SearchAPI: those are the rows with both dep & ret.
    for row in latest_calendar_snapshot_per_itinerary(
        conn, route.name, source=SEARCHAPI_SOURCE,
    ):
        stay = row["stay_days"]
        if stay < min_stay or stay > max_stay:
            continue

        # Search-window date filter: skip itineraries whose dates fall
        # outside the CURRENT window. Old snapshots persist in the DB
        # after the window is narrowed; without this filter they'd still
        # be point-queried (the "179 candidates" surprise).
        try:
            dep_d = date.fromisoformat(row["departure_date"])
            ret_d = date.fromisoformat(row["return_date"])
        except (ValueError, TypeError):
            continue
        if (dep_d < sw.earliest_departure or dep_d > latest_dep
                or ret_d > sw.latest_return):
            continue

        if price_mode:
            # Price-threshold candidate selection — uses the batched
            # per-itinerary minimum instead of a per-row history query.
            key = (row["origin"], row["destination"],
                   row["departure_date"], row["return_date"])
            all_time_min = min_by_itin.get(key)
            if all_time_min is None or all_time_min > watch_below:
                continue
            if row["price"] > drop_above:
                continue
            out.append({
                "origin": row["origin"],
                "destination": row["destination"],
                "departure_date": row["departure_date"],
                "return_date": row["return_date"],
                "snapshot_price": row["price"],
                "all_time_min": all_time_min,
                "trigger": "price_threshold",
            })
            continue

        # --- legacy baseline-trigger mode -----------------------------
        # This mode needs the trailing price history for THIS itinerary;
        # fetch it per-row (only reached when price thresholds are unset,
        # which the current spain-nairobi config never hits).
        history = calendar_history_for_itinerary(
            conn,
            route.name,
            row["origin"], row["destination"],
            row["departure_date"], row["return_date"],
            source=SEARCHAPI_SOURCE,
        )
        drop_pct = route.alerts.drop_threshold_pct
        min_obs = route.alerts.min_observations
        baseline_since = today - timedelta(days=route.alerts.baseline_window_days)
        is_lowest = bool(row["is_lowest_price"])
        prior = [
            r["price"] for r in history
            if r["snapshot_at"] >= baseline_since.isoformat()
            and r["snapshot_at"] < row["snapshot_at"]
        ]
        below_baseline = False
        if len(prior) >= min_obs:
            median = statistics.median(prior)
            if median > 0 and (median - row["price"]) / median * 100.0 >= drop_pct:
                below_baseline = True
        if not (is_lowest or below_baseline):
            continue
        out.append({
            "origin": row["origin"],
            "destination": row["destination"],
            "departure_date": row["departure_date"],
            "return_date": row["return_date"],
            "snapshot_price": row["price"],
            "is_lowest_price": is_lowest,
            "below_baseline": below_baseline,
            "prior_count": len(prior),
            "trigger": "baseline",
        })

    # Diversify across departure months instead of plain cheapest-first.
    #
    # With a global cheapest-first sort, a flat fare (e.g. one carrier
    # pricing 555 EUR across all of Nov-Feb) fills the entire capped
    # followup budget with near-identical itineraries of a single
    # carrier — 20 calls spent learning one fact. Round-robin across
    # months keeps "cheapest first" *within* each month while spreading
    # the budget over the whole search window, so the price-over-time
    # series covers every month and the carrier detail shows variety.
    #
    # Month queues are ordered by their cheapest candidate, so the
    # single globally-cheapest itinerary is still queried first.
    out.sort(key=lambda c: c["snapshot_price"])
    by_month: dict[str, list[dict]] = {}
    for c in out:
        by_month.setdefault(c["departure_date"][:7], []).append(c)
    queues = sorted(by_month.values(), key=lambda q: q[0]["snapshot_price"])
    interleaved: list[dict] = []
    while any(queues):
        for q in queues:
            if q:
                interleaved.append(q.pop(0))
    return interleaved


def run_followup(
    *,
    conn,
    client: SearchApiClient | None,
    route: RouteConfig,
    max_calls: int | None = None,
    dry_run: bool = False,
    skyscanner_client: SkyScrapperClient | None = None,
    skyscanner_max_calls: int | None = None,
    candidates: list[dict] | None = None,
) -> FollowupResult:
    """For each candidate itinerary, point-query both sources.

    `max_calls` caps SearchAPI point-queries. `skyscanner_max_calls`
    caps Sky Scrapper point-queries (each costs 1-2 API calls due to
    polling). Pass either as None to disable that source's cap;
    pass 0 to disable that source entirely for this run.

    `candidates`: when None (CLI path), self-selects + diversifies via
    select_candidates. When provided (RunPlan path), uses that exact list
    — the planner already selected, window-filtered, diversified, and
    capped it, so quote == execution.

    Candidates are processed cheapest-first, so a cap keeps the most
    promising itineraries and drops the marginal ones.
    """
    if candidates is None:
        candidates = select_candidates(conn, route)
    LOG.info("followup route=%s candidates=%d", route.name, len(candidates))

    if dry_run:
        for c in candidates:
            extra = ""
            if c.get("trigger") == "baseline":
                extra = (f" lowest={c.get('is_lowest_price')} "
                         f"below_baseline={c.get('below_baseline')}")
            else:
                extra = f" min_seen={c.get('all_time_min')}"
            LOG.info(
                "plan %s->%s dep=%s ret=%s price=%d%s",
                c["origin"], c["destination"],
                c["departure_date"], c["return_date"], c["snapshot_price"], extra,
            )
        return FollowupResult(
            candidates=len(candidates), calls_made=0,
            itineraries_queried=0, rows_stored=0,
        )

    sa_calls = 0
    sa_queried = 0
    sa_rows = 0
    sa_empty = 0
    sky_calls = 0
    sky_rows = 0
    snapshot_at = _now_iso()
    for c in candidates:
        if max_calls is not None and sa_calls >= max_calls:
            LOG.info("followup stopping at SearchAPI max_calls=%d", max_calls)
            break
        outbound = date.fromisoformat(c["departure_date"])
        return_ = date.fromisoformat(c["return_date"])

        # ---- Primary point-query client (SearchAPI, or any duck-typed
        # client exposing point_query + source_id, e.g. GoogleFlights) ----
        if client is not None:
            client_source = getattr(client, "source_id", SEARCHAPI_SOURCE)
            try:
                resp = client.point_query(
                    origin=c["origin"],
                    destination=c["destination"],
                    outbound=outbound,
                    return_=return_,
                    currency=route.currency,
                )
                rows = [
                    PointRow(
                        snapshot_at=snapshot_at,
                        route_id=route.name,
                        source=client_source,
                        origin=c["origin"],
                        destination=c["destination"],
                        departure_date=c["departure_date"],
                        return_date=c["return_date"],
                        rank=i,
                        price=f.price,
                        currency=route.currency,
                        carriers=f.carriers,
                        total_minutes=f.total_minutes,
                        stops=f.stops,
                        is_self_transfer=False,  # SearchAPI doesn't expose this
                    )
                    for i, f in enumerate(resp.best_flights[:MAX_RANKS_TO_STORE])
                ]
                sa_rows += insert_point_rows(conn, rows)
                sa_queried += 1
                if not rows:
                    # API answered but returned zero flights (e.g. "Google
                    # Flights API returned no results"). Burned a call,
                    # stored nothing — surface it instead of hiding it.
                    sa_empty += 1
                    LOG.warning(
                        "followup searchapi NO FLIGHTS %s->%s dep=%s ret=%s "
                        "(call spent, nothing stored)",
                        c["origin"], c["destination"],
                        c["departure_date"], c["return_date"],
                    )
                else:
                    LOG.info(
                        "followup searchapi %s->%s dep=%s ret=%s ranks=%d",
                        c["origin"], c["destination"],
                        c["departure_date"], c["return_date"], len(rows),
                    )
            except Exception as exc:  # noqa: BLE001 — client-specific error types
                # (SearchApiError, GoogleFlightsError, ...) all mean the
                # same thing here: this candidate produced nothing; move on.
                LOG.error(
                    "%s point_query failed %s->%s dep=%s ret=%s err=%s",
                    client_source, c["origin"], c["destination"],
                    c["departure_date"], c["return_date"], exc,
                )
            sa_calls += 1

        # ---- Sky Scrapper ----
        if (
            skyscanner_client is not None
            and (skyscanner_max_calls is None or sky_calls + 2 <= skyscanner_max_calls)
        ):
            try:
                sky_resp = skyscanner_client.point_query(
                    origin=c["origin"],
                    destination=c["destination"],
                    outbound=outbound,
                    return_=return_,
                    currency=route.currency,
                )
                # Conservative: kickoff + 1 poll = 2 calls in typical case.
                # If best_flights is empty after one poll, the kickoff still counted.
                sky_call_count = 2 if any(
                    True for _ in sky_resp.best_flights) else 1
                sky_calls += sky_call_count
                rows = [
                    PointRow(
                        snapshot_at=snapshot_at,
                        route_id=route.name,
                        source=SKYSCANNER_SOURCE,
                        origin=c["origin"],
                        destination=c["destination"],
                        departure_date=c["departure_date"],
                        return_date=c["return_date"],
                        rank=i,
                        price=f.price,
                        currency=route.currency,
                        carriers=f.carriers,
                        total_minutes=f.total_minutes,
                        stops=f.stops,
                        is_self_transfer=f.is_self_transfer,
                    )
                    for i, f in enumerate(sky_resp.best_flights[:MAX_RANKS_TO_STORE])
                ]
                sky_rows += insert_point_rows(conn, rows)
                LOG.info(
                    "followup skyscanner %s->%s dep=%s ret=%s ranks=%d "
                    "self_transfer=%s",
                    c["origin"], c["destination"],
                    c["departure_date"], c["return_date"], len(rows),
                    any(r.is_self_transfer for r in rows),
                )
            except SkyScrapperError as exc:
                LOG.error(
                    "skyscanner point_query failed %s->%s dep=%s ret=%s err=%s",
                    c["origin"], c["destination"],
                    c["departure_date"], c["return_date"], exc,
                )
                sky_calls += 1
        elif skyscanner_client is not None and skyscanner_max_calls is not None:
            LOG.info(
                "skyscanner skipping %s->%s dep=%s ret=%s (cap reached: %d)",
                c["origin"], c["destination"],
                c["departure_date"], c["return_date"], skyscanner_max_calls,
            )
    return FollowupResult(
        candidates=len(candidates),
        calls_made=sa_calls,
        itineraries_queried=sa_queried,
        rows_stored=sa_rows + sky_rows,
        skyscanner_calls=sky_calls,
        skyscanner_rows=sky_rows,
        empty_results=sa_empty,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
