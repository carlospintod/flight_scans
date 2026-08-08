"""ntfy.sh pushes for the deal pipeline (DB-free, never raises).

Mistake-class candidates bypass the daily ritual via ntfy (D3): the
queue push is how Carlos learns there is something to approve without
opening the console. Quiet runs push nothing (notification blindness).
"""

from __future__ import annotations

import logging
import os

import requests

LOG = logging.getLogger(__name__)


def push(title: str, body: str, *, priority: str = "default",
         tags: str = "") -> bool:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    try:
        requests.post(
            f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags},
            timeout=15)
        return True
    except Exception as exc:  # noqa: BLE001 — a push hiccup never breaks a run
        LOG.warning("ntfy push failed: %s", exc)
        return False


def push_queued(n_queued: int, top_line: str) -> bool:
    if n_queued <= 0:
        return False
    return push(f"Vuelazo: {n_queued} chollo(s) en cola", top_line,
                priority="default", tags="airplane")


def push_mistake(origin: str, dest: str, price: int, currency: str) -> bool:
    return push(
        f"Vuelazo MISTAKE? {origin}->{dest} {price} {currency}",
        "Posible tarifa error retenida: necesita segunda familia de "
        "verificación / aprobación en 30s.",
        priority="high", tags="rotating_light")
