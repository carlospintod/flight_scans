"""One-shot probe of the two new data sources.

Run from the repo root once you've added TRAVELPAYOUTS_TOKEN and/or
have an unexhausted RAPIDAPI_KEY:

    python probe_new_sources.py

Burns:
- Aviasales: 2-3 calls (one each of cheap_prices / latest_prices /
  prices_for_dates) — Travelpayouts free tier is generous so this is
  effectively free in budget terms.
- Kiwi: 1 call (round-trip search). Costs against your RapidAPI quota
  (separate counter from Sky Scrapper).

Writes raw responses to `response_<name>.json` (gitignored) so we can
inspect the actual shapes and tighten the adapters' defensive parsing
where needed.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def dump(name: str, payload: dict | object) -> None:
    out = Path(f"response_{name}.json")
    if not isinstance(payload, dict):
        out.write_text(repr(payload), encoding="utf-8")
        return
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  wrote {out}  ({out.stat().st_size:,} bytes)")


def probe_aviasales() -> int:
    if not os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip():
        print("AVIASALES: TRAVELPAYOUTS_TOKEN not set — skipping.")
        return 0
    print("AVIASALES probes:")
    from lib.aviasales_api import AviasalesClient, AviasalesError
    client = AviasalesClient.from_env()
    spent = 0

    print("  GET /v1/prices/cheap MAD->NBO")
    try:
        r = client.cheap_prices(origin="MAD", destination="NBO")
        spent += 1
        print(f"    quotes parsed: {len(r.quotes)}")
        dump("aviasales_cheap", r.raw)
    except AviasalesError as exc:
        print(f"    failed: {exc}")
        spent += 1

    print("  GET /v2/prices/latest MAD->NBO month=2026-10")
    try:
        r = client.latest_prices(
            origin="MAD", destination="NBO",
            depart_date_month=date(2026, 10, 1), limit=30,
        )
        spent += 1
        print(f"    quotes parsed: {len(r.quotes)}")
        # Spotlight any Saudia (SV) presence — that's the gap we
        # specifically added Aviasales to fill.
        sv = [q for q in r.quotes if (q.airline or "").upper() == "SV"]
        if sv:
            print(f"    SAUDIA (SV) present: {len(sv)} quote(s)!")
            for q in sv[:3]:
                print(f"      {q.departure_date}..{q.return_date}  "
                      f"{q.price} {q.currency}  airline={q.airline}")
        dump("aviasales_latest_oct2026", r.raw)
    except AviasalesError as exc:
        print(f"    failed: {exc}")
        spent += 1

    print("  GET /v3/prices_for_dates MAD->NBO 2026-10-15..2026-12-10")
    try:
        r = client.prices_for_dates(
            origin="MAD", destination="NBO",
            depart_date=date(2026, 10, 15),
            return_date=date(2026, 12, 10),
        )
        spent += 1
        print(f"    quotes parsed: {len(r.quotes)}")
        dump("aviasales_pricesfordates", r.raw)
    except AviasalesError as exc:
        print(f"    failed: {exc}")
        spent += 1

    print(f"  aviasales: spent {spent} calls.\n")
    return spent


def probe_kiwi() -> int:
    if not os.environ.get("RAPIDAPI_KEY", "").strip():
        print("KIWI: RAPIDAPI_KEY not set — skipping.")
        return 0
    print("KIWI probes:")
    from lib.kiwi_rapidapi import KiwiClient, KiwiError
    client = KiwiClient.from_env()
    spent = 0

    print("  GET /round-trip MAD->NBO 2026-10-15..2026-12-10 limit=5")
    try:
        r = client.round_trip_search(
            origin="MAD", destination="NBO",
            depart_date=date(2026, 10, 15),
            return_date=date(2026, 12, 10),
            currency="EUR", limit=5,
        )
        spent += 1
        print(f"    options parsed: {len(r.options)}")
        vi = [o for o in r.options if o.is_virtual_interlining]
        if vi:
            print(f"    VIRTUAL INTERLINING present: {len(vi)} options!")
            for o in vi[:3]:
                print(f"      {o.carriers}  "
                      f"{o.price} {o.currency}  stops={o.stops}")
        dump("kiwi_round_trip", r.raw)
        print(f"    rate-limit headers: {client.latest_quota}")
    except KiwiError as exc:
        print(f"    failed: {exc}")
        if client.latest_quota:
            print(f"    (but rate-limit headers captured: {client.latest_quota})")
        spent += 1

    print(f"  kiwi: spent {spent} calls.\n")
    return spent


def main() -> int:
    print("Probing Aviasales + Kiwi.\n")
    av_spent = probe_aviasales()
    kw_spent = probe_kiwi()
    print(f"DONE. Total calls: Aviasales={av_spent}, Kiwi={kw_spent}.")
    print("Raw responses written to response_*.json (gitignored).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
