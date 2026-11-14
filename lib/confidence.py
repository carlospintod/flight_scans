"""Best-price confidence (R5) — how sure are we we found the true cheapest?

The 2026-07-13 coverage audit's key correction: confidence must count
INDEPENDENT coverage FAMILIES, not endpoints or successful calls. serpapi
+ googleflights + searchapi are all the ONE Google corpus; flights_sky +
skyscanner are the ONE Skyscanner corpus. Two Google mirrors agreeing is
NOT two independent confirmations — treating it as such would make the
number lie.

- 1 live coverage family (usually Google) → ~85-92% of the true cheapest
  on mainstream long-haul (misses OTA-only fares).
- 2 independent families (Google + OTA/Skyscanner) → ~97-99%.
- cached-only (Travelpayouts, no live verification) → low; leads only.

And net-price honesty: an OTA teaser (Kiwi/gotogate/mytrip) is the lowest
DISPLAYED price but rises at checkout — a lead to verify, never a trusted
"cheapest bookable." Computed from the per-source health this module is
handed (which the ledger already produced). No new tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .sources import FAMILY_GOOGLE, FAMILY_OTA, families_of, family_of, spec

# The families that carry live, bookable-grade fares (a "coverage"
# family). Cached (Travelpayouts) helps discovery but doesn't count as an
# independent live confirmation.
_COVERAGE_FAMILIES = {FAMILY_GOOGLE, FAMILY_OTA}


@dataclass(frozen=True)
class ConfidenceResult:
    level: str                       # high | medium | low | no_data
    score: int                       # rough % band, for display only
    families: list[str] = field(default_factory=list)
    live_verification: bool = False
    note: str = ""
    # Date coverage of the owner search's window (audit F7, 2026-11-14):
    # {"priced", "possible", "fresh_days"}. None = not measured.
    coverage: dict | None = None
    # The level from source families alone, before the coverage cap. Drop
    # pushes compare THIS, so a window step (a week of new, unpriced
    # days) never pages as a "confidence drop".
    family_level: str | None = None

    def as_dict(self) -> dict:
        out = {"level": self.level, "score": self.score,
               "families": self.families,
               "live_verification": self.live_verification, "note": self.note,
               "family_level": self.family_level or self.level}
        if self.coverage is not None:
            out["coverage"] = self.coverage
        return out


_RANK = {"no_data": 0, "low": 1, "medium": 2, "high": 3}


def assess_confidence(source_health: dict) -> ConfidenceResult:
    """`source_health`: {source: SourceHealth} from lib.health. A source
    'produces' when its verdict is live or degraded (recent data)."""
    producing = [s for s, h in source_health.items()
                 if getattr(h, "verdict", None) in ("live", "degraded")]
    fams = families_of(producing)
    coverage = sorted(fams & _COVERAGE_FAMILIES)
    n_cov = len(coverage)

    live_verification = any(
        family_of(s) in _COVERAGE_FAMILIES
        and "verification" in (spec(s).roles if spec(s) else ())
        for s in producing)

    has_ota = FAMILY_OTA in fams
    ota_note = (" OTA prices are leads — verify net at checkout (teasers "
                "rise with fees).") if has_ota else ""

    if not producing:
        return ConfidenceResult("no_data", 0, [], False,
                                "no source produced data this run.")
    if n_cov == 0:
        # Only the cached scout produced — no live confirmation.
        return ConfidenceResult(
            "low", 72, sorted(fams), False,
            "discovery running cached-only — live sources dark; treat "
            "prices as stale leads." + ota_note)
    if n_cov == 1:
        return ConfidenceResult(
            "medium", 89, sorted(fams), live_verification,
            f"one live coverage family ({coverage[0]}) — good on major "
            f"carriers, may miss OTA-only fares." + ota_note)
    return ConfidenceResult(
        "high", 97, sorted(fams), live_verification,
        f"{n_cov} independent families ({', '.join(coverage)}) — airline "
        f"and OTA fares both covered." + ota_note)


# Coverage caps (F7): "medium ~89%" with 0.5% of the window priced said
# more than the data could. Below these shares of the window's date
# pairs priced, the level cannot be higher than the cap.
COVERAGE_CAPS = ((0.25, "low"), (0.75, "medium"))
COVERAGE_FRESH_DAYS = 21     # same window as the web radar's FRESH_WINDOW_DAYS


def window_coverage(conn, route, *, today, fresh_days: int = COVERAGE_FRESH_DAYS
                    ) -> tuple[int, int]:
    """(priced, possible) date pairs for a round-trip search, counted the
    way the web radar counts them: per origin, every departure day of the
    window x every SAMPLED stay that is back by latest_return; priced =
    such pairs with any observation in the last `fresh_days` days."""
    from datetime import timedelta

    if route.is_one_way:
        return 0, 0
    sw = route.search_window
    stays = route.stay.sampled()
    last = sw.last_departure(route.stay.min_days)
    per_origin = 0
    d = sw.earliest_departure
    while d <= last:
        per_origin += sum(1 for s in stays
                          if d + timedelta(days=s) <= sw.latest_return)
        d += timedelta(days=1)
    possible = per_origin * len(route.origins)
    if not possible:
        return 0, 0
    cutoff = (today - timedelta(days=fresh_days)).isoformat()
    marks = ",".join("?" * len(stays))
    row = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT origin, departure_date, "
        "stay_days FROM calendar_snapshots WHERE route_id = ? "
        "AND snapshot_at >= ? AND departure_date >= ? AND departure_date <= ? "
        f"AND return_date <= ? AND stay_days IN ({marks}))",
        (route.name, cutoff, sw.earliest_departure.isoformat(),
         last.isoformat(), sw.latest_return.isoformat(), *stays)).fetchone()
    return min(int(row[0] or 0), possible), possible


def with_coverage(conf: ConfidenceResult, priced: int, possible: int,
                  *, fresh_days: int = COVERAGE_FRESH_DAYS) -> ConfidenceResult:
    """Cap the family-based level by how much of the window is priced and
    say so in the note."""
    from dataclasses import replace

    if possible <= 0:
        return replace(conf, family_level=conf.level)
    share = priced / possible
    level = conf.level
    for limit, cap in COVERAGE_CAPS:
        if share < limit and _RANK[level] > _RANK[cap]:
            level = cap
            break
    line = (f"{priced:,} of {possible:,} date pairs priced in the last "
            f"{fresh_days} days ({share:.0%})")
    if level != conf.level:
        line += f" — capped at {level}"
    return replace(conf, level=level, family_level=conf.level,
                   note=f"{line}. {conf.note}",
                   coverage={"priced": priced, "possible": possible,
                             "fresh_days": fresh_days})


def confidence_drop_push(current: ConfidenceResult,
                         prior: ConfidenceResult | None) -> dict | None:
    """A push when confidence worsened a level (e.g. an OTA/live family
    went dark and we fell to cached-only). Transition-based so a
    persistently-low state doesn't re-page every run."""
    if prior is None:
        return None
    cur = current.family_level or current.level
    was = prior.family_level or prior.level
    if _RANK[cur] < _RANK[was] and cur in ("low", "medium"):
        return {"title": f"Best-price confidence dropped to {cur}",
                "body": current.note,
                "priority": "high" if current.level == "low" else "default",
                "tags": "chart_with_downwards_trend"}
    return None
