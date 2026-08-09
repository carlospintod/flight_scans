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
FAMILY_SERVICE = "service"          # non-fare services (drafting, publish fan-out)


@dataclass(frozen=True)
class SourceSpec:
    id: str
    family: str
    roles: tuple[str, ...]                 # discovery | verification | corroboration
    env_var: str | None = None             # API key env var (None = keyless)
    # The adapter this source is built from (default: its own id). Two
    # source ids can share one adapter — that is how a project gets its
    # OWN ledger pool on the same provider (serpapi_vz -> serpapi).
    backend: str | None = None
    # Used when env_var is unset: the other project's key, with a loud
    # warning. Sharing a key means sharing the PROVIDER's allowance, so
    # the pool below must be a self-imposed slice, not the full plan.
    env_var_fallback: str | None = None
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
        "serpapi", family=FAMILY_GOOGLE, roles=("discovery", "verification"),
        env_var="SERPAPI_KEY",
        metered={"point_query": 1, "booking_options": 1},
        pool=("monthly", 250, None, 25, 7, None),
        failure_mode="clean_429", enabled=True,
        note="PRIMARY discovery + verification rail (2026-07-14): live "
             "Google Flights date grid + booking_options (OTA sellers) + "
             "price_insights. Never captcha'd. No card. $25/mo -> 1000 switch."),
    SourceSpec(
        "aviasales", family=FAMILY_CACHED, roles=("discovery", "corroboration"),
        env_var="TRAVELPAYOUTS_TOKEN",
        metered={"cheap_prices": 1, "prices_for_dates": 1,
                 "latest_prices": 1, "one_way_month_prices": 1,
                 "anywhere_prices": 1},
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
        "searchapi", family=FAMILY_GOOGLE, roles=("discovery", "verification"),
        env_var="SEARCHAPI_KEY",
        metered={"point_query": 1, "calendar": 1},
        # LIFETIME credits, not renewing: reset_anchor_day=None means the
        # ledger NEVER presumes a reset — availability only moves via
        # /me-anchors and recorded spend. per_search_cap=28 = one full
        # mission-rectangle sweep; safety_margin=4 fails closed near zero.
        pool=("monthly", 100, None, 4, 28, None),
        failure_mode="lifetime_cap", enabled=True,
        note="RECTANGLE SWEEP rail (2026-07-16 coverage audit): "
             "google_flights_calendar prices the full (dep x ret) window "
             "in ~28 calls — the only true no-blind-spots discovery. "
             "Biweekly cadence, owner round-trip searches only "
             "(run_batch gates). 100 lifetime credits ~= 3 full sweeps."),
    # -- Vuelazo fare rails ---------------------------------------------
    # flight_scans (the Spain-Nairobi tracker) and Vuelazo are SEPARATE
    # projects that happen to share this repo, this database and — until
    # Carlos provisions dedicated accounts — these provider keys. They
    # must NOT share a quota pool: the tracker is a free-tier-only
    # project and a Vuelazo sweep that ate its 250 SerpAPI searches would
    # silently kill the tracker's verification rail.
    #
    # A distinct source id gives a distinct pool for free: quota_pools is
    # keyed by source, spend_events are summed per source, and
    # reserve()/GuardedClient already work per source. `backend` points
    # at the adapter to construct, so no new client code exists.
    #
    # Pool semantics with a SHARED key (env_var unset, fallback used):
    # the provider allowance is one pot, so the _vz pool is a SELF-imposed
    # slice seeded like the service rails — never anchored from the
    # provider counter, which reports the whole account and would let
    # both pools believe they own it. With a DEDICATED key, run_deals
    # anchors it from that account's own /account probe instead.
    SourceSpec(
        "serpapi_vz", family=FAMILY_GOOGLE, roles=("discovery", "verification"),
        env_var="SERPAPI_KEY_VZ", backend="serpapi",
        env_var_fallback="SERPAPI_KEY",
        metered={"point_query": 1, "booking_options": 1},
        # 50/mo: the measured headroom on the shared free key after the
        # tracker's own needs (2026-08-08 audit: ~194 of 250 committed).
        # This is a HOLDING number — it does not fund a daily deal
        # pipeline. Raising it means either starving the tracker or
        # buying capacity, which is Carlos's call (CLAUDE.md, budget).
        pool=("monthly", 50, None, 5, 7, None),
        failure_mode="clean_429", enabled=True,
        note="Vuelazo's slice of the Google rail. Verification of "
             "candidates + price_insights. Own pool so a deal sweep can "
             "never drain the NBO tracker's free 250."),
    SourceSpec(
        "searchapi_vz", family=FAMILY_GOOGLE, roles=("discovery", "verification"),
        env_var="SEARCHAPI_KEY_VZ", backend="searchapi",
        env_var_fallback=None,   # deliberately NO fallback: see note
        metered={"point_query": 1, "calendar": 1},
        # Developer plan = 10k searches/mo. Margin 200 ~= one heavy day.
        pool=("monthly", 10000, None, 200, 28, None),
        failure_mode="clean_429", enabled=False,
        note="OFF until Carlos buys the SearchAPI.io Developer plan and "
             "sets SEARCHAPI_KEY_VZ (CLAUDE.md: flipping a paid tier is "
             "his explicit action, never a code default). NO fallback to "
             "SEARCHAPI_KEY on purpose — those are 100 LIFETIME credits "
             "reserved for the tracker's rectangle sweeps."),
    SourceSpec(
        "aviasales_vz", family=FAMILY_CACHED, roles=("discovery", "corroboration"),
        env_var="TRAVELPAYOUTS_TOKEN_VZ", backend="aviasales",
        env_var_fallback="TRAVELPAYOUTS_TOKEN",
        metered={"cheap_prices": 1, "prices_for_dates": 1,
                 "latest_prices": 1, "one_way_month_prices": 1,
                 "anywhere_prices": 1},
        # Travelpayouts caps requests per second, not per month — sharing
        # the token costs the tracker nothing. Separate id anyway, so the
        # ledger can answer "what did Vuelazo spend" per project.
        pool=("rate_only", None, None, 0, None, None),
        failure_mode="clean_429", enabled=True,
        note="Vuelazo's cached scout. Unmetered by the provider; the "
             "separate pool exists for per-project attribution."),
    # -- Vuelazo service rails (M0). env_var=None on purpose: their keys
    #    (ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, RESEND_API_KEY) are infra
    #    secrets per CLAUDE.md #6 — env / Actions secrets only, never the
    #    /ops key manager. Providers publish no quota counters, so the
    #    monthly pools below are SELF-imposed budgets: run_deals seeds a
    #    baseline anchor equal to period_limit and the ledger meters our
    #    own spend against it (predicted = upper bound still holds).
    SourceSpec(
        "anthropic", family=FAMILY_SERVICE, roles=(),
        env_var=None,
        metered={"draft": 1},
        pool=("monthly", 200, None, 10, 20, None),
        failure_mode="clean_429", enabled=True,
        note="Deal drafting via the Anthropic API (templates/deal_draft_es.md)."
             " 200 drafts/mo self-cap ~= EUR 4/mo ceiling at launch volume."),
    SourceSpec(
        "telegram", family=FAMILY_SERVICE, roles=(),
        env_var=None,
        metered={"send_message": 1, "create_invite_link": 1,
                 "remove_member": 2},  # ban + unban = 2 HTTP requests
        pool=("rate_only", None, None, 0, None, None),
        failure_mode="clean_429", enabled=True,
        note="Telegram Bot API — free; per-run bound comes from the "
             "reservation, not a monthly pool. Units follow METERED's "
             "worst-case-HTTP-requests convention."),
    SourceSpec(
        "resend", family=FAMILY_SERVICE, roles=(),
        env_var=None,
        metered={"send_email": 1},
        # per_search_cap None: the member/subscriber fan-out grows with
        # the audience — a fixed per-search cap would silently halt the
        # WHOLE pipeline (all-or-nothing reserve) at ~20 members. The
        # monthly pool (3000) + margin (100 = one worst free-tier day)
        # remain the real bounds.
        pool=("monthly", 3000, None, 100, None, None),
        failure_mode="clean_429", enabled=True,
        note="Resend PLAIN email API only (never the contacts-priced "
             "Marketing track, D4). Free tier 3000/mo + 100/day."),
)

_BY_ID = {s.id: s for s in REGISTRY}

# --- derivations consumed by lib/quota.py (golden-tested identical) ---
POOL_SEEDS: tuple[tuple, ...] = tuple(
    (s.id, *s.pool) for s in REGISTRY if s.pool is not None)
METERED: dict[str, dict[str, int]] = {
    s.id: dict(s.metered) for s in REGISTRY if s.metered}


def spec(source: str) -> SourceSpec | None:
    return _BY_ID.get(source)


def backend_of(source: str) -> str:
    """The adapter a source is built from. Distinct source ids may share
    one adapter (serpapi_vz -> serpapi) to get their own quota pool."""
    s = _BY_ID.get(source)
    return (s.backend or s.id) if s else source


def resolve_env_var(source: str, environ) -> tuple[str | None, bool]:
    """(env_var_to_use, is_shared_fallback) for a source.

    Returns the source's own key var when it holds a value; otherwise the
    declared fallback (the other project's key) with True, so the caller
    can warn that the provider allowance is now shared. (None, False)
    means keyless or no key available anywhere."""
    s = _BY_ID.get(source)
    if s is None or s.env_var is None:
        return None, False
    if (environ.get(s.env_var) or "").strip():
        return s.env_var, False
    if s.env_var_fallback and (environ.get(s.env_var_fallback) or "").strip():
        return s.env_var_fallback, True
    return s.env_var, False   # missing: build anyway so the error names it


def shares_key_with_other_project(source: str, environ) -> bool:
    """True when `source` is falling back to another project's key — the
    condition under which its pool must stay a self-imposed slice rather
    than be anchored from the provider's account-wide counter."""
    return resolve_env_var(source, environ)[1]


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
