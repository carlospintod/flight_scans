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
    out: dict[str, object | None] = {
        "searchapi": None, "skyscanner": None,
        "aviasales": None, "kiwi": None,
        "googleflights": None, "serpapi": None,
    }
    warnings: list[str] = []
    if dry_run:
        return out, warnings

    def _try(source: str, label: str, build) -> None:
        if source not in sources:
            return
        try:
            out[source] = build()
        except RuntimeError as exc:
            warnings.append(f"{label} disabled: {exc}")

    def _build_searchapi():
        from .searchapi_io import SearchApiClient
        return SearchApiClient.from_env()

    def _build_skyscanner():
        from .skyscanner_rapidapi import SkyScrapperClient
        return SkyScrapperClient.from_env(db_conn=conn)

    def _build_aviasales():
        from .aviasales_api import AviasalesClient
        return AviasalesClient.from_env()

    def _build_kiwi():
        from .kiwi_rapidapi import KiwiClient
        return KiwiClient.from_env(db_conn=conn)

    def _build_googleflights():
        from .googleflights_direct import GoogleFlightsClient
        return GoogleFlightsClient.from_env()

    def _build_serpapi():
        from .serpapi_io import SerpApiClient
        return SerpApiClient.from_env()

    _try("searchapi", "SearchAPI", _build_searchapi)
    _try("skyscanner", "Sky Scrapper", _build_skyscanner)
    _try("aviasales", "Aviasales", _build_aviasales)
    _try("kiwi", "Kiwi", _build_kiwi)
    _try("googleflights", "Google Flights (direct)", _build_googleflights)
    _try("serpapi", "SerpAPI", _build_serpapi)

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
