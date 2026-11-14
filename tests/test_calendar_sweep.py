"""SerpApi calendar strip sweep (2026-11-14): every date pair of the window.

Offline: the adapter is exercised against a REAL captured response
(tests/fixtures/serpapi_calendar_response.json — the probe call of
2026-11-14, MAD->NBO, 90-day stay, 65 departure days, 1 credit) and fake
sessions/clients. No network.

What is pinned here:
  * the parser and request shape (defensive, one strip per call);
  * the geometry: the NBO window is 62 strips = 4,030 date pairs;
  * freshness: resume after a partial sweep, errors retried, an empty
    strip asked once more before it is believed, a moved window counts
    as unswept;
  * quote == execution through the real ledger (charge-before-call);
  * sweep cells never fire alerts (they nominate; verified rows alert).
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import pytest

from lib.config import (
    AlertParams, FollowupParams, RouteConfig, SearchWindow,
    StayPreferences, SweepParams,
)
from lib.db import (
    CalendarRow, connect, ensure_schema, insert_calendar_rows,
    insert_calendar_sweep, upsert_route,
)
from lib.planner import SERPAPI_OTA_RESERVE, Caps, build_run_plan, cost_vector
from lib.searchapi_io import CalendarEntry, CalendarResponse, PointResponse
from lib.serpapi_io import (
    CALENDAR_SOURCE_ID, SerpApiClient, SerpApiError, _parse_calendar_prices,
)
from lib.sweep import (
    SERPAPI_SWEEP_REFRESH_DAYS, CalendarStrip, due_strips, plan_strips,
    run_strip_sweep,
)

FIXTURE = Path(__file__).parent / "fixtures" / "serpapi_calendar_response.json"
TODAY = date(2026, 11, 14)


def _nbo(**overrides) -> RouteConfig:
    """The live spain-nairobi search as of 2026-11-14."""
    base = dict(
        name="spain-nairobi",
        origins=("MAD", "BCN"),
        destinations=("NBO",),
        search_window=SearchWindow(
            earliest_departure=date(2026, 12, 27),
            latest_return=date(2027, 5, 30),
            latest_departure=date(2027, 3, 1),
        ),
        stay=StayPreferences(min_days=60, max_days=90),
        currency="EUR",
        sweep=SweepParams(cadence_days=3, skip_if_min_above=800,
                          skip_grace_days=60),
        followup=FollowupParams(watch_below_price=600, drop_above_price=700),
        alerts=AlertParams(15.0, 30, 4),
    )
    base.update(overrides)
    return RouteConfig(**base)


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        yield c


@pytest.fixture()
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# -- adapter -------------------------------------------------------------

def test_parses_the_real_probe_response(payload):
    entries = _parse_calendar_prices(payload)
    assert len(entries) == 65                       # every requested day
    assert entries[0] == CalendarEntry(
        departure_date="2026-12-27", return_date="2027-03-27", price=1129,
        has_no_flights=False, is_lowest_price=False)
    stays = {(date.fromisoformat(e.return_date)
              - date.fromisoformat(e.departure_date)).days for e in entries}
    assert stays == {90}
    cheapest = min(entries, key=lambda e: e.price)
    assert (cheapest.departure_date, cheapest.price) == ("2027-01-13", 617)
    # low_price = "within 5% of the range minimum": half the strip here,
    # unlike SearchAPI's sparser flag (8% of cells) — see the parser note.
    assert sum(e.is_lowest_price for e in entries) == 33


def test_parser_drops_cells_that_are_not_observations():
    entries = _parse_calendar_prices({"prices": [
        {"outbound_date": "2027-01-10", "return_date": "2027-03-11", "price": 590},
        {"outbound_date": "2027-01-11", "return_date": "2027-03-12", "price": True},
        {"outbound_date": "2027-01-12", "return_date": "2027-03-13", "price": 0},
        {"outbound_date": "2027-01-13", "return_date": "2027-03-14", "price": "600"},
        {"outbound_date": "2027-01-14", "price": 610},
        {"outbound_date": "13/01/2027", "return_date": "2027-03-15", "price": 620},
        {"outbound_date": "2027-01-15", "return_date": "2027-03-16", "price": 631.6},
        "not-a-cell",
    ]})
    assert [(e.departure_date, e.price) for e in entries] == [
        ("2027-01-10", 590), ("2027-01-15", 632)]
    assert _parse_calendar_prices({}) == []
    assert _parse_calendar_prices({"prices": None}) == []


class _Resp:
    def __init__(self, data, *, ok=True, status=200):
        self._data, self.ok, self.status_code = data, ok, status
        self.text = json.dumps(data)

    def json(self):
        return self._data


class _Session:
    def __init__(self, resp):
        self.resp, self.params, self.timeout = resp, None, None

    def get(self, url, params=None, timeout=None):
        assert url == "https://serpapi.com/search"
        self.params, self.timeout = dict(params or {}), timeout
        return self.resp


def test_calendar_request_is_one_stay_strip(payload):
    session = _Session(_Resp(payload))
    client = SerpApiClient(api_key="test-key", session=session)
    resp = client.calendar(origin="MAD", destination="NBO", stay_days=90,
                           outbound_start=date(2026, 12, 27),
                           outbound_end=date(2027, 3, 1), currency="EUR")
    p = session.params
    assert p["engine"] == "google_flights_calendar"
    assert p["type"] == "1"
    assert (p["date_range_start"], p["date_range_end"]) == ("2026-12-27", "2027-03-01")
    assert p["round_trip_days"] == 90
    assert p["currency"] == "EUR"          # the engine defaults to USD
    assert "fixed_outbound_date" not in p  # mutually exclusive with round_trip_days
    assert session.timeout == 60
    assert len(resp.entries) == 65


def test_no_results_payload_is_an_empty_strip_not_an_error():
    session = _Session(_Resp(
        {"error": "Google Flights hasn't returned any results for this query."}))
    client = SerpApiClient(api_key="k", session=session)
    resp = client.calendar(origin="MAD", destination="NBO", stay_days=60,
                           outbound_start=date(2027, 1, 1),
                           outbound_end=date(2027, 1, 31), currency="EUR")
    assert resp.entries == ()


def test_real_errors_raise():
    client = SerpApiClient(api_key="k", session=_Session(
        _Resp({"error": "Invalid API key."}, ok=False, status=401)))
    with pytest.raises(SerpApiError, match="401"):
        client.calendar(origin="MAD", destination="NBO", stay_days=60,
                        outbound_start=date(2027, 1, 1),
                        outbound_end=date(2027, 1, 31), currency="EUR")
    with pytest.raises(SerpApiError):
        client.calendar(origin="MAD", destination="NBO", stay_days=0,
                        outbound_start=date(2027, 1, 1),
                        outbound_end=date(2027, 1, 31), currency="EUR")


# -- geometry ------------------------------------------------------------

def test_nbo_window_is_62_strips_and_4030_pairs():
    strips = plan_strips(_nbo(), today=TODAY)
    assert len(strips) == 62                              # 31 stays x 2 origins
    assert sum(s.departures for s in strips) == 4030      # every date pair once
    assert {s.stay_days for s in strips} == set(range(60, 91))
    assert all((s.dep_start, s.dep_end) == (date(2026, 12, 27), date(2027, 3, 1))
               for s in strips)
    # Every strip's last trip still ends by latest_return.
    assert all(s.dep_end + timedelta(days=s.stay_days) <= date(2027, 5, 30)
               for s in strips)


def test_strips_clamp_to_today_and_chunk_long_ranges():
    inside = plan_strips(_nbo(), today=date(2027, 1, 20))
    assert all(s.dep_start == date(2027, 1, 20) for s in inside)
    chunked = plan_strips(_nbo(origins=("MAD",)), today=TODAY,
                          max_departure_days=30)
    per_stay = [s for s in chunked if s.stay_days == 75]
    assert [(s.dep_start, s.dep_end) for s in per_stay] == [
        (date(2026, 12, 27), date(2027, 1, 25)),
        (date(2027, 1, 26), date(2027, 2, 24)),
        (date(2027, 2, 25), date(2027, 3, 1))]


def test_stay_that_cannot_fit_the_window_plans_no_strip():
    route = _nbo(search_window=SearchWindow(
        earliest_departure=date(2027, 3, 20), latest_return=date(2027, 5, 30)))
    stays = {s.stay_days for s in plan_strips(route, today=TODAY)}
    # 20 Mar + 72 days = 31 May > 30 May: stays 72-90 cannot fit.
    assert stays == set(range(60, 72))


# -- freshness -----------------------------------------------------------

def _log(conn, route, strip: CalendarStrip, *, at: str, status="ok", cells=65):
    insert_calendar_sweep(
        conn, route_id=route.name, source=CALENDAR_SOURCE_ID,
        origin=strip.origin, destination=strip.destination,
        stay_days=strip.stay_days, dep_start=strip.dep_start.isoformat(),
        dep_end=strip.dep_end.isoformat(), swept_at=at, cells=cells,
        status=status)


def test_everything_is_due_until_swept_then_fresh_for_the_refresh_period(conn):
    route = _nbo()
    strips = plan_strips(route, today=TODAY)
    assert len(due_strips(conn, route, strips, today=TODAY)) == 62
    for s in strips:
        _log(conn, route, s, at="2026-11-14T13:00:00Z")
    assert due_strips(conn, route, strips, today=TODAY) == []
    day13 = TODAY + timedelta(days=SERPAPI_SWEEP_REFRESH_DAYS - 1)
    assert due_strips(conn, route, strips, today=day13) == []
    # Two weeks on — the same scan slot — the whole window is due again.
    day14 = TODAY + timedelta(days=SERPAPI_SWEEP_REFRESH_DAYS)
    assert len(due_strips(conn, route, strips, today=day14)) == 62


def test_partial_sweep_resumes_and_errors_are_retried_first(conn):
    route = _nbo()
    strips = plan_strips(route, today=TODAY)
    for s in strips[:40]:
        _log(conn, route, s, at="2026-11-14T13:00:00Z")
    _log(conn, route, strips[40], at="2026-11-14T13:00:00Z", status="error",
         cells=0)
    due = due_strips(conn, route, strips, today=TODAY)
    assert set(due) == set(strips[40:])          # the 22 never priced


def test_an_empty_strip_is_asked_once_more_then_believed(conn):
    """The first real sweep (2026-11-14) got Google's "no results" for
    BCN 65-day stays while 64 and 66 returned all 65 days. One empty is
    asked again on the next run; two in a row are believed."""
    route = _nbo()
    strips = plan_strips(route, today=TODAY)
    blip, dead, flaky = strips[3], strips[4], strips[5]
    for s in strips:
        empty = s in (blip, dead, flaky)
        _log(conn, route, s, at="2026-11-14T01:00:00Z",
             status="empty" if empty else "ok", cells=0 if empty else 65)
    assert set(due_strips(conn, route, strips, today=TODAY)) == {blip, dead, flaky}

    monday = date(2026, 11, 16)
    _log(conn, route, blip, at="2026-11-16T13:00:00Z")                  # priced now
    _log(conn, route, dead, at="2026-11-16T13:00:00Z", status="empty", cells=0)
    _log(conn, route, flaky, at="2026-11-16T13:00:00Z", status="error", cells=0)
    assert due_strips(conn, route, strips, today=monday) == [flaky]

    # An empty after an ERROR is not a confirmation: still asked again.
    _log(conn, route, flaky, at="2026-11-18T13:00:00Z", status="empty", cells=0)
    assert due_strips(conn, route, strips, today=date(2026, 11, 18)) == [flaky]

    # At the refresh, the still-unconfirmed strip goes first, ahead of
    # the 59 that are merely two weeks old.
    refresh = due_strips(conn, route, strips, today=date(2026, 11, 28))
    assert refresh[0] == flaky and len(refresh) == 60


def test_a_moved_window_counts_as_unswept(conn):
    route = _nbo()
    for s in plan_strips(route, today=TODAY):
        _log(conn, route, s, at="2026-11-14T13:00:00Z")
    # Carlos extends the last departure by a week: every strip now has
    # 7 departure days nobody priced, so every strip is due.
    wider = _nbo(search_window=SearchWindow(
        earliest_departure=date(2026, 12, 27), latest_return=date(2027, 6, 6),
        latest_departure=date(2027, 3, 8)))
    assert len(due_strips(conn, wider, plan_strips(wider, today=TODAY),
                          today=TODAY)) == 62
    # Narrowing the window keeps the existing coverage.
    narrower = _nbo(search_window=SearchWindow(
        earliest_departure=date(2027, 1, 10), latest_return=date(2027, 5, 30),
        latest_departure=date(2027, 3, 1)))
    assert due_strips(conn, narrower, plan_strips(narrower, today=TODAY),
                      today=TODAY) == []


def test_a_cut_short_sweep_still_samples_the_whole_stay_range(conn):
    route = _nbo()
    due = due_strips(conn, route, plan_strips(route, today=TODAY), today=TODAY)
    first8 = due[:8]
    stays = sorted({s.stay_days for s in first8})
    # Spread, not 60-63: the first strips span the stay range...
    assert stays[0] == 60 and stays[-1] >= 84
    # ...and alternate origins.
    assert {s.origin for s in first8} == {"MAD", "BCN"}


# -- executor ------------------------------------------------------------

class FakeSerpApi:
    """Answers each strip with one cell per departure day, plus one cell
    outside the window that the sweep must drop."""

    source_id = "serpapi"

    def __init__(self, *, fail_on=(), price=600):
        self.calendar_calls: list[dict] = []
        self.point_calls = 0
        self.fail_on = set(fail_on)
        self.price = price

    def calendar(self, *, origin, destination, stay_days, outbound_start,
                 outbound_end, currency, **_):
        self.calendar_calls.append(dict(origin=origin, stay=stay_days))
        if (origin, stay_days) in self.fail_on:
            raise SerpApiError(500, "upstream exploded")
        cells = []
        d = outbound_start
        while d <= outbound_end:
            cells.append(CalendarEntry(
                d.isoformat(), (d + timedelta(days=stay_days)).isoformat(),
                self.price + stay_days, False, False))
            d += timedelta(days=1)
        cells.append(CalendarEntry("2026-12-20", "2027-03-01", 1, False, True))
        return CalendarResponse(raw={}, entries=tuple(cells))

    def point_query(self, **_):
        self.point_calls += 1
        return PointResponse(raw={}, best_flights=())

    def booking_options(self, **_):
        return []


def test_sweep_stores_in_window_cells_and_logs_every_call(conn):
    route = _nbo(origins=("MAD",))
    strips = plan_strips(route, today=TODAY)[:3]
    fake = FakeSerpApi(fail_on={("MAD", strips[1].stay_days)})
    res = run_strip_sweep(conn=conn, client=fake, route=route, strips=strips)
    assert (res.calls, res.errors, res.cells_stored) == (3, 1, 2 * 65)
    rows = conn.execute(
        "SELECT source, MIN(departure_date) AS lo, COUNT(*) AS n "
        "FROM calendar_snapshots GROUP BY source").fetchall()
    assert [tuple(r) for r in rows] == [(CALENDAR_SOURCE_ID, "2026-12-27", 130)]
    log = conn.execute("SELECT stay_days, status, cells FROM calendar_sweeps "
                       "ORDER BY sweep_id").fetchall()
    assert [tuple(r) for r in log] == [
        (strips[0].stay_days, "ok", 65), (strips[1].stay_days, "error", 0),
        (strips[2].stay_days, "ok", 65)]
    # The failed strip is due again next run; the two good ones are not.
    assert due_strips(conn, route, strips, today=TODAY) == [strips[1]]


def test_an_empty_strip_is_logged_with_the_reason_and_stays_due(conn, caplog):
    route = _nbo(origins=("MAD",))
    strip = plan_strips(route, today=TODAY)[0]
    msg = "Google Flights Calendar hasn't returned any results for this query."

    class NoResults(FakeSerpApi):
        def calendar(self, **kw):
            self.calendar_calls.append(kw)
            return CalendarResponse(raw={"error": msg}, entries=())

    with caplog.at_level(logging.WARNING, logger="lib.sweep"):
        res = run_strip_sweep(conn=conn, client=NoResults(), route=route,
                              strips=[strip])
    assert (res.calls, res.empty, res.errors, res.cells_stored) == (1, 1, 0, 0)
    assert "came back empty: " + msg in caplog.text
    assert conn.execute("SELECT status FROM calendar_sweeps").fetchone()[0] == "empty"
    assert due_strips(conn, route, [strip], today=TODAY) == [strip]


def test_sweep_stops_on_repeated_errors_and_on_the_wall_budget(conn):
    route = _nbo(origins=("MAD",))
    strips = plan_strips(route, today=TODAY)
    dead = FakeSerpApi(fail_on={("MAD", s) for s in range(60, 91)})
    res = run_strip_sweep(conn=conn, client=dead, route=route, strips=strips)
    assert res.calls == 3 and "errors in a row" in res.stopped_early

    ticks = iter(range(0, 10_000, 400))          # 400 "seconds" per strip
    slow = FakeSerpApi()
    res = run_strip_sweep(conn=conn, client=slow, route=route, strips=strips,
                          wall_budget_s=900, clock=lambda: next(ticks))
    assert res.calls == 2 and "wall budget" in res.stopped_early


# -- planner + ledger ----------------------------------------------------

def test_sweep_is_off_unless_granted(conn):
    route = _nbo()
    upsert_route(conn, route)
    plan = build_run_plan(conn, route, sources=["serpapi"], caps=Caps(),
                          today=TODAY)
    assert plan.serpapi_sweep == ()
    assert plan.calls_by_source["serpapi"] == (
        len(plan.serpapi_discovery) + SERPAPI_OTA_RESERVE)


def test_granted_sweep_plans_due_strips_and_quotes_them(conn):
    route = _nbo()
    upsert_route(conn, route)
    plan = build_run_plan(conn, route, sources=["serpapi"],
                          caps=Caps(serpapi_sweep=80), today=TODAY)
    assert len(plan.serpapi_sweep) == 62
    base = len(plan.serpapi_discovery) + SERPAPI_OTA_RESERVE
    assert plan.calls_by_source["serpapi"] == base + 62
    cv = cost_vector(plan, caps=Caps(serpapi_sweep=80))
    serp = [l for l in cv.lines if l.source == "serpapi"]
    assert len(serp) == 1 and serp[0].units == base + 62   # ONE line: settle()
    assert "calendar sweep (62 strips)" in serp[0].note
    assert any(n.startswith("calendar sweep: 62 of 62") for n in plan.notes)
    # A smaller grant plans a prefix; one-way routes never sweep.
    assert len(build_run_plan(conn, route, sources=["serpapi"],
                              caps=Caps(serpapi_sweep=10),
                              today=TODAY).serpapi_sweep) == 10
    ow = _nbo(name="ow", trip_type="one_way",
              stay=StayPreferences(min_days=0, max_days=0))
    upsert_route(conn, ow)
    assert build_run_plan(conn, ow, sources=["serpapi"],
                          caps=Caps(serpapi_sweep=80),
                          today=TODAY).serpapi_sweep == ()


def test_quote_equals_execution_through_the_real_ledger(conn, tmp_path):
    """Reserve the plan, execute it with hard-stop GuardedClients: the
    calendar spend equals the planned strips, every call is charged
    before it runs, and the guard never trips."""
    from lib.clients import guard_clients
    from lib.quota import SCOPE_TRACKER, QuotaLedger
    from lib.runner import execute_search

    route = _nbo()
    upsert_route(conn, route)
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("serpapi", remaining=228, limit_total=250,
                         origin="account_api")
    caps = Caps(serpapi_sweep=80)
    plan = build_run_plan(conn, route, sources=["serpapi"], caps=caps,
                          today=TODAY)
    cost = cost_vector(plan, caps=caps)
    run_id = ledger.begin_run(trigger="test", scope=SCOPE_TRACKER)
    # Guest-sized per-search cap (7) would refuse it; the owner may.
    assert ledger.reserve(run_id, "g", cost, enforce_per_search_cap=True) is False
    assert ledger.reserve(run_id, "spain-nairobi", cost,
                          enforce_per_search_cap=False) is True
    fake = FakeSerpApi()
    clients = guard_clients({"serpapi": fake}, ledger=ledger, run_id=run_id,
                            search_id="spain-nairobi", shadow=False)
    res = execute_search(conn=conn, route=route, plan=plan, clients=clients,
                         caps=caps, alerts_log=tmp_path / "alerts.log")
    assert len(fake.calendar_calls) == len(plan.serpapi_sweep) == 62
    assert res.results["serpapi_calendar"]["stored"] == 62 * 65
    assert res.results["serpapi_calendar"]["error"] is None
    spend = {r["op"]: r["n"] for r in conn.execute(
        "SELECT op, SUM(units) AS n FROM spend_events WHERE source='serpapi' "
        "GROUP BY op").fetchall()}
    assert spend["calendar"] == 62
    assert sum(spend.values()) <= cost.total("serpapi")
    ledger.settle(run_id, "spain-nairobi")
    used = conn.execute(
        "SELECT SUM(used_units) FROM run_reservations WHERE run_id = ? "
        "AND search_id = 'spain-nairobi' AND source = 'serpapi'",
        (run_id,)).fetchone()[0]
    assert used == sum(spend.values())          # no double counting
    # Next run plans nothing: the window is fresh.
    again = build_run_plan(conn, route, sources=["serpapi"], caps=caps,
                           today=TODAY)
    assert again.serpapi_sweep == ()


def test_batch_allowance_keeps_headroom_for_regular_scans():
    from run_batch import (SERPAPI_SWEEP_HEADROOM_SCANS, SERPAPI_SWEEP_MAX_STRIPS,
                           _serpapi_sweep_allowance)

    class _St:
        def __init__(self, avail):
            self.effective_available = avail

    headroom = SERPAPI_SWEEP_HEADROOM_SCANS * 1 * 7
    assert _serpapi_sweep_allowance({"serpapi": _St(203)}, per_scan=7,
                                    active_searches=1) == min(
        SERPAPI_SWEEP_MAX_STRIPS, 203 - 7 - headroom)
    assert _serpapi_sweep_allowance({"serpapi": _St(60)}, per_scan=7,
                                    active_searches=1) == 60 - 7 - headroom
    assert _serpapi_sweep_allowance({"serpapi": _St(40)}, per_scan=7,
                                    active_searches=1) == 0
    assert _serpapi_sweep_allowance({"serpapi": _St(None)}, per_scan=7,
                                    active_searches=1) == 0
    assert _serpapi_sweep_allowance({}, per_scan=7, active_searches=1) == 0
    # More active searches, more headroom kept back.
    assert _serpapi_sweep_allowance({"serpapi": _St(100)}, per_scan=7,
                                    active_searches=2) == 100 - 7 - 2 * headroom


# -- alerts --------------------------------------------------------------

def test_sweep_cells_never_alert_but_verified_rows_still_do(conn, tmp_path):
    from lib import alerts

    route = _nbo()

    def row(source, snap, price):
        return CalendarRow(snapshot_at=snap, route_id=route.name,
                           source=source, origin="MAD", destination="NBO",
                           departure_date="2027-01-28",
                           return_date="2027-04-20", stay_days=82,
                           price=price, currency="EUR", is_lowest_price=False)

    insert_calendar_rows(conn, [
        row(CALENDAR_SOURCE_ID, "2026-11-01T13:00:00Z", 590),
        row(CALENDAR_SOURCE_ID, "2026-11-14T13:00:00Z", 540),  # -50: a "new low"
        row("googleflights", "2026-11-01T13:00:00Z", 590),
        row("googleflights", "2026-11-14T13:00:00Z", 545),
    ])
    fired = alerts.evaluate(conn=conn, route=route,
                            log_path=tmp_path / "alerts.log", today=TODAY)
    assert [(a.source, a.alert_type, a.price) for a in fired] == [
        ("googleflights", "new_low", 545)]
