"""Stop re-nominating itineraries the live market already disproved.

Measured 2026-08-10..12, every deal since the long-haul fixes:

    19  cache was too low (live price higher than cached + tolerance)
     6  rejected by the route floor
     5  Google returned no results
     0  passed

with BCN->EVN, BCN->KUT and MAD->NYC each nominated SIX times. The cache
serves the same fiction three times a day; each re-nomination consumed a
daily-cap slot and a paid verification to reach a conclusion already on
record — while genuinely new candidates never got a slot.
"""

from datetime import datetime, timedelta, timezone

import pytest

from lib.dealconfig import load_deal_config
from lib.db import connect, ensure_schema
from lib import deals_db
from lib.dealgate import gate_candidates
from lib.deals_db import Observation, ensure_deals_schema

CONFIG = load_deal_config()
NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        ensure_deals_schema(c)
        yield c


def _obs(dest, price, *, origin="MAD", dep="2026-11-06", ret="2026-11-13"):
    return Observation(origin=origin, dest=dest, depart_date=dep,
                       return_date=ret, price=price, currency="EUR",
                       source="aviasales", source_family="cached",
                       found_at=None)


def _disprove(conn, dest, *, origin="MAD", dep="2026-11-06", ret="2026-11-13",
              cached=355, live=398, hours=None):
    deals_db.record_disproved(
        conn, origin=origin, dest=dest, depart_date=dep, return_date=ret,
        cached=cached, live=live,
        hours=CONFIG.disproved_cooldown_hours if hours is None else hours)


def test_the_config_is_24h():
    assert CONFIG.disproved_cooldown_hours == 24


def test_a_disproved_itinerary_is_not_re_nominated(conn):
    obs = [_obs("NYC", 355), _obs("MIA", 700), _obs("BKK", 740),
           _obs("EZE", 820), _obs("TYO", 900), _obs("DEL", 640)]
    before = gate_candidates(conn, obs, CONFIG, today=NOW)
    assert any(c.dest == "NYC" for c in before), "NYC should start eligible"

    _disprove(conn, "NYC")
    after = gate_candidates(conn, obs, CONFIG, today=NOW)
    assert not any(c.dest == "NYC" for c in after)


def test_the_block_is_per_itinerary_not_per_route(conn):
    """A route whose November fare was fiction may still have a real
    January one — muting the whole route would throw those away."""
    _disprove(conn, "NYC", dep="2026-11-06", ret="2026-11-13")
    obs = [_obs("NYC", 355, dep="2027-01-14", ret="2027-01-21"),
           _obs("MIA", 700), _obs("BKK", 740), _obs("EZE", 820),
           _obs("TYO", 900), _obs("DEL", 640)]
    cands = gate_candidates(conn, obs, CONFIG, today=NOW)
    assert any(c.dest == "NYC" for c in cands), "different dates, still eligible"


def test_the_window_expires(conn):
    _disprove(conn, "NYC", hours=-1)      # already elapsed
    obs = [_obs("NYC", 355), _obs("MIA", 700), _obs("BKK", 740),
           _obs("EZE", 820), _obs("TYO", 900), _obs("DEL", 640)]
    assert any(c.dest == "NYC"
               for c in gate_candidates(conn, obs, CONFIG, today=NOW))


def test_re_disproving_extends_rather_than_colliding(conn):
    """Second disproof of the same itinerary must upsert, not raise on
    the primary key."""
    _disprove(conn, "NYC", live=398)
    _disprove(conn, "NYC", live=421)
    rows = conn.execute("SELECT live FROM disproved").fetchall()
    assert len(rows) == 1 and rows[0]["live"] == 421


def test_a_disproved_price_cannot_skew_its_own_class_median(conn, tmp_path):
    """Dropped BEFORE the cross-section: a phantom price left in the
    class median drags the comparator down for every REAL candidate in
    that class — the phantom would quietly raise the bar for everyone
    else."""
    obs = [_obs("NYC", 20),      # the phantom
           _obs("MIA", 400),     # a genuine cheap fare
           _obs("BKK", 740), _obs("EZE", 820), _obs("TYO", 900),
           _obs("DEL", 640)]

    with connect(tmp_path / "clean.db") as clean:
        ensure_schema(clean)
        ensure_deals_schema(clean)
        with_phantom = gate_candidates(clean, obs, CONFIG, today=NOW)
    median_with = next(c.xsection_median for c in with_phantom
                       if c.dest == "MIA")

    _disprove(conn, "NYC", cached=20, live=900)
    without = gate_candidates(conn, obs, CONFIG, today=NOW)
    median_without = next(c.xsection_median for c in without
                          if c.dest == "MIA")

    assert median_without > median_with, (
        f"phantom dragged the median down: {median_with} -> {median_without}")


def test_one_way_itineraries_are_handled(conn):
    """NULL return_date would break the primary key; '' is the sentinel."""
    deals_db.record_disproved(conn, origin="MAD", dest="NYC",
                              depart_date="2026-11-06", return_date=None,
                              cached=355, live=398, hours=24)
    assert ("MAD", "NYC", "2026-11-06", "") in deals_db.active_disproved(conn)


def test_active_disproved_is_one_query_not_one_per_observation(conn):
    """The gate compares thousands of rows; a query per row would make
    the sweep unusable."""
    calls = []
    real = conn.execute

    class _Spy:
        def execute(self, sql, *a):
            if "FROM disproved" in sql:
                calls.append(sql)
            return real(sql, *a)

        def __getattr__(self, n):
            return getattr(conn, n)

    _disprove(conn, "NYC")
    obs = [_obs(f"X{i:02d}", 400 + i) for i in range(40)]
    gate_candidates(_Spy(), obs, CONFIG, today=NOW)
    assert len(calls) == 1, f"{len(calls)} disproved queries in one gate pass"


def test_run_deals_records_the_disproof_on_verification_failure():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "run_deals.py").read_text(
        encoding="utf-8")
    idx = src.index("if not verify.ok:")
    assert "record_disproved" in src[idx:idx + 1200]
    assert "hours=config.disproved_cooldown_hours" in src
