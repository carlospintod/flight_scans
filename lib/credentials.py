"""Owner-managed API keys, loaded from the DB into the environment.

Carlos manages the free-source API keys in /ops (stored in the
source_credentials table). At scan startup the runner loads them into
os.environ so every adapter's `from_env()` just works — no code change
in the adapters. The DB is authoritative for keys the owner set there;
anything not in the DB falls back to the existing env (GH Actions
secrets / local .env).

These are low-value free-tier flight keys (no payment/PII). Infra
secrets (TURSO_*, SESSION_SECRET) are deliberately NOT managed here —
they can't live in the DB they secure.
"""

from __future__ import annotations

import logging
import os

LOG = logging.getLogger(__name__)


def load_credentials_into_env(conn) -> int:
    """Copy source_credentials rows into os.environ (DB overrides env,
    since the owner set them deliberately). Returns how many were loaded.
    Never raises — a credentials hiccup must not abort a scan."""
    try:
        rows = conn.execute(
            "SELECT env_var, value FROM source_credentials").fetchall()
    except Exception as exc:  # noqa: BLE001 — table may not exist on a fresh DB
        LOG.info("no source_credentials to load (%s)", exc)
        return 0
    n = 0
    for r in rows:
        var, val = r["env_var"], r["value"]
        if var and val:
            os.environ[var] = val
            n += 1
    if n:
        LOG.info("loaded %d API key(s) from source_credentials", n)
    return n


def mask(value: str) -> str:
    """Never expose a full key. '••••1234' for the last 4 chars."""
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 4 else value
    return "••••" + tail
