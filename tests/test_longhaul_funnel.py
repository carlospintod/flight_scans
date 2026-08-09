"""Regressions for the long-haul blindness bugs (2026-08-08 diagnosis).

Each test pins one of the four mechanisms that made a EUR 37 Ryanair hop
out-rank a transatlantic fare — and kept transatlantic out of the funnel
entirely.
"""

from datetime import date, datetime, timezone

import pytest

from lib.dealconfig import DealConfigError, load_deal_config, load_route_classes
from lib.dealgate import classify_route, gate_candidates
from lib.db import connect, ensure_schema
from lib.deals_db import Observation, ensure_deals_schema
from lib import dealpipe

CONFIG = load_deal_config()
NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        ensure_deals_schema(c)
        yield c


def _obs(dest, price, origin="BCN"):
    return Observation(origin=origin, dest=dest, depart_date="2026-11-10",
                       return_date="2026-11-20", price=price, currency="EUR",
                       source="aviasales", source_family="cached",
                       found_at=None)


# -- BUG 1: the 60-day horizon hid every long-haul fare ---------------------

def test_sweep_horizon_reaches_the_long_haul_window():
    months = dealpipe.sweep_months(date(2026, 8, 8), CONFIG.sweep_months_ahead)
    assert len(months) >= 4
    # Measured: long-haul only appears in the cache from +3 months.
    assert "2026-11" in months


# -- BUG 2: unknown codes silently inherited `medium` -----------------------

def test_turin_is_intra_eu_not_medium():
    """TRN defaulting to `medium` is precisely what let BCN->TRN at
    EUR 37 be scored against a Middle-East median and win the queue."""
    assert classify_route("TRN", CONFIG) == "intra_eu"
    for code in ("AHO", "VIL", "GRO", "LCA", "REK"):
        assert classify_route(code, CONFIG) in ("intra_eu", "medium")


def test_route_classes_have_no_duplicate_codes():
    """A code in two classes used to resolve silently by YAML order."""
    assert load_route_classes()  # raises DealConfigError on a duplicate


def test_duplicate_class_membership_is_a_config_error(tmp_path):
    bad = tmp_path / "dupe.yaml"
    bad.write_text("intra_eu: [AAA]\nmedium: [AAA]\n", encoding="utf-8")
    with pytest.raises(DealConfigError, match="multiple classes"):
        load_route_classes(bad)


def test_caucasus_is_sellable_russia_is_not():
    assert classify_route("EVN", CONFIG) == "medium"     # Yerevan: flyable
    assert classify_route("TBS", CONFIG) == "medium"     # Tbilisi: flyable
    assert classify_route("MOW", CONFIG) == "excluded"   # closed airspace
    assert classify_route("KBP", CONFIG) == "excluded"   # Kyiv


# -- BUG 3: the price sort returned only the cheapest (intra-EU) ------------

def test_sweep_runs_a_breadth_pass_as_well_as_a_cheapest_pass():
    from types import SimpleNamespace

    calls = []

    class _Avia:
        def anywhere_prices(self, *, origin, month, currency, sorting, limit):
            calls.append((month, sorting, limit))
            return SimpleNamespace(quotes=())

    dealpipe.sweep_origin(_Avia(), origin="BCN", months=["2026-11"],
                          currency="EUR",
                          sortings=CONFIG.sweep_sortings,
                          limits=CONFIG.sweep_limits)
    sortings = {c[1] for c in calls}
    assert {"price", "route"} <= sortings
    route_call = next(c for c in calls if c[1] == "route")
    assert route_call[2] > 100  # breadth needs a bigger limit


# -- BUG 4: same-day round trips killed 100% of long-haul verification ------

def test_same_day_round_trip_never_reaches_verification():
    from lib.aviasales_api import _parse_quotes
    payload = {"data": [{"origin": "MAD", "destination": "NYC", "price": 549,
                         "departure_at": "2026-10-16T10:00:00Z",
                         "return_at": "2026-10-16T22:00:00Z"}]}
    q = _parse_quotes(payload, "eur", origin_default="MAD")[0]
    assert q.return_date is None


# -- The funnel end to end: long-haul must be able to win -------------------

def test_long_haul_outranks_a_cheap_short_haul_hop(conn):
    """The exact shape of the complaint: a genuine transatlantic deal
    must beat a EUR 37 intra-EU fare in the queue."""
    routes = [
        # intra_eu cross-section (median ~120)
        _obs("TRN", 37), _obs("FCO", 96), _obs("CDG", 121),
        _obs("AMS", 134), _obs("MXP", 118), _obs("LGW", 142),
        # long cross-section (median ~720)
        _obs("NYC", 372), _obs("MIA", 700), _obs("BKK", 740),
        _obs("EZE", 820), _obs("TYO", 900), _obs("DEL", 640),
    ]
    cands = gate_candidates(conn, routes, CONFIG, today=NOW)
    assert cands, "the gate produced nothing"
    top = cands[0]
    assert top.route_class == "long"
    assert top.dest == "NYC"
    trn = [c for c in cands if c.dest == "TRN"]
    if trn:
        assert trn[0].score < top.score


# -- The route-specific floor (measured always, enforced on decision) -------

def test_insights_floor_measures_against_the_route_not_the_class():
    from lib.dealgate import Candidate

    cand = Candidate(
        origin="BCN", dest="TRN", depart_date="2026-11-10",
        return_date="2026-11-20", price=37, currency="EUR",
        route_class="intra_eu", xsection_median=311, xsection_p25=100,
        pct_below=88.4, abs_saving=275, deal_class="standard", score=150.7,
        found_at=None)
    # Google says this route normally costs 40-110: a EUR 3 saving.
    verify = dealpipe.VerifyResult(True, 37, "Ryanair",
                                   {"typical_low": 40, "typical_high": 110},
                                   "live-confirmed")
    passed, note = dealpipe.insights_floor_check(cand, verify, CONFIG)
    assert passed is False and "3" in note

    # A real transatlantic deal clears it comfortably.
    nyc = Candidate(
        origin="MAD", dest="NYC", depart_date="2026-10-27",
        return_date="2026-11-03", price=372, currency="EUR",
        route_class="long", xsection_median=720, xsection_p25=500,
        pct_below=48.0, abs_saving=348, deal_class="standard", score=133.0,
        found_at=None)
    ok, _ = dealpipe.insights_floor_check(
        nyc, dealpipe.VerifyResult(True, 372, "Iberia",
                                   {"typical_low": 620, "typical_high": 900},
                                   "live-confirmed"), CONFIG)
    assert ok is True

    # No Google range for the route -> None (unknown), never a silent pass.
    unknown, _ = dealpipe.insights_floor_check(
        nyc, dealpipe.VerifyResult(True, 372, "Iberia", None, "ok"), CONFIG)
    assert unknown is None


# -- BUG 5: Google rejects the metro codes long-haul arrives as ---------

def test_metro_codes_are_swapped_for_an_airport_before_verifying():
    """MAD->NYC returns a hard SerpAPI error; MAD->JFK returns 5 flights
    and a typical range. Every metro-coded long-haul candidate died on
    this, which is most of the watchlist."""
    for metro, airport in (("NYC", "JFK"), ("LON", "LHR"), ("TYO", "HND"),
                           ("SAO", "GRU"), ("CHI", "ORD")):
        assert dealpipe.verification_airport(metro, CONFIG) == airport
    # Real airport codes pass through untouched.
    for code in ("JFK", "BKK", "MIA", "EZE"):
        assert dealpipe.verification_airport(code, CONFIG) == code


def test_verification_queries_the_airport_and_records_it():
    from types import SimpleNamespace

    from lib.dealgate import Candidate

    asked = {}

    class _Serp:
        def point_query(self, *, origin, destination, outbound, return_,
                        currency):
            asked["dest"] = destination
            return SimpleNamespace(
                best_flights=(SimpleNamespace(price=372, carriers="Iberia"),),
                raw={"price_insights": {"typical_price_range": [620, 900]}})

    cand = Candidate(
        origin="MAD", dest="NYC", depart_date="2026-10-27",
        return_date="2026-11-03", price=380, currency="EUR",
        route_class="long", xsection_median=720, xsection_p25=500,
        pct_below=47.0, abs_saving=340, deal_class="standard", score=133.0,
        found_at=None)
    result = dealpipe.verify_candidate(_Serp(), cand, CONFIG)
    assert asked["dest"] == "JFK", "the metro code reached Google unchanged"
    assert result.ok is True
    assert result.airport == "JFK"


def test_a_metro_tolerance_miss_says_which_airport_it_checked():
    from types import SimpleNamespace

    from lib.dealgate import Candidate

    class _Serp:
        def point_query(self, **kw):
            return SimpleNamespace(
                best_flights=(SimpleNamespace(price=900, carriers="Iberia"),),
                raw={})

    cand = Candidate(
        origin="MAD", dest="NYC", depart_date="2026-10-27",
        return_date="2026-11-03", price=380, currency="EUR",
        route_class="long", xsection_median=720, xsection_p25=500,
        pct_below=47.0, abs_saving=340, deal_class="standard", score=133.0,
        found_at=None)
    result = dealpipe.verify_candidate(_Serp(), cand, CONFIG)
    assert result.ok is False
    assert "JFK" in result.note and "metro" in result.note


def test_a_metro_may_not_map_to_itself(tmp_path):
    from lib.dealconfig import load_metro_airports
    bad = tmp_path / "m.yaml"
    bad.write_text("NYC: NYC\n", encoding="utf-8")
    with pytest.raises(DealConfigError, match="maps to itself"):
        load_metro_airports(bad)


def test_large_sweeps_insert_in_chunks(conn, monkeypatch):
    """The widened sweep made the observation batch ~10x bigger and a
    single executemany blew Turso's 60s HTTP timeout — after the run had
    already spent its whole discovery budget."""
    from lib import deals_db

    sizes = []

    class _Spy:
        """sqlite3.Connection methods are read-only; wrap instead."""

        def __init__(self, inner):
            self._inner = inner

        def executemany(self, sql, seq):
            seq = list(seq)
            sizes.append(len(seq))
            return self._inner.executemany(sql, seq)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    n = deals_db.insert_observations(
        _Spy(conn), [_obs(f"X{i:03d}", 100 + i) for i in range(1000)])
    assert n == 1000
    assert len(sizes) > 1, "1000 rows went out as one request"
    assert max(sizes) <= deals_db.OBS_CHUNK
    assert conn.execute(
        "SELECT COUNT(*) FROM fare_observations").fetchone()[0] == 1000


def test_publish_channels_are_config_driven():
    """Telegram is postponed; ntfy carries the alerts meanwhile."""
    assert "ntfy" in CONFIG.publish_channels
    assert "email" in CONFIG.publish_channels


# -- The enforced floor + the two-stage verify (2026-08-09) -------------

def _cand(**kw):
    from lib.dealgate import Candidate
    base = dict(origin="BCN", dest="KUT", depart_date="2027-01-31",
                return_date="2027-02-07", price=126, currency="EUR",
                route_class="medium", xsection_median=398, xsection_p25=180,
                pct_below=68.8, abs_saving=278, deal_class="standard",
                score=125.6, found_at=None)
    base.update(kw)
    return Candidate(**base)


def test_the_floor_is_enforced_now():
    """Carlos's call after the KUT alert: enforcement is ON."""
    assert CONFIG.insights_floor is True


def test_kut_would_be_rejected_today():
    """The alert that decided it, with its real numbers: BCN->KUT 120
    live-confirmed, 'saving' EUR 278 vs a Gulf-heavy class median — but
    Google's typical low for the route itself is 130. Real saving: 10.
    The class median published it; the enforced floor rejects it."""
    verify = dealpipe.VerifyResult(True, 120, "Wizz Air",
                                   {"typical_low": 130, "typical_high": 210},
                                   "live-confirmed")
    passed, note = dealpipe.insights_floor_check(_cand(), verify, CONFIG)
    assert passed is False
    assert "80" in note  # the medium-class floor it failed


def _fake(price, insights=None, fail=False):
    from types import SimpleNamespace

    class _Client:
        calls = 0

        def point_query(self, **kw):
            type(self).calls += 1
            if fail:
                raise RuntimeError("captcha wall")
            raw = {"price_insights": insights} if insights else {}
            return SimpleNamespace(
                best_flights=(SimpleNamespace(price=price, carriers="X"),),
                raw=raw)

    return _Client()


def test_second_opinion_agreement_carries_the_insights():
    first = dealpipe.verify_candidate(_fake(120), _cand(), CONFIG)
    assert first.ok and first.insights is None  # the scraper has no insights
    serp = _fake(122, insights={"typical_price_range": [130, 210]})
    final = dealpipe.second_opinion(serp, _cand(), first, CONFIG)
    assert final.ok is True
    assert final.live_price == 122          # the read with the receipt
    assert final.insights["typical_low"] == 130
    assert "x2" in final.note


def test_two_google_reads_disagreeing_is_a_verification_failure():
    """Cached said 126, the scraper saw 120, serpapi sees 190: someone
    is looking at a ghost fare. Publishing either number would be a
    guess — 'fuentes en desacuerdo' must kill the deal."""
    first = dealpipe.verify_candidate(_fake(120), _cand(), CONFIG)
    final = dealpipe.second_opinion(_fake(190), _cand(), first, CONFIG)
    assert final.ok is False
    assert "desacuerdo" in final.note


def test_serpapi_failure_keeps_the_free_verification_without_insights():
    """SerpAPI down must not kill a scraper-confirmed deal — but the
    floor becomes unknowable and the note says so out loud."""
    first = dealpipe.verify_candidate(_fake(120), _cand(), CONFIG)
    final = dealpipe.second_opinion(_fake(0, fail=True), _cand(), first,
                                    CONFIG)
    assert final.ok is True
    assert final.live_price == 120
    assert final.insights is None
    assert "sin insights" in final.note
    passed, _ = dealpipe.insights_floor_check(_cand(), final, CONFIG)
    assert passed is None  # recorded as unknowable, never a silent pass


def test_run_deals_verifies_free_first():
    """The wiring: the scraper is stage 1, serpapi only on survivors."""
    import pathlib
    deals = (pathlib.Path(__file__).resolve().parents[1]
             / "run_deals.py").read_text(encoding="utf-8")
    assert 'SRC_SCRAPER = "googleflights_vz"' in deals
    assert "second_opinion" in deals
