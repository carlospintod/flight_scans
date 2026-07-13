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


def test_missing_summary_still_pings(monkeypatch, tmp_path):
    pushed = _run(monkeypatch, {}, job="failure")
    monkeypatch.setattr("sys.argv", ["notify", str(tmp_path / "nope.json")])
    assert notify.main() == 0
    assert len(pushed) == 1
    assert "attention" in pushed[0]["title"]
