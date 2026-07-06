"""route_store: DB is the config source of truth, YAML is the seed.

Regression suite for the 2026-07-06 cloud outage: the UI wrote route
config to the container filesystem (ephemeral, drifts from git) and
nothing ever read the DB's config_json back.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from lib.config import (
    AlertParams,
    ConfigError,
    FollowupParams,
    RouteConfig,
    SearchWindow,
    StayPreferences,
    SweepParams,
    route_from_json,
)
from lib.db import connect, ensure_schema
from lib.route_store import (
    get_route_config_json,
    list_route_ids,
    load_effective_route,
    save_route_config,
)


def _route(*, name="t", watch=650) -> RouteConfig:
    return RouteConfig(
        name=name,
        origins=("MAD", "BCN"),
        destinations=("NBO",),
        search_window=SearchWindow(
            earliest_departure=date(2026, 9, 12),
            latest_return=date(2027, 1, 15),
        ),
        stay=StayPreferences(min_days=60, max_days=90),
        currency="EUR",
        sweep=SweepParams(cadence_days=3, skip_if_min_above=800),
        followup=FollowupParams(watch_below_price=watch, drop_above_price=800),
        alerts=AlertParams(drop_threshold_pct=15, baseline_window_days=30,
                           min_observations=4),
    )


def _write_yaml(routes_dir: Path, route: RouteConfig) -> None:
    from lib.config import save_route
    routes_dir.mkdir(parents=True, exist_ok=True)
    save_route(routes_dir / f"{route.name}.yaml", route)


def test_json_round_trip_is_lossless():
    r = _route()
    assert route_from_json(r.to_json()) == r


def test_yaml_seeds_empty_db_in_canonical_shape(tmp_path: Path):
    routes_dir = tmp_path / "routes"
    _write_yaml(routes_dir, _route())
    with connect(tmp_path / "t.db") as conn:
        ensure_schema(conn)
        route, source = load_effective_route(conn, "t", routes_dir)
        assert source == "yaml-seed"
        assert route == _route()
        stored = json.loads(get_route_config_json(conn, "t"))
        assert stored["route"]["name"] == "t"          # canonical shape
        assert stored["stay_preferences"]["min_days"] == 60


def test_db_wins_over_yaml(tmp_path: Path):
    """An operator edit saved to the DB beats the (stale) YAML seed."""
    routes_dir = tmp_path / "routes"
    _write_yaml(routes_dir, _route(watch=650))
    with connect(tmp_path / "t.db") as conn:
        ensure_schema(conn)
        save_route_config(conn, _route(watch=500))     # the operator edit
        route, source = load_effective_route(conn, "t", routes_dir)
        assert source == "db"
        assert route.followup.watch_below_price == 500  # not the YAML's 650


def test_legacy_asdict_row_parses_and_heals(tmp_path: Path):
    """Rows written before 2026-07-06 hold asdict(RouteConfig). They must
    load, and be rewritten in canonical shape on first read."""
    legacy = {
        "name": "t",
        "origins": ["MAD", "BCN"],
        "destinations": ["NBO"],
        "search_window": {"earliest_departure": "2026-09-12",
                          "latest_return": "2027-01-15"},
        "stay": {"min_days": 60, "max_days": 90},
        "currency": "EUR",
        "sweep": {"outbound_window_days": 14, "return_window_days": 14,
                  "overlap_days": 3, "cadence_days": 3,
                  "skip_if_min_above": 800, "skip_grace_days": None},
        "followup": {"watch_below_price": 650, "drop_above_price": 800},
        "alerts": {"drop_threshold_pct": 15, "baseline_window_days": 30,
                   "min_observations": 4},
    }
    routes_dir = tmp_path / "routes"
    with connect(tmp_path / "t.db") as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO routes (route_id, config_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("t", json.dumps(legacy), "2026-07-01T00:00:00Z", "2026-07-01T00:00:00Z"),
        )
        route, source = load_effective_route(conn, "t", routes_dir)
        assert source == "db"
        assert route == _route()
        healed = json.loads(get_route_config_json(conn, "t"))
        assert "route" in healed                        # canonical now
        # Second read: already canonical, still parses, still db-sourced.
        route2, source2 = load_effective_route(conn, "t", routes_dir)
        assert (route2, source2) == (route, "db")


def test_corrupt_db_row_falls_back_to_yaml_and_heals(tmp_path: Path):
    routes_dir = tmp_path / "routes"
    _write_yaml(routes_dir, _route())
    with connect(tmp_path / "t.db") as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO routes (route_id, config_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("t", "{not json", "2026-07-01T00:00:00Z", "2026-07-01T00:00:00Z"),
        )
        route, source = load_effective_route(conn, "t", routes_dir)
        assert source == "yaml-seed"
        assert route == _route()
        assert "route" in json.loads(get_route_config_json(conn, "t"))


def test_missing_everything_raises_config_error(tmp_path: Path):
    with connect(tmp_path / "t.db") as conn:
        ensure_schema(conn)
        with pytest.raises(ConfigError):
            load_effective_route(conn, "nope", tmp_path / "routes")


def test_list_route_ids_unions_db_and_yaml(tmp_path: Path):
    routes_dir = tmp_path / "routes"
    _write_yaml(routes_dir, _route(name="yaml-only"))
    with connect(tmp_path / "t.db") as conn:
        ensure_schema(conn)
        save_route_config(conn, _route(name="db-only"))
        assert list_route_ids(conn, routes_dir) == ["db-only", "yaml-only"]
