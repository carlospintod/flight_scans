"""M0 walking-skeleton chain, offline: sweep -> gate -> verify -> draft
-> publish, with REAL ledger enforcement (shadow=False) and the
used <= reserved invariant asserted at the end.

Fakes stand in for every network client; the GuardedClient wrappers,
reservation CAS, deal rows, send_log and suppression logic are the real
code paths run_deals.py uses.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.clients import guard_clients
from lib.db import connect, ensure_schema
from lib.dealconfig import load_deal_config
from lib.dealgate import gate_candidates
from lib import dealpipe, deals_db
from lib.drafting import AnthropicDraftClient
from lib.planner import CostLine, CostVector
from lib.quota import QuotaExceeded, QuotaLedger
from lib.resend_api import ResendClient
from lib.telegram_api import TelegramClient

FIXTURE = Path(__file__).parent / "fixtures" / "aviasales_anywhere.json"
SEARCH_ID = "vuelazo-deals"


# ---- fakes over the real adapters ----------------------------------------

class _AviaFake:
    """Duck-type of AviasalesClient.anywhere_prices over the fixture."""

    def __init__(self):
        from lib.aviasales_api import _parse_quotes
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self._quotes = tuple(_parse_quotes(payload, "eur",
                                           origin_default="VLC"))
        self.calls = 0

    def anywhere_prices(self, **kw):
        self.calls += 1
        return SimpleNamespace(raw={}, quotes=self._quotes)


class _SerpFake:
    def __init__(self):
        self.calls = 0

    def point_query(self, **kw):
        self.calls += 1
        return SimpleNamespace(
            raw={"price_insights": {"typical_price_range": [70, 140]}},
            best_flights=(SimpleNamespace(price=41, carriers="Volotea",
                                          total_minutes=95, stops=0),))


class _SessionStub:
    def __init__(self, payload):
        self._payload = payload

    def post(self, url, json=None, headers=None, timeout=None):
        return SimpleNamespace(ok=True, status_code=200, text="",
                               json=lambda: self._payload)


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        deals_db.ensure_deals_schema(c)
        yield c


def _anthropic_fake():
    resp = SimpleNamespace(stop_reason="end_turn", content=[
        SimpleNamespace(type="text",
                        text="Valencia a Marsella por 41 EUR ida y vuelta.")])
    inner = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))
    return AnthropicDraftClient(api_key="k", model="m", inner=inner)


def test_full_chain_offline_with_enforced_ledger(conn):
    config = load_deal_config()
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    run_id = ledger.begin_run(trigger="test")
    assert run_id

    # Anchors: serpapi from a fake /account, service pools self-seeded.
    ledger.record_anchor("serpapi", remaining=200, limit_total=250,
                         origin="account_api")
    ledger.record_anchor("anthropic", remaining=200, limit_total=200,
                         origin="seed")
    ledger.record_anchor("resend", remaining=3000, limit_total=3000,
                         origin="seed")

    cost = CostVector(lines=(
        CostLine("aviasales", 2, "primary", "sweep"),
        CostLine("serpapi", 1, "primary", "verify"),
        CostLine("anthropic", 1, "primary", "draft"),
        CostLine("telegram", 1, "primary", "post"),
        CostLine("resend", 1, "primary", "email"),
    ))
    assert ledger.reserve(run_id, SEARCH_ID, cost)

    raw = {
        "aviasales": _AviaFake(),
        "serpapi": _SerpFake(),
        "anthropic": _anthropic_fake(),
        "telegram": TelegramClient(
            bot_token="T", session=_SessionStub(
                {"ok": True, "result": {"message_id": 9}})),
        "resend": ResendClient(
            api_key="K", session=_SessionStub({"id": "re_1"})),
    }
    guarded = guard_clients(raw, ledger=ledger, run_id=run_id,
                            search_id=SEARCH_ID, shadow=False)

    # 1. discover (2 metered sweep calls)
    obs = dealpipe.sweep_origin(guarded["aviasales"], origin="VLC",
                                months=["2026-08", "2026-09"], currency="EUR")
    assert len(obs) == 16  # fixture routes x 2 months
    deals_db.insert_observations(conn, obs)

    # 2. gate
    cands = [c for c in gate_candidates(conn, obs, config)
             if c.rejected_reason is None]
    assert [c.dest for c in cands] == ["MRS"]
    cand = cands[0]

    # 3. verify + 4. draft + 6. publish
    deal_id = deals_db.insert_deal(conn, origin=cand.origin, dest=cand.dest,
                                   price=cand.price, currency="EUR",
                                   score=cand.score)
    verify = dealpipe.verify_candidate(guarded["serpapi"], cand, config)
    assert verify.ok and verify.live_price == 41

    baseline_median, line = dealpipe.baseline_context(cand, verify.insights)
    fields = dealpipe.draft_fields(cand, verify, line)
    result = guarded["anthropic"].draft(fields=fields)
    deals_db.update_deal(conn, deal_id, status="approved",
                         price=verify.live_price,
                         baseline_median=baseline_median,
                         draft_es=result.text,
                         draft_version=result.template_version)

    assert not deals_db.is_suppressed(conn, config.alert_email_to)
    sent = guarded["telegram"].send_message(chat_id="-100", text=result.text)
    deals_db.record_send(conn, channel="tg_private", deal_id=deal_id,
                         provider_ref=str(sent.message_id))
    mail = guarded["resend"].send_email(from_=config.email_from,
                                        to=config.alert_email_to,
                                        subject="s", text=result.text)
    deals_db.record_send(conn, channel="email", deal_id=deal_id,
                         provider_ref=mail.email_id)
    deals_db.update_deal(conn, deal_id, status="published",
                         publish_targets=json.dumps(["tg_private", "email"]))

    # settle: used <= reserved on every line — the M0 DoD invariant.
    ledger.settle(run_id, SEARCH_ID)
    ledger.finalize_run(run_id, "ok")
    rows = conn.execute(
        "SELECT source, reserved_units, used_units FROM run_reservations "
        "WHERE run_id = ?", (run_id,)).fetchall()
    assert len(rows) == 5
    for r in rows:
        assert r["used_units"] <= r["reserved_units"], dict(r)
    used = {r["source"]: r["used_units"] for r in rows}
    assert used == {"aviasales": 2, "serpapi": 1, "anthropic": 1,
                    "telegram": 1, "resend": 1}

    deal = conn.execute("SELECT * FROM deals WHERE id = ?",
                        (deal_id,)).fetchone()
    assert deal["status"] == "published"
    assert deal["price"] == 41
    assert deal["draft_version"] == "deal_draft_es v1"
    sends = conn.execute("SELECT channel FROM send_log").fetchall()
    assert {s["channel"] for s in sends} == {"tg_private", "email"}


def test_guard_refuses_beyond_reservation(conn):
    """The hard-stop: a second draft against a 1-unit reservation raises
    QuotaExceeded BEFORE any HTTP call."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    run_id = ledger.begin_run(trigger="test")
    ledger.record_anchor("anthropic", remaining=200, limit_total=200,
                         origin="seed")
    cost = CostVector(lines=(CostLine("anthropic", 1, "primary", "draft"),))
    assert ledger.reserve(run_id, SEARCH_ID, cost)
    guarded = guard_clients({"anthropic": _anthropic_fake()}, ledger=ledger,
                            run_id=run_id, search_id=SEARCH_ID, shadow=False)
    from tests.test_drafting import FIELDS
    guarded["anthropic"].draft(fields=FIELDS)
    with pytest.raises(QuotaExceeded):
        guarded["anthropic"].draft(fields=FIELDS)


def test_suppression_honored_before_send(conn):
    conn.execute("INSERT INTO suppressions (email, reason, ts) VALUES "
                 "('x@y.com', 'unsub', '2026-08-08T00:00:00Z')")
    assert deals_db.is_suppressed(conn, "x@y.com")
    assert not deals_db.is_suppressed(conn, "other@y.com")


def test_run_deals_module_imports():
    """Syntax/import smoke for the entrypoint (no side effects at import)."""
    import importlib
    mod = importlib.import_module("run_deals")
    assert hasattr(mod, "main")
