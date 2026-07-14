"""The source registry — one declarative place per source.

Before this, a source was defined implicitly across ~6 files (clients,
POOL_SEEDS, METERED, planner roles, runner, web labels). This collapses
the DATA (pool config, metered methods, roles, and — new — the coverage
FAMILY and failure mode) into one object. `lib/quota.py` derives
POOL_SEEDS + METERED from it (proven byte-identical by a golden test);
`run_batch` reads the role map; the confidence model reads the family.

The FAMILY is the key addition from the 2026-07-13 coverage audit: for
"are we sure we found the cheapest," what matters is how many
INDEPENDENT families cover a search, not how many endpoints succeed.
serpapi + googleflights + searchapi are all the ONE Google corpus;
flights_sky + skyscanner are the ONE Skyscanner corpus. Counting them
as families keeps the confidence score honest.

This module has no dependencies on the rest of lib (no import cycle):
it is pure data + derivations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Coverage families — independent views of the market.
FAMILY_GOOGLE = "google"            # Google Flights corpus (airline-metasearch)
FAMILY_OTA = "ota_metasearch"       # Skyscanner corpus (OTA-metasearch)
FAMILY_CACHED = "cached"            # Travelpayouts cached scout
FAMILY_SELF_TRANSFER = "self_transfer"  # Kiwi virtual-interlining (retired)


@dataclass(frozen=True)
class SourceSpec:
    id: str
    family: str
    roles: tuple[str, ...]                 # discovery | verification | corroboration
    env_var: str | None = None             # API key env var (None = keyless)
    metered: dict[str, int] = field(default_factory=dict)
    # POOL_SEEDS payload minus the id, or None if metered-but-unpooled
    # (break-glass / optional sources): (pool_kind, period_limit,
    #  reset_anchor_day, safety_margin, per_search_cap, per_run_cap)
    pool: tuple | None = None
    failure_mode: str = "unknown"          # clean_429|hard_limit_429|card_freemium_402|lifetime_cap|scraper
    enabled: bool = True
    note: str = ""


REGISTRY: tuple[SourceSpec, ...] = (
    # -- pooled sources (order defines POOL_SEEDS order) --
    SourceSpec(
        "kiwi", family=FAMILY_SELF_TRANSFER, roles=("discovery",),
        env_var="RAPIDAPI_KEY",
        metered={"range_search": 1, "round_trip_search": 1,
                 "one_way_search": 1, "one_way_range_search": 1},
        pool=("monthly", 300, 10, 15, 10, None),
        failure_mode="card_freemium_402", enabled=False,
        note="RETIRED 2026-07-13 — proxy is the 402 freemium trap; "
             "official Tequila invitation-gated. Opt-in only."),
    SourceSpec(
        "serpapi", family=FAMILY_GOOGLE, roles=("verification",),
        env_var="SERPAPI_KEY",
        metered={"point_query": 1, "booking_options": 1},
        pool=("monthly", 250, None, 25, 7, None),
        failure_mode="clean_429", enabled=True,
        note="Live Google Flights + booking_options (OTA sellers) + "
             "price_insights. No card. $25/mo -> 1000 switch."),
    SourceSpec(
        "aviasales", family=FAMILY_CACHED, roles=("discovery", "corroboration"),
        env_var="TRAVELPAYOUTS_TOKEN",
        metered={"cheap_prices": 1, "prices_for_dates": 1,
                 "latest_prices": 1, "one_way_month_prices": 1},
        pool=("rate_only", None, None, 0, None, None),
        failure_mode="clean_429", enabled=True,
        note="Travelpayouts cached date scout — leads to verify, "
             "never a trusted fare."),
    SourceSpec(
        "googleflights", family=FAMILY_GOOGLE, roles=("verification", "discovery"),
        env_var=None,
        metered={"point_query": 1},
        pool=("per_run", None, None, 0, 25, 30),
        failure_mode="scraper", enabled=True,
        note="fast-flights/Playwright — free, best-effort "
             "(captcha-prone from CI). Same corpus as serpapi."),
    # -- metered but UNPOOLED (break-glass / optional, off by default) --
    SourceSpec(
        "flights_sky", family=FAMILY_OTA, roles=("discovery", "verification"),
        env_var="RAPIDAPI_KEY",
        metered={"search_roundtrip": 1, "search_one_way": 1,
                 "flight_details": 1, "price_calendar": 1},
        pool=None, failure_mode="hard_limit_429", enabled=False,
        note="Skyscanner proxy (ntd119, 50/mo Hard Limit) — the OTA "
             "family: gotogate/mytrip/trip.com/kiwi sellers. DISABLED "
             "until no-card confirmed + search-endpoint sample."),
    SourceSpec(
        "skyscanner", family=FAMILY_OTA, roles=("corroboration",),
        env_var="RAPIDAPI_KEY",
        metered={"point_query": 2, "search_airport": 1},
        pool=None, failure_mode="hard_limit_429", enabled=False,
        note="Sky-Scrapper (apiheya, 20/mo Hard Limit) — OTA breadth + "
             "price calendar. Second backend of the OTA family."),
    SourceSpec(
        "searchapi", family=FAMILY_GOOGLE, roles=("verification",),
        env_var="SEARCHAPI_KEY",
        metered={"point_query": 1, "calendar": 1},
        pool=None, failure_mode="lifetime_cap", enabled=False,
        note="Break-glass — 100 lifetime then $40/mo. Same Google corpus."),
)

_BY_ID = {s.id: s for s in REGISTRY}

# --- derivations consumed by lib/quota.py (golden-tested identical) ---
POOL_SEEDS: tuple[tuple, ...] = tuple(
    (s.id, *s.pool) for s in REGISTRY if s.pool is not None)
METERED: dict[str, dict[str, int]] = {
    s.id: dict(s.metered) for s in REGISTRY if s.metered}


def spec(source: str) -> SourceSpec | None:
    return _BY_ID.get(source)


def family_of(source: str) -> str | None:
    s = _BY_ID.get(source)
    return s.family if s else None


def role_map(sources: list[str] | None = None) -> dict[str, list[str]]:
    """{role: [sources]} restricted to `sources` (default: all enabled).
    Used for whole-role-blackout detection and confidence coverage."""
    pool = (sources if sources is not None
            else [s.id for s in REGISTRY if s.enabled])
    out: dict[str, list[str]] = {}
    for sid in pool:
        s = _BY_ID.get(sid)
        if not s:
            continue
        for role in s.roles:
            out.setdefault(role, []).append(sid)
    return out


def families_of(sources: list[str]) -> set[str]:
    """The distinct coverage families the given sources represent — the
    honest 'how many independent views' count for confidence."""
    return {f for sid in sources if (f := family_of(sid))}


def managed_env_vars() -> list[str]:
    """Distinct API-key env vars the /ops key manager owns (keyless
    sources excluded). Infra secrets (TURSO_*, SESSION_SECRET) are NOT
    here — they can't live in the DB they secure."""
    seen: list[str] = []
    for s in REGISTRY:
        if s.env_var and s.env_var not in seen:
            seen.append(s.env_var)
    return seen


def sources_for_env_var(env_var: str) -> list[str]:
    return [s.id for s in REGISTRY if s.env_var == env_var]
