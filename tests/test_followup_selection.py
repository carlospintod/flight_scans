"""Test the followup candidate-selection logic.

Both modes are exercised: price-threshold (the new default for this
route) and the legacy baseline-trigger fallback.
"""

from datetime import date, datetime
from pathlib import Path

from lib.config import (
    AlertParams,
    FollowupParams,
    RouteConfig,
    SearchWindow,
    StayPreferences,
    SweepParams,
)
from lib.db import CalendarRow, connect, ensure_schema, insert_calendar_rows, upsert_route
from lib.followup import select_candidates


def _route(*, watch=600, drop=800, min_stay=60, max_stay=90) -> RouteConfig:
    return RouteConfig(
        name="t",
        origins=("MAD",),
        destinations=("NBO",),
        search_window=SearchWindow(
            earliest_departure=date(2026, 9, 1),
            latest_return=date(2027, 5, 31),
        ),
        stay=StayPreferences(min_days=min_stay, max_days=max_stay),
        currency="EUR",
        sweep=SweepParams(14, 14, 3, 14),
        followup=FollowupParams(watch_below_price=watch, drop_above_price=drop),
        alerts=AlertParams(15, 30, 4),
    )


def _row(snapshot: datetime, dep: str, ret: str, stay: int, price: int) -> CalendarRow:
    return CalendarRow(
        snapshot_at=snapshot.strftime("%Y-%m-%dT%H:%M:%SZ"),
        route_id="t",
        source="searchapi",
        origin="MAD",
        destination="NBO",
        departure_date=dep,
        return_date=ret,
        stay_days=stay,
        price=price,
        currency="EUR",
        is_lowest_price=False,
    )


def test_price_mode_picks_itineraries_seen_cheap_and_still_affordable(tmp_path: Path):
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        insert_calendar_rows(conn, [
            # Itinerary A: was once 580 (under 600), now 720 (under 800). KEEP.
            _row(datetime(2026, 5, 1), "2026-09-05", "2026-11-08", 64, 580),
            _row(datetime(2026, 5, 15), "2026-09-05", "2026-11-08", 64, 720),
            # Itinerary B: only ever expensive. DROP (never below 600).
            _row(datetime(2026, 5, 1), "2026-09-10", "2026-11-15", 66, 900),
            _row(datetime(2026, 5, 15), "2026-09-10", "2026-11-15", 66, 850),
            # Itinerary C: was cheap, but now blown out (>800). DROP.
            _row(datetime(2026, 5, 1), "2026-09-12", "2026-11-20", 69, 540),
            _row(datetime(2026, 5, 15), "2026-09-12", "2026-11-20", 69, 870),
            # Itinerary D: outside stay range. DROP.
            _row(datetime(2026, 5, 1), "2026-09-05", "2026-09-30", 25, 450),
        ])
        candidates = select_candidates(conn, _route(), today=date(2026, 5, 16))

    keys = {(c["departure_date"], c["return_date"]) for c in candidates}
    assert ("2026-09-05", "2026-11-08") in keys
    assert ("2026-09-10", "2026-11-15") not in keys
    assert ("2026-09-12", "2026-11-20") not in keys
    assert ("2026-09-05", "2026-09-30") not in keys
    # Only A qualifies.
    assert len(candidates) == 1
    a = candidates[0]
    assert a["trigger"] == "price_threshold"
    assert a["snapshot_price"] == 720
    assert a["all_time_min"] == 580


def test_price_mode_handles_single_observation(tmp_path: Path):
    """A single cheap observation is enough to put an itinerary on the watch list."""
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        insert_calendar_rows(conn, [
            _row(datetime(2026, 5, 1), "2026-09-05", "2026-11-08", 64, 590),
        ])
        candidates = select_candidates(conn, _route(), today=date(2026, 5, 16))
    assert len(candidates) == 1


def test_out_of_window_itineraries_excluded(tmp_path: Path):
    """Itineraries departing before earliest or after latest-feasible, or
    returning after latest_return, are excluded even if price/stay match.

    Regression for the narrowed-window surprise: old snapshots persisted
    after the user shrank the window and were still point-queried.
    """
    db = tmp_path / "t.db"
    # Window Sep 1 2026 -> Dec 20 2026; min_stay 60 -> latest feasible
    # departure = Dec 20 - 60 = Oct 21 2026.
    route = _route(min_stay=60, max_stay=90)
    from dataclasses import replace
    route = replace(route, search_window=SearchWindow(
        earliest_departure=date(2026, 9, 1),
        latest_return=date(2026, 12, 20),
    ))
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [
            # In window: dep Sep 5, ret Nov 8 (64d) -> KEEP
            _row(datetime(2026, 6, 1), "2026-09-05", "2026-11-08", 64, 500),
            # Departs before window start -> DROP
            _row(datetime(2026, 6, 1), "2026-08-20", "2026-10-25", 66, 480),
            # Departs after latest-feasible (Nov 5 + 60 = past Dec 20) -> DROP
            _row(datetime(2026, 6, 1), "2026-11-05", "2027-01-08", 64, 470),
            # Returns after latest_return -> DROP (dep Oct 15 in range but
            # ret Dec 25 > Dec 20)
            _row(datetime(2026, 6, 1), "2026-10-15", "2026-12-25", 71, 460),
        ])
        candidates = select_candidates(conn, route, today=date(2026, 6, 15))

    keys = {(c["departure_date"], c["return_date"]) for c in candidates}
    assert ("2026-09-05", "2026-11-08") in keys
    assert ("2026-08-20", "2026-10-25") not in keys
    assert ("2026-11-05", "2027-01-08") not in keys
    assert ("2026-10-15", "2026-12-25") not in keys
    assert len(candidates) == 1


def test_candidates_round_robin_across_departure_months(tmp_path: Path):
    """Candidates must interleave departure months, not sort purely by price.

    Regression for the flat-fare monopoly: one carrier pricing 555 across
    all of Nov-Feb made the 20 cheapest candidates all-November clones,
    so a capped followup spent its whole budget learning one fact. With
    round-robin, budget spreads across the window.
    """
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        insert_calendar_rows(conn, [
            # September itineraries (cheapest month overall)
            _row(datetime(2026, 6, 1), "2026-09-05", "2026-11-08", 64, 500),
            _row(datetime(2026, 6, 1), "2026-09-10", "2026-11-15", 66, 510),
            # November itineraries (more expensive, would lose a pure
            # price sort entirely until Sep exhausted)
            _row(datetime(2026, 6, 1), "2026-11-05", "2027-01-08", 64, 550),
            _row(datetime(2026, 6, 1), "2026-11-10", "2027-01-15", 66, 560),
        ])
        candidates = select_candidates(conn, _route(), today=date(2026, 6, 15))

    months = [c["departure_date"][:7] for c in candidates]
    prices = [c["snapshot_price"] for c in candidates]
    # Global cheapest still first; then months alternate.
    assert prices[0] == 500
    assert months == ["2026-09", "2026-11", "2026-09", "2026-11"]
    assert prices == [500, 550, 510, 560]


def test_legacy_mode_when_thresholds_unset(tmp_path: Path):
    db = tmp_path / "t.db"
    route = _route(watch=None, drop=None)
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [
            # Itinerary flagged is_lowest_price in latest snapshot.
            CalendarRow(
                snapshot_at="2026-05-01T00:00:00Z",
                route_id="t", source="searchapi", origin="MAD", destination="NBO",
                departure_date="2026-09-05", return_date="2026-11-08",
                stay_days=64, price=720, currency="EUR",
                is_lowest_price=True,
            ),
        ])
        candidates = select_candidates(conn, route, today=date(2026, 5, 16))
    assert len(candidates) == 1
    assert candidates[0]["trigger"] == "baseline"
    assert candidates[0]["is_lowest_price"] is True


def test_unavailable_client_aborts_batch_after_first_failure(tmp_path: Path):
    """A browser that can't launch fails identically for every candidate.

    Regression for 2026-07-06: a missing Chromium executable produced 25
    error stanzas (24 of them the misleading playwright asyncio message)
    instead of one. The batch must stop after the FIRST unavailability."""
    from lib.followup import run_followup
    from lib.googleflights_direct import GoogleFlightsUnavailable

    class DeadClient:
        source_id = "googleflights"
        calls = 0

        def point_query(self, **kwargs):
            DeadClient.calls += 1
            raise GoogleFlightsUnavailable("browser launch failed: no exe")

    cands = [
        {"origin": "MAD", "destination": "NBO",
         "departure_date": f"2026-09-{d:02d}", "return_date": f"2026-11-{d:02d}"}
        for d in (5, 10, 15)
    ]
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        result = run_followup(conn=conn, client=DeadClient(), route=_route(),
                              candidates=cands)

    assert DeadClient.calls == 1        # stopped after the first failure
    assert result.rows_stored == 0


def test_followup_writes_calendar_row_for_verified_price(tmp_path: Path):
    """A verified point-query price is also a calendar observation, so the
    discovery board reflects fresh scans. Regression for 2026-07-06: the
    'cheapest right now' board froze on month-old searchapi data because
    googleflights verification wrote only point_queries."""
    from lib.followup import run_followup
    from lib.searchapi_io import FlightOption, PointResponse

    class FakeGF:
        source_id = "googleflights"

        def point_query(self, **kwargs):
            return PointResponse(raw={}, best_flights=(
                FlightOption(price=567, total_minutes=820, stops=1,
                             carriers="Etihad"),
                FlightOption(price=690, total_minutes=825, stops=1,
                             carriers="Qatar Airways"),
            ))

    cands = [{"origin": "MAD", "destination": "NBO",
              "departure_date": "2026-09-15", "return_date": "2026-11-17"}]
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        run_followup(conn=conn, client=FakeGF(), route=_route(),
                     candidates=cands)
        cal = conn.execute(
            "SELECT source, price, stay_days FROM calendar_snapshots "
            "WHERE route_id='t' AND source='googleflights'").fetchall()
        pq = conn.execute(
            "SELECT COUNT(*) FROM point_queries WHERE source='googleflights'"
        ).fetchone()[0]
    assert len(cal) == 1                      # one calendar row (rank-0 only)
    assert cal[0]["price"] == 567             # the cheapest option
    assert cal[0]["stay_days"] == 63          # Sep15 -> Nov17
    assert pq == 2                            # both ranks still in point_queries


def test_candidates_from_kiwi_only_history(tmp_path: Path):
    """Candidate selection must be source-agnostic. Until 2026-07-07 it
    filtered to source='searchapi' — retired from CI — so a search whose
    history came only from kiwi/googleflights discovery would NEVER grow
    verification candidates and its alert stream starved (red-team A1)."""
    db = tmp_path / "t.db"
    rows = []
    for src in ("kiwi", "googleflights"):
        r = _row(datetime(2026, 5, 1), "2026-09-05", "2026-11-08", 64, 580)
        rows.append(CalendarRow(**{**r.__dict__, "source": src}))
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        insert_calendar_rows(conn, rows)
        candidates = select_candidates(conn, _route(), today=date(2026, 5, 16))
    assert len(candidates) == 1              # one itinerary, deduped across sources
    assert candidates[0]["departure_date"] == "2026-09-05"
    assert candidates[0]["all_time_min"] == 580


def test_candidates_deduped_across_sources_latest_wins(tmp_path: Path):
    """One candidate per itinerary even when several sources observed it;
    the most recent observation supplies the current price."""
    db = tmp_path / "t.db"
    base = _row(datetime(2026, 5, 1), "2026-09-05", "2026-11-08", 64, 580)
    newer = _row(datetime(2026, 5, 15), "2026-09-05", "2026-11-08", 64, 720)
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        insert_calendar_rows(conn, [
            CalendarRow(**{**base.__dict__, "source": "kiwi"}),
            CalendarRow(**{**newer.__dict__, "source": "googleflights"}),
        ])
        candidates = select_candidates(conn, _route(), today=date(2026, 5, 16))
    assert len(candidates) == 1
    assert candidates[0]["snapshot_price"] == 720   # latest across sources
    assert candidates[0]["all_time_min"] == 580     # min across sources


def test_cached_source_never_displaces_live_current_price(tmp_path: Path):
    """A stale aviasales cache quote re-stamped with a newer scan time
    must NOT hijack an itinerary's current price: a live 620 with an
    850 cached row on top stays a candidate; a live 900 with a zombie
    cached 640 on top stays excluded (M0 adversarial audit, 2026-07-07)."""
    db = tmp_path / "t.db"
    live_cheap = _row(datetime(2026, 5, 10), "2026-09-05", "2026-11-08", 64, 620)
    cached_high = _row(datetime(2026, 5, 15), "2026-09-05", "2026-11-08", 64, 850)
    live_high = _row(datetime(2026, 5, 10), "2026-09-10", "2026-11-15", 66, 900)
    cached_zombie = _row(datetime(2026, 5, 15), "2026-09-10", "2026-11-15", 66, 640)
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        insert_calendar_rows(conn, [
            CalendarRow(**{**live_cheap.__dict__, "source": "googleflights"}),
            CalendarRow(**{**cached_high.__dict__, "source": "aviasales"}),
            CalendarRow(**{**live_high.__dict__, "source": "googleflights"}),
            CalendarRow(**{**cached_zombie.__dict__, "source": "aviasales"}),
        ])
        candidates = select_candidates(conn, _route(watch=650),
                                       today=date(2026, 5, 16))
    keys = {(c["departure_date"], c["snapshot_price"]) for c in candidates}
    assert ("2026-09-05", 620) in keys       # live price wins despite older stamp
    assert not any(d == "2026-09-10" for d, _ in keys)  # zombie can't resurrect


def test_cached_only_itinerary_still_nominates(tmp_path: Path):
    """Aviasales' discovery value survives: an itinerary ONLY it has
    seen still becomes a candidate."""
    db = tmp_path / "t.db"
    r = _row(datetime(2026, 5, 10), "2026-09-05", "2026-11-08", 64, 580)
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        insert_calendar_rows(conn, [
            CalendarRow(**{**r.__dict__, "source": "aviasales"}),
        ])
        candidates = select_candidates(conn, _route(), today=date(2026, 5, 16))
    assert len(candidates) == 1
    assert candidates[0]["snapshot_price"] == 580


# ---------------------------------------------------------------------------
# One-way (M7): sentinel-aware selection + execution


def _one_way_route(*, watch=600, drop=800) -> RouteConfig:
    return RouteConfig(
        name="t",
        origins=("MAD",),
        destinations=("NBO",),
        search_window=SearchWindow(
            earliest_departure=date(2026, 9, 1),
            latest_return=date(2027, 5, 31),
        ),
        stay=StayPreferences(min_days=0, max_days=0),
        currency="EUR",
        sweep=SweepParams(14, 14, 3, 14),
        followup=FollowupParams(watch_below_price=watch, drop_above_price=drop),
        alerts=AlertParams(15, 30, 4),
        trip_type="one_way",
    )


def _ow_row(snapshot: datetime, dep: str, price: int,
            source: str = "kiwi") -> CalendarRow:
    return CalendarRow(
        snapshot_at=snapshot.strftime("%Y-%m-%dT%H:%M:%SZ"),
        route_id="t", source=source, origin="MAD", destination="NBO",
        departure_date=dep, return_date="", stay_days=0,
        price=price, currency="EUR", is_lowest_price=False,
    )


def test_one_way_sentinel_rows_survive_selection(tmp_path: Path):
    """Regression for the M6 gap: fromisoformat('') silently dropped
    EVERY one-way row, so the (new in M7) one-way verification rail
    would have looked enabled but never fired."""
    db = tmp_path / "t.db"
    route = _one_way_route()
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [
            _ow_row(datetime(2026, 7, 1), "2026-09-20", 310),
            _ow_row(datetime(2026, 7, 2), "2026-09-20", 330),
            # never under the watch bar -> not a candidate
            _ow_row(datetime(2026, 7, 1), "2026-09-25", 900),
        ])
        candidates = select_candidates(conn, route, today=date(2026, 7, 3))
    assert [(c["departure_date"], c["return_date"]) for c in candidates] \
        == [("2026-09-20", "")]


def test_round_trip_route_still_drops_sentinel_rows(tmp_path: Path):
    """A round-trip route must never verify one-way sentinel rows, even
    when its stay filter would let stay_days=0 through."""
    db = tmp_path / "t.db"
    route = _route(min_stay=0, max_stay=90)
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [
            CalendarRow(
                snapshot_at="2026-07-01T00:00:00Z", route_id="t",
                source="kiwi", origin="MAD", destination="NBO",
                departure_date="2026-09-20", return_date="", stay_days=0,
                price=310, currency="EUR", is_lowest_price=False,
            ),
        ])
        candidates = select_candidates(conn, route, today=date(2026, 7, 3))
    assert candidates == []


def test_one_way_followup_queries_none_return_and_writes_sentinel(tmp_path: Path):
    """One-way verification end-to-end: the '' candidate becomes
    point_query(return_=None), and the verified price lands in
    calendar_snapshots with the ''/stay 0 sentinel so the one-way curve
    reflects fresh scans (the round-trip freeze bug, one-way edition)."""
    from lib.followup import run_followup
    from lib.searchapi_io import FlightOption, PointResponse

    captured = {}

    class FakeGF:
        source_id = "googleflights"

        def point_query(self, **kwargs):
            captured.update(kwargs)
            return PointResponse(raw={}, best_flights=(
                FlightOption(price=301, total_minutes=1105, stops=1,
                             carriers="Etihad"),
            ))

    cands = [{"origin": "MAD", "destination": "NBO",
              "departure_date": "2026-09-20", "return_date": ""}]
    db = tmp_path / "t.db"
    route = _one_way_route()
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        res = run_followup(conn=conn, client=FakeGF(), route=route,
                           candidates=cands)
        cal = conn.execute(
            "SELECT return_date, stay_days, price FROM calendar_snapshots "
            "WHERE route_id='t' AND source='googleflights'").fetchall()
        pq = conn.execute(
            "SELECT return_date FROM point_queries "
            "WHERE source='googleflights'").fetchall()
    assert captured["return_"] is None
    assert res.itineraries_queried == 1
    assert len(cal) == 1
    assert (cal[0]["return_date"], cal[0]["stay_days"], cal[0]["price"]) \
        == ("", 0, 301)
    assert [r["return_date"] for r in pq] == [""]


def test_round_trip_sentinel_candidate_skipped_not_downgraded(tmp_path: Path):
    """A '' candidate on a ROUND-TRIP route must be skipped (B3), never
    silently executed as a one-way query — the client must not be
    called at all."""
    from lib.followup import run_followup

    calls = []

    class FakeGF:
        source_id = "googleflights"

        def point_query(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("must not be called")

    cands = [{"origin": "MAD", "destination": "NBO",
              "departure_date": "2026-09-20", "return_date": ""}]
    db = tmp_path / "t.db"
    route = _route(min_stay=0, max_stay=90)
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        res = run_followup(conn=conn, client=FakeGF(), route=route,
                           candidates=cands)
    assert calls == []
    assert res.itineraries_queried == 0


def test_skyscanner_skipped_for_one_way_candidates(tmp_path: Path):
    """The skyscanner adapter is round-trip only; a one-way candidate
    must skip it (an unguarded return_=None would raise AttributeError
    past the SkyScrapperError catch and kill the whole batch) while the
    primary client still runs."""
    from lib.followup import run_followup
    from lib.searchapi_io import FlightOption, PointResponse

    sky_calls = []

    class FakeSky:
        def point_query(self, **kwargs):
            sky_calls.append(kwargs)
            raise AssertionError("must not be called for one-way")

    class FakeGF:
        source_id = "googleflights"

        def point_query(self, **kwargs):
            return PointResponse(raw={}, best_flights=(
                FlightOption(price=301, total_minutes=1105, stops=1,
                             carriers="Etihad"),
            ))

    cands = [{"origin": "MAD", "destination": "NBO",
              "departure_date": "2026-09-20", "return_date": ""}]
    db = tmp_path / "t.db"
    route = _one_way_route()
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        res = run_followup(conn=conn, client=FakeGF(), route=route,
                           candidates=cands,
                           skyscanner_client=FakeSky(),
                           skyscanner_max_calls=None)
    assert sky_calls == []
    assert res.itineraries_queried == 1
