"""Route config persistence: DB is the source of truth, YAML is the seed.

Why this module exists: the deployed UI's "Save as default" used to write
the route YAML back onto the container filesystem. On Streamlit Cloud that
file is ephemeral AND diverges from git — a mutated copy crashed the app
at startup on 2026-07-06, and every saved edit silently vanished on
restart. The `routes` table always had a `config_json` column, but nothing
ever read it back.

The rule, implemented by `load_effective_route`:

    DB row wins when present and parseable.
    Otherwise the YAML file seeds the DB and becomes the config.

Both the CLI (`run_scan.py`) and the UI load through here, so an edit
saved in the deployed UI is the config the next scheduled scan runs with —
no filesystem writes anywhere.

Kept separate from lib/config.py (which must stay import-free of lib/db
to avoid a cycle) and from lib/db.py (which holds no business logic).
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import ConfigError, RouteConfig, load_route, route_from_json
from .db import upsert_route

LOG = logging.getLogger(__name__)


def get_route_config_json(conn, route_id: str) -> str | None:
    row = conn.execute(
        "SELECT config_json FROM routes WHERE route_id = ?", (route_id,)
    ).fetchone()
    return row[0] if row else None


def save_route_config(conn, route: RouteConfig) -> None:
    """Persist `route` as the effective config (canonical JSON, upsert)."""
    upsert_route(conn, route)


def load_effective_route(
    conn, route_id: str, routes_dir: str | Path
) -> tuple[RouteConfig, str]:
    """Load the effective config for `route_id`.

    Returns (route, source) where source is:
      "db"        — DB row present and parseable (self-healed to canonical
                    shape if it was a legacy asdict snapshot);
      "yaml-seed" — no usable DB row; the YAML file seeded the DB.

    Raises ConfigError only when BOTH the DB row and the YAML are unusable
    — callers surface that as a visible error, not a crash loop.
    """
    routes_dir = Path(routes_dir)
    raw = get_route_config_json(conn, route_id)
    if raw is not None:
        try:
            route = route_from_json(raw)
            if route.name != route_id:
                raise ConfigError(
                    f"config_json route name {route.name!r} != row id {route_id!r}"
                )
            # Self-heal: rows written before 2026-07-06 hold the legacy
            # asdict shape. Re-saving after a successful parse converts
            # them to canonical exactly once (canonical rows re-serialize
            # identically, so this is idempotent).
            if route.to_json() != _normalized(raw):
                save_route_config(conn, route)
                LOG.info("route %s: healed legacy config_json to canonical shape",
                         route_id)
            return route, "db"
        except ConfigError as exc:
            LOG.warning("route %s: unusable DB config (%s); falling back to YAML",
                        route_id, exc)

    yaml_path = routes_dir / f"{route_id}.yaml"
    try:
        route = load_route(yaml_path)
    except (OSError, ConfigError) as exc:
        raise ConfigError(
            f"route {route_id!r}: no usable DB config and YAML seed failed: {exc}"
        ) from exc
    save_route_config(conn, route)
    return route, "yaml-seed"


def list_route_ids(conn, routes_dir: str | Path) -> list[str]:
    """All known route ids: union of DB rows and routes/*.yaml stems."""
    ids = {p.stem for p in Path(routes_dir).glob("*.yaml")}
    ids.update(r[0] for r in conn.execute("SELECT route_id FROM routes").fetchall())
    return sorted(ids)


def _normalized(raw_json: str) -> str:
    """Re-serialize for shape comparison; unparseable input compares unequal."""
    import json
    try:
        return json.dumps(json.loads(raw_json), sort_keys=True)
    except (TypeError, ValueError):
        return ""
