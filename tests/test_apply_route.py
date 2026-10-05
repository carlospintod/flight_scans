"""scripts/apply_route.py: YAML overrides the DB row; one-way pause is
targeted (destination + trip type + active only); dry run writes nothing."""

from __future__ import annotations

import dataclasses
import sys
from datetime import date
from pathlib import Path

from lib.config import SearchWindow, StayPreferences, route_from_json
from lib.db import connect, ensure_schema
from lib.route_store import get_route_config_json, save_route_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from apply_route import apply  # noqa: E402
from tests.test_route_store import _route, _write_yaml  # noqa: E402


def _search(conn, sid, status="active"):
    conn.execute(
        "INSERT INTO searches (search_id, user_id, status, created_at, updated_at)"
        " VALUES (?, 1, ?, 'x', 'x')", (sid, status))


def _setup(tmp_path):
    routes_dir = tmp_path / "routes"
    new = dataclasses.replace(
        _route(),
        search_window=SearchWindow(date(2026, 12, 27), date(2027, 3, 31)),
        stay=StayPreferences(min_days=30, max_days=90))
    _write_yaml(routes_dir, new)
    one_way = dataclasses.replace(_route(name="ow"), trip_type="one_way",
                                  stay=StayPreferences(0, 0))
    other = dataclasses.replace(_route(name="ow2"), trip_type="one_way",
                                destinations=("LHR",), stay=StayPreferences(0, 0))
    return routes_dir, new, one_way, other


def _status(conn, sid):
    return conn.execute("SELECT status FROM searches WHERE search_id = ?",
                        (sid,)).fetchone()[0]


def test_applies_yaml_and_pauses_only_matching_one_way(tmp_path):
    routes_dir, new, one_way, other = _setup(tmp_path)
    with connect(tmp_path / "t.db") as conn:
        ensure_schema(conn)
        for r in (_route(), one_way, other):
            save_route_config(conn, r)
        for sid in ("t", "ow", "ow2"):
            _search(conn, sid)
        paused = apply(conn, "t", routes_dir, pause_one_way_to="nbo",
                       dry_run=False)
        assert paused == ["ow"]
        assert route_from_json(get_route_config_json(conn, "t")) == new
        assert _status(conn, "ow") == "paused"
        assert _status(conn, "t") == "active"     # round trip untouched
        assert _status(conn, "ow2") == "active"   # other destination


def test_dry_run_writes_nothing(tmp_path):
    routes_dir, _new, one_way, _other = _setup(tmp_path)
    with connect(tmp_path / "t.db") as conn:
        ensure_schema(conn)
        save_route_config(conn, _route())
        save_route_config(conn, one_way)
        _search(conn, "ow")
        before = get_route_config_json(conn, "t")
        assert apply(conn, "t", routes_dir, pause_one_way_to="NBO",
                     dry_run=True) == ["ow"]
        assert get_route_config_json(conn, "t") == before
        assert _status(conn, "ow") == "active"
