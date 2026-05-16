"""Sky Scrapper probe v4 — the polling test.

Hypothesis: re-call searchFlights with ALL the original parameters AND
the sessionId from the previous incomplete response. Skyscanner's own
mobile API works this way (the sessionId tells the backend "use the
results I'm already preparing" while the rest of the params let the
endpoint route the request correctly).

Burns 2 calls (kickoff + poll).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

HOST = "sky-scrapper.p.rapidapi.com"
BASE = f"https://{HOST}/api/v1/flights"


def must_key() -> str:
    load_dotenv()
    key = (os.environ.get("RAPIDAPI_KEY") or "").strip()
    if not key:
        print("RAPIDAPI_KEY not set in .env", file=sys.stderr)
        sys.exit(1)
    return key


def call(path: str, params: dict, key: str) -> dict:
    url = f"{BASE}/{path}"
    headers = {"x-rapidapi-key": key, "x-rapidapi-host": HOST}
    print(f"GET {path} params_keys={list(params.keys())}")
    r = requests.get(url, headers=headers, params=params, timeout=60)
    print(f"  status={r.status_code} bytes={len(r.content)}")
    try:
        return r.json()
    except ValueError:
        return {"_raw": r.text[:2000], "_status": r.status_code}


def dump(name: str, payload: dict) -> None:
    out = Path(f"response_{name}.json")
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  wrote {out}")


def main() -> int:
    key = must_key()
    base_params = {
        "originSkyId": "MAD",
        "destinationSkyId": "NBO",
        "originEntityId": "95565077",
        "destinationEntityId": "95673395",
        "date": "2026-09-05",
        "returnDate": "2026-11-08",
        "adults": "1",
        "currency": "EUR",
        "countryCode": "ES",
        "market": "es-ES",
    }
    kickoff = call("searchFlights", base_params, key)
    dump("kickoff_v4", kickoff)
    ctx = (kickoff.get("data") or {}).get("context", {}) or {}
    print(f"  kickoff status={ctx.get('status')}")
    sid = ctx.get("sessionId")
    if not sid:
        print("  no sessionId returned")
        return 0

    time.sleep(5)
    polled = call("searchFlights", {**base_params, "sessionId": sid}, key)
    dump("polled_v4", polled)
    p_ctx = (polled.get("data") or {}).get("context", {}) or {}
    p_total = ((polled.get("data") or {}).get("filterStats") or {}).get("total")
    itineraries = (polled.get("data") or {}).get("itineraries") or []
    print(f"  poll status={p_ctx.get('status')} total={p_total} itineraries_len={len(itineraries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
