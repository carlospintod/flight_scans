"""The route's own 60-day history — the comparator the product rests on.

Numbers below are real, captured live 2026-08-13.
"""

import pytest

from lib.dealconfig import load_deal_config
from lib import pricehistory as ph

CONFIG = load_deal_config()
PCT = CONFIG.history_min_pct_below


def _series(prices, start_ts=1781042400):
    """[[ts, price], ...] one point per day, as Google returns it."""
    return {"price_insights": {
        "price_history": [[start_ts + i * 86400, p]
                          for i, p in enumerate(prices)]}}


# -- parsing -----------------------------------------------------------

def test_parses_a_real_shaped_series():
    # MAD->JFK 6-13 Nov, measured: 61 daily points, 326..466, median 379.
    prices = [466] * 10 + [428] * 10 + [326] * 6 + [379] * 35
    h = ph.parse_history(_series(prices))
    assert h.points == 61
    assert h.low == 326 and h.high == 466
    assert h.median == 379
    # 61 daily points => the window spans exactly 60 days.
    from datetime import date
    assert (date.fromisoformat(h.last_day)
            - date.fromisoformat(h.first_day)).days == 60


def test_too_few_points_is_no_baseline_not_a_guess():
    assert ph.parse_history(_series([400] * (ph.MIN_POINTS - 1))) is None
    assert ph.parse_history(_series([400] * ph.MIN_POINTS)) is not None


def test_missing_or_malformed_blocks_never_crash():
    assert ph.parse_history({}) is None
    assert ph.parse_history({"price_insights": {}}) is None
    assert ph.parse_history({"price_insights": {"price_history": "nope"}}) is None
    junk = {"price_insights": {"price_history": [
        None, [1], ["a", "b"], [1781042400, 0], [1781042400, -5]]}}
    assert ph.parse_history(junk) is None


# -- the verdict -------------------------------------------------------

def _assess(live, prices, route_class="long"):
    return ph.assess(live, _series(prices), route_class=route_class,
                     min_pct_below=PCT)


def test_a_record_low_is_always_a_deal():
    v = _assess(300, [466] * 20 + [379] * 20 + [326] * 21)
    assert v.level == "record" and v.is_deal
    assert "lo más barato" in v.note


def test_a_bottom_decile_price_needs_a_real_gap_to_the_median():
    """MAD->BKK measured: live 545, p10 545, median 589 — bottom decile,
    but only 7% below the median. Sitting at the bottom of a flat
    distribution is not a vuelazo."""
    prices = [604] * 20 + [589] * 30 + [545] * 11
    v = _assess(545, prices)
    assert v.level == "low"
    assert v.is_deal is False
    assert "exigido" in v.note


def test_a_bottom_decile_price_with_a_real_gap_is_a_deal():
    prices = [700] * 30 + [600] * 20 + [430] * 11
    v = _assess(430, prices)
    assert v.level == "low" and v.is_deal
    assert v.pct_below_median >= PCT["long"]


def test_an_ordinary_price_is_rejected_with_the_numbers_in_spanish():
    """BCN->JFK measured: live 428 against 269..473, median 379. Google
    called it 'typical'; so must we."""
    prices = [473] * 20 + [379] * 30 + [269] * 11
    v = _assess(428, prices)
    assert v.level == "typical" and v.is_deal is False
    assert "normal" in v.note and "379" in v.note


def test_no_history_is_unknown_never_a_silent_pass():
    v = ph.assess(300, {}, route_class="long", min_pct_below=PCT)
    assert v.level == "unknown" and v.is_deal is False
    assert v.history is None
    v2 = ph.assess(None, _series([400] * 61), route_class="long",
                   min_pct_below=PCT)
    assert v2.level == "unknown" and v2.is_deal is False


def test_the_threshold_is_per_route_class():
    prices = [700] * 30 + [600] * 20 + [480] * 11   # ~20% below median
    assert _assess(480, prices, "long").is_deal          # long needs 20%
    assert not _assess(480, prices, "intra_eu").is_deal  # intra_eu needs 25%


def test_the_note_always_carries_the_evidence():
    """Every verdict is a sentence a member can check — that IS the
    product's claim ('el precio normal, demostrado')."""
    for live, prices in ((300, [466] * 20 + [326] * 41),
                         (430, [700] * 30 + [600] * 20 + [430] * 11),
                         (428, [473] * 20 + [379] * 30 + [269] * 11)):
        v = _assess(live, prices)
        assert str(live) in v.note or "mediana" in v.note
        assert any(ch.isdigit() for ch in v.note)


# -- what it replaces --------------------------------------------------

def test_history_beats_typical_range_on_the_measured_case():
    """Measured on ONE response, MAD->JFK: typical_price_range [350,550]
    while the itinerary's own 60 days ran 326..466. The typical low sits
    ABOVE the real minimum and its high far above the real maximum — it
    is a route-level aggregate, not this trip's history. The old floor
    (typical_low - live >= 150) demanded <=200, below anything the trip
    had ever cost."""
    prices = [466] * 20 + [379] * 30 + [326] * 11
    h = ph.parse_history(_series(prices))
    typical_low, floor = 350, CONFIG.floors["long"]
    assert typical_low - floor < h.low, (
        "the retired floor demanded a price below the 60-day minimum")
    # The history gate, on the same data, accepts a genuine record low.
    assert _assess(310, prices).is_deal


def test_verify_result_carries_the_history():
    from lib.dealpipe import VerifyResult
    assert "history" in VerifyResult.__dataclass_fields__


def test_run_deals_gates_on_it():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "run_deals.py").read_text(
        encoding="utf-8")
    assert 'refs["history"] = verify.history' in src
    assert "config.history_gate" in src
