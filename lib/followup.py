"""Tier 2: point-query followups on itineraries flagged by Tier 1.

An itinerary is a followup candidate when, from its most-recent calendar
snapshot, it:

  1. falls within the configured stay range, AND
  2. either was flagged `is_lowest_price` in its sweep, OR
     is below the trailing baseline by `drop_threshold_pct` (when we
     already have `min_observations` prior snapshots).

For each candidate we capture up to three `best_flights` from the
google_flights engine and store one row per rank.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .api import SearchApiClient, SearchApiError
from .config import RouteConfig
from .db import (
    PointRow,
    calendar_history_for_itinerary,
    insert_point_rows,
    latest_calendar_snapshot_per_itinerary,
)

LOG = logging.getLogger(__name__)

MAX_RANKS_TO_STORE = 3


@dataclass
class FollowupResult:
    candidates: int
    calls_made: int
    itineraries_queried: int
    rows_stored: int


def select_candidates(conn, route: RouteConfig, *, today: date | None = None) -> list[dict]:
    """Return a list of candidate-itinerary dicts ready for point queries."""
    today = today or date.today()
    baseline_since = today - timedelta(days=route.alerts.baseline_window_days)
    min_stay = route.stay.min_days
    max_stay = route.stay.max_days
    drop_pct = route.alerts.drop_threshold_pct
    min_obs = route.alerts.min_observations

    out: list[dict] = []
    for row in latest_calendar_snapshot_per_itinerary(conn, route.name):
        stay = row["stay_days"]
        if stay < min_stay or stay > max_stay:
            continue
        is_lowest = bool(row["is_lowest_price"])
        below_baseline = False
        history = calendar_history_for_itinerary(
            conn,
            route.name,
            row["origin"], row["destination"],
            row["departure_date"], row["return_date"],
            since=baseline_since,
        )
        prior = [r["price"] for r in history if r["snapshot_at"] < row["snapshot_at"]]
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
        })
    # Sort cheapest-first so when capped by max_calls we keep the best signals.
    out.sort(key=lambda c: c["snapshot_price"])
    return out


def run_followup(
    *,
    conn,
    client: SearchApiClient,
    route: RouteConfig,
    max_calls: int | None = None,
    dry_run: bool = False,
) -> FollowupResult:
    candidates = select_candidates(conn, route)
    LOG.info("followup route=%s candidates=%d", route.name, len(candidates))

    if dry_run:
        for c in candidates:
            LOG.info(
                "plan %s->%s dep=%s ret=%s price=%d lowest=%s below_baseline=%s",
                c["origin"], c["destination"],
                c["departure_date"], c["return_date"], c["snapshot_price"],
                c["is_lowest_price"], c["below_baseline"],
            )
        return FollowupResult(
            candidates=len(candidates), calls_made=0,
            itineraries_queried=0, rows_stored=0,
        )

    calls = 0
    queried = 0
    rows_stored = 0
    snapshot_at = _now_iso()
    for c in candidates:
        if max_calls is not None and calls >= max_calls:
            LOG.info("followup stopping at max_calls=%d", max_calls)
            break
        try:
            resp = client.point_query(
                origin=c["origin"],
                destination=c["destination"],
                outbound=date.fromisoformat(c["departure_date"]),
                return_=date.fromisoformat(c["return_date"]),
                currency=route.currency,
            )
        except SearchApiError as exc:
            LOG.error(
                "followup call failed %s->%s dep=%s ret=%s err=%s",
                c["origin"], c["destination"],
                c["departure_date"], c["return_date"], exc,
            )
            calls += 1
            continue
        calls += 1
        queried += 1
        rows = [
            PointRow(
                snapshot_at=snapshot_at,
                route_id=route.name,
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
            )
            for i, f in enumerate(resp.best_flights[:MAX_RANKS_TO_STORE])
        ]
        rows_stored += insert_point_rows(conn, rows)
        LOG.info(
            "followup stored %s->%s dep=%s ret=%s ranks=%d",
            c["origin"], c["destination"],
            c["departure_date"], c["return_date"], len(rows),
        )
    return FollowupResult(
        candidates=len(candidates),
        calls_made=calls,
        itineraries_queried=queried,
        rows_stored=rows_stored,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
