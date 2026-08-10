"""A configured rail that cannot be built must never fail silently.

Measured 2026-08-10: `explore.enabled: true` in routes/vuelazo.yaml,
SERPAPI_KEY_VZ absent from the GitHub Actions secrets. Every scheduled
run skipped the paid half of discovery and reported status 'ok'. A full
day of runs looked healthy and produced no Explore data at all.
"""

import re
from pathlib import Path

RUN_DEALS = (Path(__file__).resolve().parents[1] / "run_deals.py").read_text(
    encoding="utf-8")


def _fn(name: str) -> str:
    """Crude but dependency-free: the body of a top-level `def name`."""
    m = re.search(rf"\n(\s*)def {name}\(.*?\n(?=\1def |\1@|\nclass |\Z)",
                  RUN_DEALS, re.S)
    assert m, f"{name} not found"
    return m.group(0)


def test_required_rails_are_collected_not_swallowed():
    body = _fn("_try_service")
    assert "required" in body
    assert "dead_rails.append" in body


def test_a_dead_rail_degrades_the_run():
    """'ok' on a run that silently skipped a configured rail is a lie —
    it is what made this invisible for a day."""
    assert re.search(r"if dead_rails:\s*\n\s*status = \"degraded\"", RUN_DEALS)


def test_a_dead_rail_pages_on_scheduled_runs():
    """Same never-silent rule as the pool-short page: a local run prints,
    a scheduled one pushes."""
    idx = RUN_DEALS.index("if dead_rails:")
    block = RUN_DEALS[idx:idx + 900]
    assert 'args.trigger != "local"' in block
    assert "push(" in block
    assert 'priority="high"' in block


def test_explore_is_marked_required():
    """The rail this bug was found on."""
    idx = RUN_DEALS.index('"explore",')
    assert "required=True" in RUN_DEALS[idx:idx + 400]


def test_publish_rails_are_required_only_when_configured_on():
    """Telegram is postponed — its absence is expected, not an outage.
    Resend is in publish_channels, so its absence IS one. The check must
    follow the config, not a hardcoded list."""
    idx = RUN_DEALS.index("resend", RUN_DEALS.index("_try_service("))
    resend_block = RUN_DEALS[RUN_DEALS.index('"resend", ResendClient'):][:300]
    assert 'required="email" in config.publish_channels' in resend_block
    tg_block = RUN_DEALS[RUN_DEALS.index('"telegram", TelegramClient'):][:300]
    assert 'required="tg_private" in config.publish_channels' in tg_block


def test_drafting_is_always_required():
    """No drafting, no publishable deal — there is no config in which a
    missing ANTHROPIC_API_KEY is a normal day."""
    idx = RUN_DEALS.index('"anthropic", lambda:')
    assert "required=True" in RUN_DEALS[idx:idx + 400]
