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

    def as_dict(self) -> dict:
        return {"level": self.level, "score": self.score,
                "families": self.families,
                "live_verification": self.live_verification, "note": self.note}


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


def confidence_drop_push(current: ConfidenceResult,
                         prior: ConfidenceResult | None) -> dict | None:
    """A push when confidence worsened a level (e.g. an OTA/live family
    went dark and we fell to cached-only). Transition-based so a
    persistently-low state doesn't re-page every run."""
    if prior is None:
        return None
    if _RANK[current.level] < _RANK[prior.level] and current.level in ("low", "medium"):
        return {"title": f"Best-price confidence dropped to {current.level}",
                "body": current.note,
                "priority": "high" if current.level == "low" else "default",
                "tags": "chart_with_downwards_trend"}
    return None
