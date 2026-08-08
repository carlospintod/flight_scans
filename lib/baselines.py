"""Per-route price baselines (D2 mature state, activated incrementally).

A route's baseline is the trailing-`window_days` distribution of its
VERIFIED fare observations (cached rows nominate, they never form the
"precio normal" we publish). Percentiles, not z-scores — fare
distributions are skewed/multimodal.

Activation mirrors the existing min_observations pattern: a route gets
its per-route percentile gate the moment it has enough verified history;
until then the day-one cross-section gate applies (lib/dealgate).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Baseline:
    origin: str
    dest: str
    n: int
    median: int
    p10: int


def route_baseline(conn, *, origin: str, dest: str, window_days: int,
                   min_observations: int,
                   now: datetime | None = None) -> Baseline | None:
    """The route's trailing verified-price distribution, or None while
    history is too thin for the percentile gate."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """
        SELECT price FROM fare_observations
        WHERE origin = ? AND dest = ? AND is_verified = 1
          AND observed_at >= ?
        ORDER BY price
        """,
        (origin, dest, since),
    ).fetchall()
    prices = [r["price"] for r in rows]
    if len(prices) < max(2, min_observations):
        return None
    median = int(statistics.median(prices))
    # P10 via the INCLUSIVE quantile method: cut points stay inside the
    # observed range. The default 'exclusive' method extrapolates BELOW
    # the minimum at small n (quantiles([1..8])[0] == 0.9), which would
    # make the P10 gate demand "beat the best price ever seen" the
    # moment a route matures — the gate would never fire again.
    p10 = int(statistics.quantiles(prices, n=10, method="inclusive")[0])
    return Baseline(origin=origin, dest=dest, n=len(prices),
                    median=median, p10=p10)


def mature_routes(conn, *, window_days: int, min_observations: int,
                  now: datetime | None = None) -> list[tuple[str, str]]:
    """(origin, dest) pairs whose verified history already supports the
    per-route gate — the audit and the SEO-page gate both read this."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """
        SELECT origin, dest, COUNT(*) AS n FROM fare_observations
        WHERE is_verified = 1 AND observed_at >= ?
        GROUP BY origin, dest HAVING n >= ?
        """,
        (since, max(2, min_observations)),
    ).fetchall()
    return [(r["origin"], r["dest"]) for r in rows]
