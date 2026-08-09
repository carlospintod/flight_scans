"""One client-construction path for the CLI, the UI, and CI.

Extracted from ui/_common._make_clients so run_scan.py stops depending on
Streamlit. The UI keeps a thin wrapper that renders the returned warnings
with st.warning; the CLI logs them.
"""

from __future__ import annotations


def make_clients(
    sources: list[str], conn, *, dry_run: bool = False,
    ledger=None, run_id: str | None = None, search_id: str | None = None,
    shadow: bool = True,
) -> tuple[dict[str, object | None], list[str]]:
    """Build API clients per the source list.

    Returns ({source_id: client_or_None}, warnings). A None entry means
    that source is unavailable (missing key, missing browser) — the
    matching human-readable reason is in `warnings`, and callers skip the
    source. In dry_run all entries are None and no warnings are produced.

    `ledger` (lib.quota.QuotaLedger): when given, every client is wrapped
    in a GuardedClient that charges spend_events BEFORE each metered call
    — the single chokepoint that makes the quota ledger unbypassable
    (CLI, batch runner, UI all construct clients here). M1 runs the
    guard in shadow mode (record, never refuse).
    """
    import os

    from .sources import backend_of, resolve_env_var

    out: dict[str, object | None] = {
        "searchapi": None, "skyscanner": None,
        "aviasales": None, "kiwi": None,
        "googleflights": None, "serpapi": None,
    }
    out.update({s: None for s in sources})
    warnings: list[str] = []
    if dry_run:
        return out, warnings

    # The KEY a source authenticates with comes from the registry, not
    # from the adapter's default. That is what lets serpapi_vz (Vuelazo)
    # and serpapi (the NBO tracker) be the same adapter against
    # different accounts — and, when Vuelazo has no key of its own, what
    # makes the borrowing visible instead of silent.
    def _key(source: str) -> dict:
        var, shared = resolve_env_var(source, os.environ)
        if shared:
            warnings.append(
                f"{source}: no dedicated key — using {var} (shared with the "
                f"other project; the ledger caps this source at its own "
                f"pool, but the PROVIDER allowance is one pot)")
        return {"var": var} if var else {}

    def _build_searchapi(kw):
        from .searchapi_io import SearchApiClient
        return SearchApiClient.from_env(**kw)

    def _build_skyscanner(kw):
        from .skyscanner_rapidapi import SkyScrapperClient
        return SkyScrapperClient.from_env(db_conn=conn, **kw)

    def _build_aviasales(kw):
        from .aviasales_api import AviasalesClient
        return AviasalesClient.from_env(**kw)

    def _build_kiwi(kw):
        from .kiwi_rapidapi import KiwiClient
        return KiwiClient.from_env(db_conn=conn, **kw)

    def _build_googleflights(kw):
        from .googleflights_direct import GoogleFlightsClient
        return GoogleFlightsClient.from_env()

    def _build_serpapi(kw):
        from .serpapi_io import SerpApiClient
        return SerpApiClient.from_env(**kw)

    BUILDERS = {
        "searchapi": ("SearchAPI", _build_searchapi),
        "skyscanner": ("Sky Scrapper", _build_skyscanner),
        "aviasales": ("Aviasales", _build_aviasales),
        "kiwi": ("Kiwi", _build_kiwi),
        "googleflights": ("Google Flights (direct)", _build_googleflights),
        "serpapi": ("SerpAPI", _build_serpapi),
    }
    # Construction order follows BUILDERS, not the caller's list, so it
    # stays deterministic however run_batch/run_deals order their sources.
    order = list(BUILDERS)
    for source in sorted(sources,
                         key=lambda s: (order.index(backend_of(s))
                                        if backend_of(s) in order else 99, s)):
        entry = BUILDERS.get(backend_of(source))
        if entry is None:
            warnings.append(f"{source}: no client builder — skipped")
            continue
        label, build = entry
        try:
            out[source] = build(_key(source))
        except RuntimeError as exc:
            warnings.append(f"{label} ({source}) disabled: {exc}")

    if ledger is not None:
        out = guard_clients(out, ledger=ledger, run_id=run_id,
                            search_id=search_id, shadow=shadow)
    return out, warnings


def guard_clients(raw: dict[str, object | None], *, ledger,
                  run_id: str | None, search_id: str | None,
                  shadow: bool = True) -> dict[str, object | None]:
    """Wrap already-constructed clients in GuardedClients for one
    (run, search). The batch runner constructs raw clients ONCE per run
    (browser startup is the expensive part) and re-wraps them per search
    with that search's own budget."""
    from .quota import GuardedClient
    out: dict[str, object | None] = {}
    for src, client in raw.items():
        if client is None:
            out[src] = None
            continue
        inner = getattr(client, "_inner", client)  # never double-wrap
        budget = None
        if not shadow and run_id and search_id:
            # Enforced mode: the hard-stop budget is this search's
            # reservation for the source (primary + contingency).
            budget = ledger.reserved_units(run_id, search_id, src)
        out[src] = GuardedClient(
            inner, ledger=ledger, source=src,
            run_id=run_id, search_id=search_id, shadow=shadow,
            budget_units=budget,
        )
    return out
