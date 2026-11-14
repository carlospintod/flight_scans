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

Keys loaded from here are not Actions secrets, so CI does not mask them
in logs: nothing may print one (see redact_network_error).
"""

from __future__ import annotations

import logging
import os
import re

LOG = logging.getLogger(__name__)

# requests puts the request URL, query string included, into its exception
# messages, and lib/serpapi_io and lib/explore_api send the key as the
# `api_key` query param. The value runs to the next &, #, quote or space.
# Secrets in other spots (Aviasales' `token=`, the Telegram bot path, the
# ntfy topic) are cut by passing them as `key`.
_API_KEY_PARAM_RE = re.compile(r"(api_key=)[^&#\s'\"]+", re.IGNORECASE)


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


def redact_network_error(exc: BaseException, key: str | None = None) -> str:
    """`exc` as 'network error: <Type>: <message>', safe to log and store.

    Every `api_key=<value>` becomes `api_key=REDACTED`, and `key` (the
    caller's own secret: an API key, a token, a topic) is replaced
    wherever else it appears. The host, port and cause stay, because a
    network error is debugged with them. Raise the wrapping error
    `from None`: a chained cause would print the original, unredacted
    message in any traceback.
    """
    text = _API_KEY_PARAM_RE.sub(r"\g<1>REDACTED", str(exc))
    if key:
        text = text.replace(key, "REDACTED")
    return f"network error: {type(exc).__name__}: {text}"
