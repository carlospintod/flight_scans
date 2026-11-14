"""notify_ntfy pushes the health alerts run_batch pre-computes, and stays
quiet on a healthy run. The regression test for "nobody was paged"."""

import json
from pathlib import Path

import scripts.notify_ntfy as notify


def _run(monkeypatch, summary, *, topic="t", job="success"):
    pushed = []
    monkeypatch.setattr(notify, "_push",
                        lambda topic, **kw: pushed.append(kw))
    monkeypatch.setenv("NTFY_TOPIC", topic)
    monkeypatch.setenv("JOB_STATUS", job)
    return pushed


def _write(tmp_path: Path, summary: dict) -> str:
    p = tmp_path / "summary.json"
    p.write_text(json.dumps(summary), encoding="utf-8")
    return str(p)


def test_source_dark_alert_is_pushed(monkeypatch, tmp_path):
    summary = {
        "status": "ok", "alerts_fired": [],
        "health_alerts": [{
            "title": "Source down: kiwi (payment walled)",
            "body": "kiwi: 402. Last OK: never.",
            "priority": "high", "tags": "warning,satellite_antenna"}],
    }
    pushed = _run(monkeypatch, summary)
    monkeypatch.setattr("sys.argv", ["notify", _write(tmp_path, summary)])
    assert notify.main() == 0
    assert len(pushed) == 1
    assert "kiwi" in pushed[0]["title"]
    assert pushed[0]["priority"] == "high"


def test_healthy_run_stays_quiet(monkeypatch, tmp_path):
    summary = {"status": "ok", "alerts_fired": [], "health_alerts": []}
    pushed = _run(monkeypatch, summary)
    monkeypatch.setattr("sys.argv", ["notify", _write(tmp_path, summary)])
    assert notify.main() == 0
    assert pushed == []


def test_price_and_health_both_push(monkeypatch, tmp_path):
    summary = {
        "status": "ok",
        "alerts_fired": [{"type": "new_low", "origin": "MAD",
                          "destination": "NBO", "departure_date": "2026-09-12",
                          "return_date": "", "price": 301, "currency": "EUR"}],
        "health_alerts": [{"title": "No working verification source",
                           "body": "...", "priority": "high",
                           "tags": "rotating_light"}],
    }
    pushed = _run(monkeypatch, summary)
    monkeypatch.setattr("sys.argv", ["notify", _write(tmp_path, summary)])
    assert notify.main() == 0
    assert len(pushed) == 2      # one price, one health


def test_oneway_alert_labelled_oneway(monkeypatch, tmp_path):
    """A one-way fare (empty return_date) must say 'one-way', not render a
    bare 'dep..' that reads like a round-trip (2026-07-15 owner confusion)."""
    summary = {
        "status": "ok",
        "alerts_fired": [{"type": "new_low", "origin": "MAD",
                          "destination": "NBO", "departure_date": "2026-12-16",
                          "return_date": "", "price": 478, "currency": "EUR"}],
        "health_alerts": [],
    }
    pushed = _run(monkeypatch, summary)
    monkeypatch.setattr("sys.argv", ["notify", _write(tmp_path, summary)])
    assert notify.main() == 0
    assert len(pushed) == 1
    assert "one-way 2026-12-16" in pushed[0]["body"]
    assert "2026-12-16.." not in pushed[0]["body"]     # no bare round-trip form
    assert "one-way" in pushed[0]["title"]


def test_roundtrip_alert_keeps_return(monkeypatch, tmp_path):
    summary = {
        "status": "ok",
        "alerts_fired": [{"type": "new_low", "origin": "MAD",
                          "destination": "NBO", "departure_date": "2026-09-20",
                          "return_date": "2026-11-19", "price": 556,
                          "currency": "EUR"}],
        "health_alerts": [],
    }
    pushed = _run(monkeypatch, summary)
    monkeypatch.setattr("sys.argv", ["notify", _write(tmp_path, summary)])
    assert notify.main() == 0
    assert "2026-09-20..2026-11-19" in pushed[0]["body"]
    assert "one-way" not in pushed[0]["title"]


def test_unconfigured_source_detection():
    """A keyed source requested but with no client is UNCONFIGURED;
    a scraper (googleflights) going dark on CI is not (expected)."""
    from run_batch import _unconfigured_sources
    # serpapi keyless while requested -> flagged (the 2026-07-15 bug).
    assert _unconfigured_sources(
        ["googleflights", "serpapi", "aviasales"], ["aviasales"]) == ["serpapi"]
    # googleflights (scraper) unavailable -> NOT flagged (no browser on CI).
    assert _unconfigured_sources(["googleflights", "aviasales"],
                                 ["aviasales"]) == []
    # everything present -> nothing flagged.
    assert _unconfigured_sources(["serpapi", "aviasales"],
                                 ["serpapi", "aviasales"]) == []


def test_unconfigured_source_pushes_once(monkeypatch, tmp_path):
    """The 'Price source unconfigured' alert reaches the phone."""
    summary = {
        "status": "ok", "alerts_fired": [],
        "health_alerts": [{
            "title": "Price source unconfigured",
            "body": "serpapi has no API key (SERPAPI_KEY) — ...",
            "priority": "high", "tags": "warning,key"}],
    }
    pushed = _run(monkeypatch, summary)
    monkeypatch.setattr("sys.argv", ["notify", _write(tmp_path, summary)])
    assert notify.main() == 0
    assert len(pushed) == 1
    assert "unconfigured" in pushed[0]["title"].lower()
    assert pushed[0]["priority"] == "high"


def test_missing_summary_still_pings(monkeypatch, tmp_path):
    pushed = _run(monkeypatch, {}, job="failure")
    monkeypatch.setattr("sys.argv", ["notify", str(tmp_path / "nope.json")])
    assert notify.main() == 0
    assert len(pushed) == 1
    assert "attention" in pushed[0]["title"]
