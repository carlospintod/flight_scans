"""The route's OWN recent price history — the honest comparator.

Every Google Flights verification we already pay for carries 61 daily
price points for the searched itinerary, and until now we threw them
away. Measured 2026-08-13, MAD->JFK 6-13 Nov:

    window   2026-06-13 -> 2026-08-12   (60 days back, ending yesterday)
    spacing  uniformly 24h, 61 points
    prices   min 326  p10 ~340  median 379  max 466
    live     398

Why this replaces what came before:

  * The CLASS CROSS-SECTION median compares a route to unrelated routes.
    It called a EUR 37 Turin hop a EUR 275 saving (vs a Gulf-heavy
    median) and a normal EUR 120 Wizz fare to Kutaisi a EUR 278 one.

  * `typical_price_range` is NOT this itinerary's history. Measured on
    the same response: typical [350, 550] while the itinerary's own 60
    days ran 326-466. Its low sits ABOVE the real minimum and its high
    far above the real maximum, so it is a broader route-level
    aggregate. On BCN->JFK it returned [360, 1050] — a 3x spread that
    cannot discriminate anything. Worse, the absolute floor built on it
    (typical_low - live >= class floor) demanded prices below the
    60-day MINIMUM on every long-haul route sampled: mathematically
    unreachable, which is why nothing ever passed.

  * `lowest_price` is the cheapest CURRENTLY BOOKABLE fare, not a
    historical low — it equalled the live price on every sample. The
    name invites exactly the wrong assumption.

KNOWN LIMIT, stated rather than hidden: the 60 days trail the SEARCH
date, not the departure. For a fixed future trip the series therefore
mixes market movement with the booking curve (fares usually drift up as
departure nears). Comparing today against that trailing window still
answers "cheaper than this trip has been recently", which is the claim
the product actually makes — but it is not a seasonality model, and a
fare that is cheap because departure is far away is not a vuelazo.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass

# Google returns ~61 daily points. Fewer than this and percentiles are
# noise rather than a baseline — the verdict says so instead of guessing.
MIN_POINTS = 20


@dataclass(frozen=True)
class PriceHistory:
    points: int
    first_day: str
    last_day: str
    low: int
    p10: int
    median: int
    high: int

    def as_dict(self) -> dict:
        return asdict(self)


def parse_history(raw: dict) -> PriceHistory | None:
    """[[unix_ts, price], ...] -> typed stats. Defensive per CLAUDE.md #5:
    any row may be malformed and the block may be absent entirely."""
    from datetime import datetime, timezone

    pi = (raw or {}).get("price_insights")
    if not isinstance(pi, dict):
        return None
    series = pi.get("price_history")
    if not isinstance(series, (list, tuple)):
        return None
    pairs: list[tuple[int, int]] = []
    for row in series:
        if (isinstance(row, (list, tuple)) and len(row) == 2
                and isinstance(row[0], (int, float))
                and isinstance(row[1], (int, float)) and row[1] > 0):
            pairs.append((int(row[0]), int(row[1])))
    if len(pairs) < MIN_POINTS:
        return None
    pairs.sort()
    prices = sorted(p for _, p in pairs)
    idx = max(0, int(len(prices) * 0.10) - 1)

    def _day(ts: int) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")

    return PriceHistory(
        points=len(prices),
        first_day=_day(pairs[0][0]), last_day=_day(pairs[-1][0]),
        low=prices[0], p10=prices[idx],
        median=int(statistics.median(prices)), high=prices[-1],
    )


@dataclass(frozen=True)
class HistoryVerdict:
    """Where a live price sits inside its own route's recent history."""
    level: str            # record | low | typical | high | unknown
    is_deal: bool
    pct_below_median: float
    saving_vs_median: int
    history: dict | None
    note: str             # Spanish — this sentence IS the product's claim

    def as_dict(self) -> dict:
        return asdict(self)


def assess(live_price: int | None, raw: dict, *, route_class: str,
           min_pct_below: dict[str, float]) -> HistoryVerdict:
    """Is `live_price` a genuine low for THIS itinerary?

    A deal is either a RECORD (below anything the trip has cost in the
    window) or a bottom-decile price that is also meaningfully below the
    route's own median. The percentage is per route class and lives in
    config: a percentile alone would call a 7%-below-median fare a
    vuelazo, and an absolute euro floor cannot scale across routes whose
    medians differ by an order of magnitude.
    """
    hist = parse_history(raw)
    if live_price is None or hist is None:
        return HistoryVerdict(
            "unknown", False, 0.0, 0, hist.as_dict() if hist else None,
            "sin histórico suficiente de esta ruta")
    saving = hist.median - live_price
    pct = (saving / hist.median * 100.0) if hist.median else 0.0
    need = float(min_pct_below.get(route_class, 20.0))

    if live_price < hist.low:
        level = "record"
    elif live_price <= hist.p10:
        level = "low"
    elif live_price <= hist.high:
        level = "typical"
    else:
        level = "high"

    is_deal = level == "record" or (level == "low" and pct >= need)
    if level == "record":
        note = (f"lo más barato en {hist.points} días: {live_price} € "
                f"(mínimo anterior {hist.low} €, mediana {hist.median} €)")
    elif level == "low":
        note = (f"{live_price} € — decil más bajo de los últimos "
                f"{hist.points} días ({hist.low}–{hist.high} €), "
                f"{pct:.0f}% bajo la mediana ({hist.median} €)")
        if not is_deal:
            note += f"; por debajo del {need:.0f}% exigido para {route_class}"
    else:
        note = (f"{live_price} € es precio {'normal' if level == 'typical' else 'alto'} "
                f"aquí: {hist.low}–{hist.high} € en {hist.points} días, "
                f"mediana {hist.median} €")
    return HistoryVerdict(level, is_deal, round(pct, 1), saving,
                          hist.as_dict(), note)
