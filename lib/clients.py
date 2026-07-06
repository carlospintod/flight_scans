"""One client-construction path for the CLI, the UI, and CI.

Extracted from ui/_common._make_clients so run_scan.py stops depending on
Streamlit. The UI keeps a thin wrapper that renders the returned warnings
with st.warning; the CLI logs them.
"""

from __future__ import annotations


def make_clients(
    sources: list[str], conn, *, dry_run: bool = False
) -> tuple[dict[str, object | None], list[str]]:
    """Build API clients per the source list.

    Returns ({source_id: client_or_None}, warnings). A None entry means
    that source is unavailable (missing key, missing browser) — the
    matching human-readable reason is in `warnings`, and callers skip the
    source. In dry_run all entries are None and no warnings are produced.
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

    _try("searchapi", "SearchAPI", _build_searchapi)
    _try("skyscanner", "Sky Scrapper", _build_skyscanner)
    _try("aviasales", "Aviasales", _build_aviasales)
    _try("kiwi", "Kiwi", _build_kiwi)
    _try("googleflights", "Google Flights (direct)", _build_googleflights)
    return out, warnings
