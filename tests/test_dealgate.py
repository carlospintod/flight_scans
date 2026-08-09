"""D2 day-one gate: cross-section + floors + guardrails, all offline."""

from datetime import datetime, timedelta, timezone

import pytest

from lib.db import connect, ensure_schema
from lib.dealconfig import load_deal_config
from lib.dealgate import (Candidate, cheapest_per_route, classify_route,
                          gate_candidates)
from lib.deals_db import (Observation, ensure_deals_schema, insert_deal,
                          ensure_deals_schema as _eds)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def config():
    return load_deal_config()


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        ensure_deals_schema(c)
        yield c


def _obs(dest, price, *, origin="VLC", dep="2026-09-10", ret="2026-09-17"):
    return Observation(origin=origin, dest=dest, depart_date=dep,
                       return_date=ret, price=price, currency="EUR",
                       source="aviasales", source_family="cached",
                       found_at=None)


# Six intra_eu routes; MRS at 38 sits far below the class median and past
# the EUR 30 intra-EU floor — the canonical M0 candidate.
INTRA = [_obs("MRS", 38), _obs("FCO", 96), _obs("CDG", 121),
         _obs("AMS", 134), _obs("MXP", 118), _obs("LGW", 142)]


def test_classify_route_never_guesses(config):
    """An unknown code must NOT inherit a class. Defaulting to 'medium'
    is what benchmarked Turin against a Tel Aviv median and gave a EUR 37
    Ryanair hop the 1.3x medium weight."""
    assert classify_route("MRS", config) == "intra_eu"
    assert classify_route("NBO", config) == "medium"
    assert classify_route("JFK", config) == "long"
    assert classify_route("XXX", config) == "unclassified"
    # The exact codes behind the phantom BCN->TRN "vuelazo".
    for code in ("TRN", "AHO", "VIL"):
        assert classify_route(code, config) == "intra_eu"
    # Russia/CIS is classified-but-never-alerted, not unclassified.
    assert classify_route("MOW", config) == "excluded"


def test_unclassified_and_excluded_never_become_candidates(conn, config):
    """They must not even form a cross-section — otherwise the unknown
    bucket sets its own median and manufactures savings."""
    routes = [_obs("XXX", 20), _obs("YYY", 25), _obs("ZZZ", 30),
              _obs("WWW", 200), _obs("MOW", 40), _obs("LED", 45),
              _obs("KZN", 50), _obs("UFA", 300)]
    cands = gate_candidates(conn, routes, config, today=NOW)
    assert cands == []


def test_cheapest_per_route_keeps_the_minimum():
    doubled = INTRA + [_obs("MRS", 55)]
    best = {(o.origin, o.dest): o.price for o in cheapest_per_route(doubled)}
    assert best[("VLC", "MRS")] == 38


def test_gate_produces_the_obvious_candidate(conn, config):
    cands = gate_candidates(conn, INTRA, config, today=NOW)
    live = [c for c in cands if c.rejected_reason is None]
    assert [c.dest for c in live] == ["MRS"]
    c = live[0]
    assert c.route_class == "intra_eu"
    assert c.xsection_median == 119  # median of 38,96,118,121,134,142
    assert c.abs_saving == 81 and c.abs_saving >= config.floors["intra_eu"]
    assert c.pct_below > config.crosssection_pct
    assert c.score > 0


def test_gate_needs_a_minimum_class_size(conn, config):
    cands = gate_candidates(conn, INTRA[:3], config, today=NOW)
    assert cands == []  # 3 routes < MIN_CLASS_SIZE — no cross-section


def test_thin_saving_below_floor_never_nominates(conn, config):
    # 25%+ below median but saving under the long-haul floor of 150.
    routes = [_obs("JFK", 300), _obs("EZE", 430), _obs("GRU", 420),
              _obs("MEX", 440)]
    cands = gate_candidates(conn, routes, config, today=NOW)
    # median 425, JFK saves 125 < 150 floor -> out.
    assert all(c.dest != "JFK" for c in cands
               if c.rejected_reason is None)


def test_gate_never_classifies_mistake_cold_start(conn, config):
    """Mistake-class is a VERIFY-time call (route-specific typical range);
    a class-wide P25 would flag normal ULCC fares as error fares."""
    routes = [_obs("MRS", 20), _obs("FCO", 96), _obs("CDG", 121),
              _obs("AMS", 134), _obs("MXP", 118), _obs("LGW", 142)]
    cands = gate_candidates(conn, routes, config, today=NOW)
    mrs = next(c for c in cands if c.dest == "MRS")
    assert mrs.deal_class == "standard"


def test_cooldown_kills_repeat_within_window(conn, config):
    insert_deal(conn, origin="VLC", dest="MRS", price=40, currency="EUR",
                status="published",
                created_at=(NOW - timedelta(days=2)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"))
    cands = gate_candidates(conn, INTRA, config, today=NOW)
    mrs = next(c for c in cands if c.dest == "MRS")
    # 38 vs last 40 is within the 5% dedup band -> dedup wins the label.
    assert mrs.rejected_reason in ("route_cooldown", "dedup_price_band")


def test_guardrails_expire_outside_cooldown_window(conn, config):
    """Dedup + cooldown stop near-in-time repeats; months later the same
    route at a similar price is a NEW deal (seasonal recurrence)."""
    insert_deal(conn, origin="VLC", dest="MRS", price=38, currency="EUR",
                status="published",
                created_at=(NOW - timedelta(days=60)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"))
    cands = gate_candidates(conn, INTRA, config, today=NOW)
    mrs = next(c for c in cands if c.dest == "MRS")
    assert mrs.rejected_reason is None  # identical price, but 60 days out


def test_dead_rows_never_mute_a_route(conn, config):
    """Stranded candidate/expired rows from degraded runs never reached
    anyone — they must not dedup-kill the route's next nomination."""
    insert_deal(conn, origin="VLC", dest="MRS", price=38, currency="EUR",
                status="expired",
                created_at=(NOW - timedelta(days=1)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"))
    cands = gate_candidates(conn, INTRA, config, today=NOW)
    mrs = next(c for c in cands if c.dest == "MRS")
    assert mrs.rejected_reason is None


def test_cooldown_broken_by_further_drop(conn, config):
    insert_deal(conn, origin="VLC", dest="MRS", price=60, currency="EUR",
                status="published",
                created_at=(NOW - timedelta(days=2)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"))
    cands = gate_candidates(conn, INTRA, config, today=NOW)
    mrs = next(c for c in cands if c.dest == "MRS")
    # 38 is >10% below 60 and outside the dedup band -> survives.
    assert mrs.rejected_reason is None


def test_daily_cap_counts_existing_deals(conn, config):
    today = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    for i in range(config.daily_candidate_cap):
        insert_deal(conn, origin="VLC", dest=f"Z{i:02d}", price=100,
                    currency="EUR", status="candidate", created_at=today)
    cands = gate_candidates(conn, INTRA, config, today=NOW)
    mrs = next(c for c in cands if c.dest == "MRS")
    assert mrs.rejected_reason == "daily_cap"


def test_run_cap_limits_candidates_per_run(conn, config):
    # Two gate-passers, run cap forced to 1 -> second killed with run_cap.
    from dataclasses import replace
    config1 = replace(config, max_candidates_per_run=1)
    routes = INTRA + [_obs("OPO", 40)]
    cands = gate_candidates(conn, routes, config1, today=NOW)
    live = [c for c in cands if c.rejected_reason is None]
    assert len(live) == 1
    killed = [c for c in cands if c.rejected_reason == "run_cap"]
    assert len(killed) == 1
