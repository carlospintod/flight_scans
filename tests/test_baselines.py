"""Per-route baselines + incremental gate activation (M1, D2)."""

from datetime import datetime, timedelta, timezone

import pytest

from lib.baselines import mature_routes, route_baseline
from lib.db import connect, ensure_schema
from lib.dealconfig import load_deal_config
from lib.dealgate import gate_candidates
from lib.deals_db import Observation, ensure_deals_schema, insert_observations

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
CONFIG = load_deal_config()


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        ensure_deals_schema(c)
        yield c


def _seed_verified(conn, origin, dest, prices, *, days_ago=5):
    ts = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for p in prices:
        conn.execute(
            """
            INSERT INTO fare_observations
                (origin, dest, depart_date, return_date, price, currency,
                 source, source_family, found_at, observed_at, is_verified)
            VALUES (?, ?, '2026-09-10', '2026-09-17', ?, 'EUR',
                    'serpapi', 'google', NULL, ?, 1)
            """,
            (origin, dest, p, ts))


def test_baseline_needs_min_observations(conn):
    _seed_verified(conn, "VLC", "LON", [100, 110, 120])
    assert route_baseline(conn, origin="VLC", dest="LON", window_days=60,
                          min_observations=8, now=NOW) is None


def test_baseline_median_and_p10(conn):
    _seed_verified(conn, "VLC", "LON", [80, 90, 100, 110, 120, 130, 140, 150])
    bl = route_baseline(conn, origin="VLC", dest="LON", window_days=60,
                        min_observations=8, now=NOW)
    assert bl and bl.n == 8
    assert bl.median == 115
    assert bl.p10 < bl.median


def test_baseline_window_excludes_old_rows(conn):
    _seed_verified(conn, "VLC", "LON", [100] * 8, days_ago=90)
    assert route_baseline(conn, origin="VLC", dest="LON", window_days=60,
                          min_observations=8, now=NOW) is None
    assert mature_routes(conn, window_days=60, min_observations=8,
                         now=NOW) == []


def _obs(dest, price, origin="VLC"):
    return Observation(origin=origin, dest=dest, depart_date="2026-09-10",
                       return_date="2026-09-17", price=price, currency="EUR",
                       source="aviasales", source_family="cached",
                       found_at=None)


def test_gate_switches_to_baseline_when_mature(conn):
    # LON has 8 verified obs around 120-150 — cached 79 is below P10,
    # >=25% below median, saving >= 30 floor.
    _seed_verified(conn, "VLC", "LON",
                   [118, 122, 128, 132, 138, 142, 148, 152])
    routes = [_obs("LON", 79), _obs("FCO", 96), _obs("CDG", 121),
              _obs("AMS", 134), _obs("MXP", 118), _obs("LGW", 142)]
    cands = gate_candidates(conn, routes, CONFIG, today=NOW)
    lon = next(c for c in cands if c.dest == "LON")
    assert lon.gate_mode == "baseline"
    assert lon.baseline_median == 135
    assert lon.rejected_reason is None
    # The immature siblings still gate via cross-section (5 routes >= 4).
    assert all(c.gate_mode == "crosssection"
               for c in cands if c.dest != "LON")


def test_baseline_gate_requires_p10(conn):
    # Price >=25% below the median but ABOVE P10 -> no candidate (D2 gate
    # is P10 AND 25% AND floor). Inclusive quantiles: p10 of
    # [40,120,...,150] = 40 + 0.7*(120-40) = 96; median = 132.
    # 98 <= 132*0.75 (25% below, saving 34 >= floor 30) but 98 > 96.
    _seed_verified(conn, "VLC", "LON",
                   [40, 120, 125, 130, 135, 140, 145, 150])
    routes = [_obs("LON", 98)]
    cands = gate_candidates(conn, routes, CONFIG, today=NOW)
    assert cands == []


def test_baseline_p10_never_extrapolates_below_min(conn):
    # The exclusive method would put p10 BELOW the observed minimum at
    # n=8 (gate could then never fire); inclusive stays within range.
    _seed_verified(conn, "VLC", "LON",
                   [100, 105, 110, 115, 120, 125, 130, 135])
    bl = route_baseline(conn, origin="VLC", dest="LON", window_days=60,
                        min_observations=8, now=NOW)
    assert bl is not None and bl.p10 >= 100


def test_watchlist_refresh_uses_month_pairs_not_latest_prices():
    """latest_prices echoed return==depart, which asked Google for a
    same-day round trip and killed 100% of long-haul verifications."""
    from datetime import date
    from types import SimpleNamespace

    from lib.dealpipe import watchlist_refresh

    class _Avia:
        def __init__(self):
            self.calls = []

        def prices_for_dates(self, *, origin, destination, depart_month,
                             currency):
            self.calls.append((origin, destination, depart_month))
            q = SimpleNamespace(origin=origin, destination=destination,
                                departure_date="2026-09-01",
                                return_date="2026-09-15", price=480,
                                currency="EUR", found_at=None)
            return SimpleNamespace(quotes=(q,))

        def latest_prices(self, **kw):  # must never be called
            raise AssertionError("latest_prices is banned from the pipeline")

    avia = _Avia()
    obs = watchlist_refresh(avia, routes=[("MAD", "NYC", 2), ("VLC", "LON", 1)],
                            currency="EUR", today=date(2026, 8, 8))
    assert avia.calls == [("MAD", "NYC", "2026-08"), ("MAD", "NYC", "2026-09"),
                          ("VLC", "LON", "2026-08")]
    assert len(obs) == 3
    assert all(o.return_date != o.depart_date for o in obs)


def test_config_horizon_reaches_long_haul_window():
    """At +1/+2 months the cache holds no long-haul from any Spanish
    origin; it appears from +3. The horizon is the whole ballgame."""
    assert set(CONFIG.origins) == {"MAD", "BCN", "VLC", "ALC"}
    assert CONFIG.sweep_months_ahead >= 4
    assert "route" in CONFIG.sweep_sortings  # breadth pass, not just cheapest
    assert len(CONFIG.watchlist["long"]) >= 20
    assert CONFIG.watchlist_months["long"] > CONFIG.watchlist_months["intra_eu"]
    assert CONFIG.min_observations >= 2


def test_watchlist_routes_expand_per_origin_and_class():
    from lib.dealpipe import watchlist_routes
    routes = watchlist_routes(CONFIG)
    longs = [r for r in routes if r[1] == "NYC"]
    assert len(longs) == 4  # one per origin
    assert all(r[2] == CONFIG.watchlist_months["long"] for r in longs)
    assert all(o != d for o, d, _ in routes)
