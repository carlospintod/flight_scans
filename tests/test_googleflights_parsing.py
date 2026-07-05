"""Parser tests for lib/googleflights_direct.py.

Fixture: tests/fixtures/googleflights_dom.html — real aria-labels
captured from a rendered Google Flights page (MAD-NBO 2026-09-15 /
2026-11-15, probed 2026-07-05), embedded in minimal markup.
"""

from pathlib import Path

from lib.googleflights_direct import _parse_aria_labels

FIX = Path(__file__).resolve().parent / "fixtures" / "googleflights_dom.html"


def _html() -> str:
    return FIX.read_text(encoding="utf-8")


def test_parses_real_fixture_with_expected_fields():
    opts = _parse_aria_labels(_html())
    assert len(opts) >= 8  # 22 labels -> ~11 after dedup
    best = opts[0]
    assert best.price == 597
    assert best.carriers == "Etihad"
    assert best.stops == 1
    assert best.total_minutes == 13 * 60 + 40


def test_sorted_ascending_and_deduplicated():
    opts = _parse_aria_labels(_html())
    prices = [o.price for o in opts]
    assert prices == sorted(prices)
    keys = [(o.price, o.carriers, o.total_minutes) for o in opts]
    assert len(keys) == len(set(keys))


def test_multi_carrier_and_nonstop_labels():
    html = (
        '<li aria-label="From 1,234 euros round trip total. Nonstop flight '
        'with Kenya Airways. Leaves X at 10:00 AM and arrives at Y. '
        'Total duration 8 hr 30 min. Select flight"></li>'
        '<li aria-label="From 810 euros round trip total. 2 stops flight '
        'with Iberia and Kenya Airways. Leaves X and arrives Y. '
        'Total duration 22 hr. Select flight"></li>'
    )
    opts = _parse_aria_labels(html)
    assert len(opts) == 2
    assert opts[0].price == 810
    assert opts[0].carriers == "Iberia + Kenya Airways"
    assert opts[0].stops == 2
    assert opts[0].total_minutes == 22 * 60
    assert opts[1].price == 1234
    assert opts[1].stops == 0
    assert opts[1].carriers == "Kenya Airways"


def test_empty_and_junk_html():
    assert _parse_aria_labels("") == []
    assert _parse_aria_labels("<html><body>consent page</body></html>") == []
