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
    CalendarRow,
    PointRow,
    calendar_history_for_itinerary,
    insert_calendar_rows,
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

# Sources whose calendar rows are re-served CACHE data rather than live
# observations. They may nominate candidates (unique discovery value)
# but never displace a live source as an itinerary's current price.
CACHED_SOURCES = frozenset({"aviasales"})


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

    Source-AGNOSTIC: any calendar source that recorded both dep & ret
    dates can nominate a candidate (kiwi and aviasales discovery rows,
    googleflights/serpapi verification write-backs, legacy searchapi).
    It filtered to searchapi until 2026-07-07 — after that source was
    retired from CI, new searches would NEVER grow candidates and the
    owner's pool was silently starving as pre-retirement rows aged out
    of the window (red-team finding A1).

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
    # quote recomputation take ~20s. Minimum is across ALL sources: a
    # cheap fare seen by any discovery rail is worth verifying.
    min_by_itin: dict[tuple, int] = {}
    if price_mode:
        for r in conn.execute(
            """
            SELECT origin, destination, departure_date, return_date,
                   MIN(price) AS min_price
            FROM calendar_snapshots
            WHERE route_id = ?
            GROUP BY origin, destination, departure_date, return_date
            """,
            (route.name,),
        ).fetchall():
            key = (r["origin"], r["destination"],
                   r["departure_date"], r["return_date"])
            min_by_itin[key] = r["min_price"]

    # latest_calendar_snapshot_per_itinerary(source=None) yields one row
    # per (itinerary, source); collapse to one candidate per itinerary,
    # keeping the most recent LIVE observation (its price is the
    # "current" price the drop_above filter judges).
    #
    # Cached sources rank below live ones regardless of snapshot_at:
    # aviasales serves 2-7 day cached quotes (they carry their own
    # found_at, which scanops discards for scan time), and the CI run
    # order puts its sweep AFTER verification — so a stale cached fare
    # would win the freshness race by construction, wrongly excluding
    # (cached 850 masking a live 620) or including (zombie cheap fare
    # burning capped verifications) candidates, self-renewing every
    # scan. Found by the M0 adversarial audit, 2026-07-07. Cached-only
    # itineraries still nominate candidates — that discovery role
    # (carriers no live rail sees) is the point of keeping aviasales.
    best_row_by_itin: dict[tuple, object] = {}

    def _freshness_rank(row) -> tuple:
        return (row["source"] not in CACHED_SOURCES, row["snapshot_at"])

    for row in latest_calendar_snapshot_per_itinerary(conn, route.name):
        key = (row["origin"], row["destination"],
               row["departure_date"], row["return_date"])
        prev = best_row_by_itin.get(key)
        if prev is None or _freshness_rank(row) > _freshness_rank(prev):
            best_row_by_itin[key] = row

    out: list[dict] = []
    for row in best_row_by_itin.values():
        stay = row["stay_days"]
        if stay < min_stay or stay > max_stay:
            continue

        # Search-window date filter: skip itineraries whose dates fall
        # outside the CURRENT window. Old snapshots persist in the DB
        # after the window is narrowed; without this filter they'd still
        # be point-queried (the "179 candidates" surprise).
        #
        # One-way routes: rows carry the '' return sentinel — accept it
        # (return_=None downstream); latest_dep already equals
        # latest_return because min_stay is 0. A round-trip route still
        # drops sentinel rows here (fromisoformat('') raises).
        try:
            dep_d = date.fromisoformat(row["departure_date"])
            ret_d = (None if route.is_one_way and not row["return_date"]
                     else date.fromisoformat(row["return_date"]))
        except (ValueError, TypeError):
            continue
        if dep_d < sw.earliest_departure or dep_d > latest_dep:
            continue
        if ret_d is not None and ret_d > sw.latest_return:
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
            source=None,  # any source — searchapi retired (A1)
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
        # A malformed date must skip THIS candidate, not crash the whole
        # batch (red-team B3). The one-way '' sentinel is NOT malformed:
        # it maps to return_=None and runs a one-way point query (M7).
        try:
            outbound = date.fromisoformat(c["departure_date"])
            ret_raw = c["return_date"]
            return_ = date.fromisoformat(ret_raw) if ret_raw else None
        except (ValueError, TypeError, KeyError) as exc:
            LOG.warning("followup skipping candidate with unusable dates "
                        "%s->%s dep=%r ret=%r: %s",
                        c.get("origin"), c.get("destination"),
                        c.get("departure_date"), c.get("return_date"), exc)
            continue

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
                # A verified price IS a calendar observation of this exact
                # itinerary — write the cheapest (rank-0) option to
                # calendar_snapshots too, so the discovery board (which
                # reads that table) reflects fresh scans. Without this the
                # board froze on the last calendar-engine sweep: after
                # searchapi was retired and kiwi hit its quota, googleflights
                # verification wrote only point_queries and the "cheapest
                # right now" view showed month-old prices (observed
                # 2026-07-06: hero stuck at a June-9 532 EUR row).
                if rows:
                    best = rows[0]
                    # One-way ('' sentinel, return_ is None): stay 0 IS
                    # the valid shape — without this branch verified
                    # one-way prices would never reach the discovery
                    # board (the round-trip freeze bug all over again).
                    stay = ((return_ - outbound).days
                            if return_ is not None else 0)
                    if return_ is None or stay > 0:
                        insert_calendar_rows(conn, [CalendarRow(
                            snapshot_at=snapshot_at,
                            route_id=route.name,
                            source=client_source,
                            origin=c["origin"],
                            destination=c["destination"],
                            departure_date=c["departure_date"],
                            return_date=c["return_date"],
                            stay_days=stay,
                            price=best.price,
                            currency=route.currency,
                            is_lowest_price=False,
                        )])
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
                # A client that says it can't serve ANY query (browser
                # won't start, hard quota wall) fails the same way for
                # every remaining candidate — stop the batch with one
                # clear line instead of 24 more error stanzas.
                if type(exc).__name__ == "GoogleFlightsUnavailable":
                    LOG.error(
                        "%s unavailable — skipping remaining %d candidates: %s",
                        client_source, len(candidates) - sa_calls - 1, exc,
                    )
                    sa_calls += 1
                    client = None  # stop only THIS client; skyscanner (below) still runs
                    continue
                # Otherwise (SearchApiError, GoogleFlightsError, ...):
                # this candidate produced nothing; move on.
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
