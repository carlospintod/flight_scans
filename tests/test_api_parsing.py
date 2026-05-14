"""Verify the API parsing layer against the captured fixture responses.

These run without hitting the network — they exercise the same parser the
live client uses, against the same response shape.
"""

import json
from pathlib import Path

from lib.api import _parse_best_flights, _parse_calendar_entries

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_calendar_parser_skips_no_flight_rows():
    data = json.loads((FIXTURES / "calendar_response.json").read_text(encoding="utf-8"))
    entries = _parse_calendar_entries(data)
    # 6 entries in fixture; one has has_no_flights=true and is dropped.
    assert len(entries) == 5
    assert all(not e.has_no_flights for e in entries)
    # The 489 entry is flagged as lowest.
    lowest = [e for e in entries if e.is_lowest_price]
    assert len(lowest) == 1
    assert lowest[0].price == 489
    assert lowest[0].departure_date == "2026-09-05"
    assert lowest[0].return_date == "2026-10-08"


def test_calendar_parser_handles_empty_calendar():
    assert _parse_calendar_entries({"calendar": []}) == []
    assert _parse_calendar_entries({}) == []


def test_point_parser_extracts_three_best_flights():
    data = json.loads((FIXTURES / "flights_response.json").read_text(encoding="utf-8"))
    best = _parse_best_flights(data)
    assert len(best) == 3
    assert best[0].price == 489
    assert best[0].carriers == "Qatar Airways"
    assert best[0].stops == 1
    assert best[0].total_minutes == 985
    assert best[1].carriers == "KLM + Kenya Airways"
    assert best[1].stops == 1
    assert best[2].carriers == "Iberia"
    assert best[2].stops == 0
