"""Pipeline steps: sweep months, verification tolerance, insights,
baseline honesty, draft fields, confidence semantics."""

from datetime import date
from types import SimpleNamespace

from lib.dealconfig import load_deal_config
from lib.dealgate import Candidate
from lib import dealpipe

CONFIG = load_deal_config()


def _cand(**over):
    base = dict(origin="VLC", dest="MRS", depart_date="2026-09-10",
                return_date="2026-09-14", price=38, currency="EUR",
                route_class="intra_eu", xsection_median=119, xsection_p25=96,
                pct_below=68.1, abs_saving=81, deal_class="standard",
                score=76.3, found_at=None)
    base.update(over)
    return Candidate(**base)


def test_sweep_months_rolls_over_year():
    assert dealpipe.sweep_months(date(2026, 12, 5), 2) == ["2026-12", "2027-01"]
    assert dealpipe.sweep_months(date(2026, 8, 8), 1) == ["2026-08"]


class _FakeSerp:
    def __init__(self, prices, raw=None, boom=None):
        self._prices = prices
        self._raw = raw or {}
        self._boom = boom
        self.seen = None

    def point_query(self, **kw):
        if self._boom:
            raise self._boom
        self.seen = kw
        options = tuple(
            SimpleNamespace(price=p, carriers="Volotea", total_minutes=95,
                            stops=0)
            for p in self._prices)
        return SimpleNamespace(raw=self._raw, best_flights=options)


def test_verify_confirms_within_tolerance():
    serp = _FakeSerp([41, 55])
    v = dealpipe.verify_candidate(serp, _cand(), CONFIG)
    assert v.ok and v.live_price == 41
    assert serp.seen["origin"] == "VLC" and serp.seen["destination"] == "MRS"
    assert serp.seen["outbound"] == date(2026, 9, 10)
    assert serp.seen["return_"] == date(2026, 9, 14)


def test_verify_rejects_above_tolerance():
    v = dealpipe.verify_candidate(_FakeSerp([60]), _cand(), CONFIG)
    assert not v.ok and "tolerance" in v.note


def test_verify_dies_quietly_on_error_and_empty():
    v = dealpipe.verify_candidate(_FakeSerp([], boom=RuntimeError("429")),
                                  _cand(), CONFIG)
    assert not v.ok and "serpapi error" in v.note
    v2 = dealpipe.verify_candidate(_FakeSerp([]), _cand(), CONFIG)
    assert not v2.ok and v2.note == "no live options"


def test_price_insights_defensive_parsing():
    raw = {"price_insights": {"lowest_price": 38, "price_level": "low",
                              "typical_price_range": [90, 160]}}
    pi = dealpipe.parse_price_insights(raw)
    assert pi == {"typical_low": 90, "typical_high": 160,
                  "lowest_price": 38, "price_level": "low"}
    assert dealpipe.parse_price_insights({}) is None
    assert dealpipe.parse_price_insights(
        {"price_insights": {"typical_price_range": ["a", "b"]}}) is None


def test_baseline_prefers_google_insights_and_names_source():
    median, line = dealpipe.baseline_context(
        _cand(), {"typical_low": 90, "typical_high": 160})
    assert median == 125
    assert "Google Flights" in line and "90-160" in line


def test_baseline_falls_back_to_crosssection_and_names_source():
    median, line = dealpipe.baseline_context(_cand(), None)
    assert median == 119
    assert "mediana" in line and "119" in line


def test_draft_fields_complete_and_honest():
    from lib.drafting import REQUIRED_FIELDS
    v = dealpipe.VerifyResult(True, 41, "Volotea", None, "live-confirmed")
    fields = dealpipe.draft_fields(_cand(), v, "linea de contexto")
    assert set(REQUIRED_FIELDS) <= set(fields)
    assert fields["price"] == 41              # the LIVE price publishes
    assert "google.com/travel/flights" in fields["booking_url"]
    assert "41" in fields["verification_line"]


def test_classify_deal_mistake_only_vs_route_typical():
    ok = dealpipe.VerifyResult(True, 41, "V7", {"typical_low": 90,
                                                "typical_high": 160},
                               "live-confirmed")
    assert dealpipe.classify_deal(_cand(), ok, CONFIG) == "mistake"  # 41 < 45
    ok2 = dealpipe.VerifyResult(True, 41, "V7", {"typical_low": 70,
                                                 "typical_high": 140},
                                "live-confirmed")
    assert dealpipe.classify_deal(_cand(), ok2, CONFIG) == "standard"  # 41 >= 35
    no_insights = dealpipe.VerifyResult(True, 41, "V7", None, "ok")
    assert dealpipe.classify_deal(_cand(), no_insights, CONFIG) == "standard"


def test_confidence_families_not_endpoints():
    c = dealpipe.deal_confidence(cached_produced=True, live_verified=True)
    assert c.level == "medium" and c.live_verification
    assert set(c.families) == {"cached", "google"}
    c2 = dealpipe.deal_confidence(cached_produced=True, live_verified=False)
    assert c2.level == "low" and not c2.live_verification
