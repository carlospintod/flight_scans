"""Probe: can a local headless Chromium extract Google Flights prices?

Strategy:
  1. Set EU-consent cookies before navigation (avoids the interstitial).
  2. Navigate to the fast-flights-built URL (protobuf tfs param).
  3. Capture the async GetShoppingResults XHR body (the real data).
  4. Also save the rendered DOM and try a simple aria-label price parse
     as a fallback path.

Free — no API quota involved. Writes probe outputs to response_gf_*.txt.
"""

from __future__ import annotations

import json
import re
import sys

from fast_flights import FlightQuery, Passengers, create_query
from playwright.sync_api import sync_playwright

DEP, RET = "2026-09-15", "2026-11-15"


def main() -> int:
    q = create_query(
        flights=[
            FlightQuery(date=DEP, from_airport="MAD", to_airport="NBO"),
            FlightQuery(date=RET, from_airport="NBO", to_airport="MAD"),
        ],
        trip="round-trip", passengers=Passengers(adults=1),
        currency="EUR", language="en-US",
    )
    url = q.url()
    print("URL:", url[:110], "...")

    captured: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
        )
        ctx.add_cookies([
            {"name": "CONSENT", "value": "YES+cb.20240101-01-p0.en+FX+700",
             "domain": ".google.com", "path": "/"},
            {"name": "SOCS",
             "value": "CAESHAgBEhJnd3NfMjAyNDAxMDEtMF9SQzIaAmVuIAEaBgiA_LyaBg",
             "domain": ".google.com", "path": "/"},
        ])
        page = ctx.new_page()

        def on_response(resp):
            if "GetShoppingResults" in resp.url:
                try:
                    captured.append(resp.text())
                    print(f"  captured GetShoppingResults ({len(captured[-1]):,} chars)")
                except Exception as exc:  # noqa: BLE001
                    print("  capture failed:", exc)

        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # Give the async results time to load.
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        html = page.content()
        browser.close()

    print(f"\nrendered DOM: {len(html):,} chars; XHR captures: {len(captured)}")

    with open("response_gf_dom.html", "w", encoding="utf-8") as f:
        f.write(html)
    for i, c in enumerate(captured):
        with open(f"response_gf_xhr_{i}.txt", "w", encoding="utf-8") as f:
            f.write(c)

    # --- Fallback path: aria-label price extraction from rendered DOM ---
    # Google Flights result rows carry aria-labels like
    # "From 532 euros round trip total. ..." — crude but robust to
    # obfuscated class names.
    labels = re.findall(r'aria-label="([^"]*(?:euros?|€)[^"]*)"', html)
    prices = []
    for lab in labels:
        m = re.search(r"From (\d[\d,.]*) euros", lab)
        if m:
            prices.append(m.group(1))
    print(f"DOM aria-label price hits: {len(prices)} -> {prices[:8]}")

    # --- XHR path: look for the flights payload inside batchexecute ---
    for i, c in enumerate(captured):
        # batchexecute bodies start with )]}' and contain nested JSON strings
        try:
            body = c.split("\n", 2)[-1]
            outer = json.loads(body) if body.strip().startswith("[") else None
        except Exception:
            outer = None
        print(f"XHR {i}: parseable outer={outer is not None}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
