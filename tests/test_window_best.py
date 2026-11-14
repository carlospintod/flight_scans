"""window_best (2026-11-14): push once when a VERIFIED fare beats the best
verified fare the search has seen in its current window (NBO audit F4, #2).

Offline: tmp SQLite and fake clients, no network.

The rule as Carlos set it on 2026-11-14:
  * verified = googleflights + serpapi rows. The leads (serpapi_calendar
    cells, aviasales quotes) and searchapi never fire it and never set
    the bar;
  * the bar is the all-time best verified fare for trips inside the
    current window and stay range;
  * any euro below the bar fires, at or under watch_below_price;
  * at most one per scan; a re-check at the same price never fires again.
"""

import json
from dataclasses import replace
from datetime import date

import pytest

import scripts.notify_ntfy as notify
from lib.alerts import WINDOW_BEST, evaluate, summary_entry
from lib.config import (
    AlertParams, FollowupParams, RouteConfig, SearchWindow, StayPreferences,
    SweepParams,
)
from lib.db import (
    AlertRow, CalendarRow, connect, ensure_schema, insert_calendar_rows,
    upsert_route,
)

# The live spain-nairobi search as of 2026-11-14.
ROUTE = RouteConfig(
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
TODAY = date(2026, 11, 17)

# Trips (origin, departure, return), all inside the window.
A = ("MAD", "2027-01-28", "2027-04-20")   # the real 557 EUR (82 days)
B = ("BCN", "2027-02-03", "2027-04-14")   # never priced live (70 days)
C = ("MAD", "2027-02-10", "2027-05-01")   # (80 days)
D = ("BCN", "2027-01-20", "2027-04-10")   # (80 days)
OLD_WINDOW = ("MAD", "2026-11-12", "2027-01-15")   # the 30 Sep 481 EUR trip
SHORT_STAY = ("MAD", "2027-01-10", "2027-02-09")   # 30 days: out of range

PRIOR = "2026-11-11T12:49:54Z"       # the scan that found 557
CELL_AT = "2026-11-14T00:54:49Z"     # the first calendar sweep
SCAN1, S1 = "2026-11-17T13:00:00Z", "2026-11-17T13:05:00Z"   # start, row
SCAN2, S2 = "2026-11-19T13:00:00Z", "2026-11-19T13:05:00Z"
SCAN3, S3 = "2026-11-21T13:00:00Z", "2026-11-21T13:05:00Z"


def row(source: str, trip: tuple, price: int, at: str) -> CalendarRow:
    origin, dep, ret = trip
    stay = (date.fromisoformat(ret) - date.fromisoformat(dep)).days
    return CalendarRow(snapshot_at=at, route_id=ROUTE.name, source=source,
                       origin=origin, destination="NBO", departure_date=dep,
                       return_date=ret, stay_days=stay, price=price,
                       currency="EUR", is_lowest_price=False)


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        upsert_route(c, ROUTE)
        yield c


def run(conn, tmp_path, since, route=ROUTE):
    return evaluate(conn=conn, route=route, log_path=tmp_path / "alerts.log",
                    today=TODAY, since=since)


def short(alerts):
    return [(a.alert_type, a.source, a.origin, a.departure_date,
             a.return_date, a.price, a.baseline_median) for a in alerts]


# -- the rule ------------------------------------------------------------

def test_verified_first_look_that_beats_the_best_fires_exactly_once(
        conn, tmp_path):
    """Audit F4: a calendar cell nominated trip B, and the scraper's FIRST
    look at it beats the window's best verified fare. new_low cannot fire
    (no earlier googleflights row for B); window_best fires once and
    names the old best."""
    insert_calendar_rows(conn, [
        row("serpapi", A, 557, PRIOR),            # the bar
        row("serpapi_calendar", B, 594, CELL_AT),  # the nomination
        row("googleflights", B, 549, S1),         # first live look
    ])
    fired = run(conn, tmp_path, SCAN1)
    assert short(fired) == [(WINDOW_BEST, "googleflights", "BCN",
                             "2027-02-03", "2027-04-14", 549, 557)]
    assert fired[0].drop_pct == round(8 / 557 * 100, 2)
    # The same scan evaluated again (a retry) adds nothing.
    assert run(conn, tmp_path, SCAN1) == []
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


def test_recheck_at_the_same_price_never_fires_again(conn, tmp_path):
    insert_calendar_rows(conn, [row("serpapi", A, 557, PRIOR),
                                row("googleflights", B, 549, S1)])
    assert len(run(conn, tmp_path, SCAN1)) == 1
    # Next scan: the scraper re-checks B at 549 and finds C at 549 too.
    insert_calendar_rows(conn, [row("googleflights", B, 549, S2),
                                row("googleflights", C, 549, S2)])
    assert run(conn, tmp_path, SCAN2) == []
    # One euro lower is a new best (Carlos: any euro counts).
    insert_calendar_rows(conn, [row("googleflights", C, 548, S3)])
    assert [(a.alert_type, a.price, a.baseline_median)
            for a in run(conn, tmp_path, SCAN3)] == [(WINDOW_BEST, 548, 549)]
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 2


def test_calendar_cells_and_aviasales_quotes_never_fire_or_set_the_bar(
        conn, tmp_path):
    insert_calendar_rows(conn, [
        row("serpapi", A, 557, PRIOR),
        row("serpapi_calendar", B, 520, S1),   # cheaper than any live fare
        row("aviasales", C, 499, S1),
    ])
    assert run(conn, tmp_path, SCAN1) == []
    # The scraper then verifies B at 540: above the cell's 520, below the
    # verified 557. A lead never sets the bar, so this fires against 557.
    insert_calendar_rows(conn, [row("googleflights", B, 540, S2)])
    assert [(a.alert_type, a.source, a.price, a.baseline_median)
            for a in run(conn, tmp_path, SCAN2)] == [
        (WINDOW_BEST, "googleflights", 540, 557)]


def test_searchapi_grid_cells_never_fire_or_set_the_bar(conn, tmp_path):
    insert_calendar_rows(conn, [
        row("serpapi", A, 557, PRIOR),
        row("searchapi", C, 530, PRIOR),
        row("searchapi", B, 525, S1),
    ])
    assert run(conn, tmp_path, SCAN1) == []
    insert_calendar_rows(conn, [row("googleflights", B, 545, S2)])
    assert [(a.alert_type, a.price, a.baseline_median)
            for a in run(conn, tmp_path, SCAN2)] == [(WINDOW_BEST, 545, 557)]


def test_new_best_above_the_watch_bar_stays_silent(conn, tmp_path):
    """After a window move into pricier dates the best can sit above the
    watch bar: 700 -> 650 is a new best, but not at or under 600."""
    insert_calendar_rows(conn, [row("serpapi", A, 700, PRIOR),
                                row("googleflights", B, 650, S1)])
    assert run(conn, tmp_path, SCAN1) == []
    # A route without a watch bar has no ceiling.
    no_bar = replace(ROUTE, followup=FollowupParams())
    assert [(a.alert_type, a.price)
            for a in run(conn, tmp_path, SCAN1, route=no_bar)] == [
        (WINDOW_BEST, 650)]


def test_a_new_best_exactly_at_the_watch_bar_fires(conn, tmp_path):
    insert_calendar_rows(conn, [row("serpapi", A, 640, PRIOR),
                                row("googleflights", B, 600, S1)])
    assert [(a.alert_type, a.price, a.baseline_median)
            for a in run(conn, tmp_path, SCAN1)] == [(WINDOW_BEST, 600, 640)]


def test_first_verified_fare_in_an_empty_window_does_not_fire(conn, tmp_path):
    """Nothing to beat: before this scan there is only an older window's
    trip, a lead and a stay outside 60-90 days."""
    insert_calendar_rows(conn, [
        row("serpapi", OLD_WINDOW, 481, PRIOR),
        row("serpapi_calendar", A, 590, PRIOR),
        row("googleflights", SHORT_STAY, 450, PRIOR),
        row("googleflights", B, 549, S1),
    ])
    assert run(conn, tmp_path, SCAN1) == []


def test_the_bar_and_the_new_fare_only_count_trips_in_the_window(
        conn, tmp_path):
    """Cheaper verified fares outside the window (an older window's trip, a
    stay outside 60-90 days) neither set the bar nor fire."""
    insert_calendar_rows(conn, [
        row("serpapi", OLD_WINDOW, 481, PRIOR),
        row("googleflights", SHORT_STAY, 450, PRIOR),
        row("serpapi", A, 557, PRIOR),
        row("googleflights", SHORT_STAY, 400, S1),   # fresh but out of range
        row("googleflights", B, 549, S1),
    ])
    assert [(a.alert_type, a.departure_date, a.price, a.baseline_median)
            for a in run(conn, tmp_path, SCAN1)] == [
        (WINDOW_BEST, "2027-02-03", 549, 557)]


def test_only_the_cheapest_new_fare_fires(conn, tmp_path):
    insert_calendar_rows(conn, [
        row("serpapi", A, 557, PRIOR),
        row("googleflights", B, 552, S1),
        row("googleflights", C, 545, S1),
        row("serpapi", D, 550, S1),
    ])
    assert [(a.alert_type, a.departure_date, a.price)
            for a in run(conn, tmp_path, SCAN1)] == [
        (WINDOW_BEST, "2027-02-10", 545)]


def test_window_best_replaces_new_low_on_the_same_trip(conn, tmp_path):
    """A re-check of the best trip that dropped is both a new_low and a new
    window best: one alert, the window_best."""
    insert_calendar_rows(conn, [row("serpapi", A, 557, PRIOR),
                                row("serpapi", A, 540, S1)])
    assert [(a.alert_type, a.price, a.baseline_median)
            for a in run(conn, tmp_path, SCAN1)] == [(WINDOW_BEST, 540, 557)]


def test_window_best_mutes_a_second_source_on_the_next_scan(conn, tmp_path):
    """Two sources price trip B in one scan. The serpapi row is a new_low
    (B was 600 before). window_best covers the trip in this pass, and the
    serpapi new_low must not surface on the next scan either."""
    insert_calendar_rows(conn, [
        row("serpapi", A, 557, PRIOR),
        row("serpapi", B, 600, PRIOR),
        row("googleflights", B, 549, S1),
        row("serpapi", B, 549, S1),
    ])
    assert [(a.alert_type, a.price)
            for a in run(conn, tmp_path, SCAN1)] == [(WINDOW_BEST, 549)]
    assert run(conn, tmp_path, SCAN2) == []
    # A genuinely lower serpapi price on B is new signal again.
    insert_calendar_rows(conn, [row("serpapi", B, 530, S3)])
    assert [(a.alert_type, a.price)
            for a in run(conn, tmp_path, SCAN3)] == [(WINDOW_BEST, 530)]


def test_new_low_elsewhere_still_fires_beside_a_window_best(conn, tmp_path):
    """window_best replaces alerts on ITS trip only. A new_low on another
    trip in the same scan still fires."""
    insert_calendar_rows(conn, [
        row("serpapi", A, 557, PRIOR),
        row("serpapi", C, 590, PRIOR),
        row("googleflights", B, 549, S1),
        row("serpapi", C, 570, S1),   # -20 on C: a new_low, not a best
    ])
    assert sorted((a.alert_type, a.departure_date, a.price)
                  for a in run(conn, tmp_path, SCAN1)) == [
        ("new_low", "2027-02-10", 570),
        (WINDOW_BEST, "2027-02-03", 549),
    ]


def test_without_since_the_rule_is_skipped(conn, tmp_path):
    """Callers outside lib.runner (tracker.py alerts, the old UI) pass no
    `since`: window_best is skipped, the other rules are unchanged."""
    insert_calendar_rows(conn, [row("serpapi", A, 557, PRIOR),
                                row("googleflights", B, 549, S1)])
    assert evaluate(conn=conn, route=ROUTE, log_path=tmp_path / "alerts.log",
                    today=TODAY) == []


def test_a_zero_price_row_never_becomes_the_bar(conn, tmp_path):
    insert_calendar_rows(conn, [row("serpapi", C, 0, PRIOR),
                                row("serpapi", A, 557, PRIOR),
                                row("googleflights", B, 549, S1)])
    assert [(a.alert_type, a.price, a.baseline_median)
            for a in run(conn, tmp_path, SCAN1)] == [(WINDOW_BEST, 549, 557)]


# -- the runner path -------------------------------------------------------

class FakeScraper:
    """The free Google Flights scraper, faked: one fare per trip."""
    source_id = "googleflights"

    def __init__(self, prices: dict):
        self.prices = prices
        self.calls: list[tuple] = []

    def point_query(self, *, origin, destination, outbound, return_,
                    currency):
        from lib.searchapi_io import FlightOption, PointResponse
        trip = (origin, outbound.isoformat(), return_.isoformat())
        self.calls.append(trip)
        return PointResponse(raw={}, best_flights=(FlightOption(
            price=self.prices.get(trip, 700), total_minutes=900, stops=1,
            carriers="Saudia"),))


def test_runner_path_fires_once_for_a_nominated_first_look(conn, tmp_path):
    """End to end through lib.runner.execute_search: the calendar cell
    nominates trip B, the scraper verifies it below the window's best, and
    the scan returns exactly one window_best. The next scan re-checks the
    same prices and returns nothing."""
    from lib.planner import Caps, build_run_plan
    from lib.runner import execute_search

    insert_calendar_rows(conn, [row("serpapi", A, 557, PRIOR),
                                row("serpapi_calendar", B, 594, CELL_AT)])
    scraper = FakeScraper({B: 549, A: 557})

    def scan():
        plan = build_run_plan(conn, ROUTE, sources=["googleflights"],
                              caps=Caps(), today=date(2026, 11, 14))
        return execute_search(conn=conn, route=ROUTE, plan=plan,
                              clients={"googleflights": scraper},
                              caps=Caps(), alerts_log=tmp_path / "alerts.log")

    first = scan()
    assert B in scraper.calls     # the cell's trip went to the scraper
    assert [(a.alert_type, a.source, a.price, a.baseline_median)
            for a in first.alerts] == [(WINDOW_BEST, "googleflights", 549, 557)]
    second = scan()
    assert second.alerts == []
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


# -- the push ------------------------------------------------------------------

def _best_549() -> AlertRow:
    return AlertRow(fired_at="2026-11-17T13:06:00Z", route_id=ROUTE.name,
                    source="googleflights", origin="BCN", destination="NBO",
                    departure_date="2027-02-03", return_date="2027-04-14",
                    price=549, currency="EUR", baseline_median=557,
                    drop_pct=1.44, alert_type=WINDOW_BEST)


def test_summary_entry_carries_the_reference_price():
    e = summary_entry(_best_549())
    assert e == {"type": "window_best", "origin": "BCN", "destination": "NBO",
                 "departure_date": "2027-02-03", "return_date": "2027-04-14",
                 "price": 549, "currency": "EUR", "ref_price": 557}


def test_push_leads_with_the_new_best_and_names_the_old_one():
    one_way_low = {"type": "new_low", "origin": "MAD", "destination": "NBO",
                   "departure_date": "2026-12-16", "return_date": "",
                   "price": 301, "currency": "EUR", "ref_price": 340}
    title, body = notify.price_push([one_way_low,
                                     summary_entry(_best_549())])
    assert title == "New best NBO fare: 549 EUR (was 557)"
    lines = body.splitlines()
    assert lines[0] == ("[window_best] BCN->NBO 2027-02-03..2027-04-14 "
                        "549 EUR (best was 557)")
    assert lines[1] == "[new_low] MAD->NBO one-way 2026-12-16 301 EUR"


def test_push_without_ref_price_drops_the_was(monkeypatch):
    """A summary written before ref_price existed still pushes."""
    e = summary_entry(_best_549())
    del e["ref_price"]
    title, body = notify.price_push([e])
    assert title == "New best NBO fare: 549 EUR"
    assert body == "[window_best] BCN->NBO 2027-02-03..2027-04-14 549 EUR"


def test_window_best_reaches_the_phone(monkeypatch, tmp_path):
    """The batch summary's alerts_fired (summary_entry) -> one high push."""
    pushed = []
    monkeypatch.setattr(notify, "_push",
                        lambda topic, **kw: pushed.append(kw))
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setenv("JOB_STATUS", "success")
    p = tmp_path / "summary.json"
    p.write_text(json.dumps({"status": "ok", "health_alerts": [],
                             "alerts_fired": [summary_entry(_best_549())]}),
                 encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["notify", str(p)])
    assert notify.main() == 0
    assert len(pushed) == 1
    assert pushed[0]["title"] == "New best NBO fare: 549 EUR (was 557)"
    assert pushed[0]["priority"] == "high"
