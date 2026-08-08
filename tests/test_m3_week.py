"""M3 DoD, offline: one simulated week produces member alerts (send_log),
free picks at T+24h, the Sunday digest, and >=3 deal cards."""

import io
from datetime import datetime, timedelta, timezone

import pytest

from lib import digest as dg
from lib.db import connect, ensure_schema
from lib.dealcard import CardData, render_deal_card
from lib.deals_db import ensure_deals_schema, insert_deal, record_send
from lib.members_db import ensure_members_schema

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)  # a Sunday


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        ensure_deals_schema(c)
        ensure_members_schema(c)
        dg.ensure_digest_schema(c)
        yield c


def _seed_week(conn):
    """Five approved deals across the week, two flagged free picks."""
    monday = datetime(2026, 8, 3, tzinfo=timezone.utc)
    ids = []
    for i, (o, d, price) in enumerate([
        ("VLC", "MRS", 38), ("ALC", "LON", 42), ("MAD", "NYC", 289),
        ("VLC", "ROM", 35), ("BCN", "RAK", 44),
    ]):
        day = monday + timedelta(days=i)
        deal_id = insert_deal(
            conn, origin=o, dest=d, price=price, currency="EUR",
            score=90 - i, status="published",
            draft_es=f"{o} a {d} por {price} EUR — chollo verificado.",
            created_at=day.strftime("%Y-%m-%dT%H:%M:%SZ"))
        conn.execute(
            "UPDATE deals SET approved_at = ?, published_at = ?, "
            "free_pick = ? WHERE id = ?",
            (day.strftime("%Y-%m-%dT%H:%M:%SZ"),
             day.strftime("%Y-%m-%dT%H:%M:%SZ"),
             1 if i < 2 else 0, deal_id))
        record_send(conn, channel="email", deal_id=deal_id,
                    provider_ref=f"re_{i}")   # the member alert
        record_send(conn, channel="tg_private", deal_id=deal_id,
                    provider_ref=str(100 + i))
        ids.append(deal_id)
    return ids


def test_week_produces_member_alerts(conn):
    _seed_week(conn)
    n = conn.execute("SELECT COUNT(*) FROM send_log "
                     "WHERE channel = 'email'").fetchone()[0]
    assert n == 5  # N member alerts


def test_free_picks_due_after_24h_and_deduped(conn):
    ids = _seed_week(conn)
    due = dg.free_picks_due(conn, now=NOW)
    assert [d["id"] for d in due] == ids[:2]  # only the flagged two
    # Posting one records tg_public -> it leaves the due list.
    record_send(conn, channel="tg_public", deal_id=ids[0],
                provider_ref="900")
    due2 = dg.free_picks_due(conn, now=NOW)
    assert [d["id"] for d in due2] == [ids[1]]
    # A deal published < 24h ago is NOT due yet.
    fresh = insert_deal(conn, origin="VLC", dest="OPO", price=40,
                        currency="EUR", status="published",
                        draft_es="x")
    conn.execute(
        "UPDATE deals SET free_pick = 1, published_at = ? WHERE id = ?",
        ((NOW - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"), fresh))
    assert fresh not in [d["id"] for d in dg.free_picks_due(conn, now=NOW)]


def test_sunday_digest_assembles_from_approvals(conn):
    _seed_week(conn)
    digest_id = dg.assemble_digest(conn, now=NOW)
    assert digest_id is not None
    row = conn.execute("SELECT * FROM digests WHERE id = ?",
                       (digest_id,)).fetchone()
    assert row["week_start"] == "2026-08-03"
    assert row["n_deals"] == 5
    for token in ("VLC → MRS", "MAD → NYC", "vuelazo.es/unete"):
        assert token in row["draft_es"]
    # Best score leads (D2 ordering).
    assert row["draft_es"].index("MRS") < row["draft_es"].index("RAK")
    # Idempotent per week.
    assert dg.assemble_digest(conn, now=NOW) == digest_id
    # Approve -> sent lifecycle.
    assert dg.approve_digest(conn, digest_id) is True
    assert dg.approve_digest(conn, digest_id) is False
    dg.mark_digest_sent(conn, digest_id)
    assert conn.execute("SELECT status FROM digests WHERE id = ?",
                        (digest_id,)).fetchone()[0] == "sent"


def test_empty_week_produces_no_digest(conn):
    assert dg.assemble_digest(conn, now=NOW) is None


def test_three_deal_cards_render(conn, tmp_path):
    _seed_week(conn)
    deals = conn.execute("SELECT * FROM deals LIMIT 3").fetchall()
    assert len(deals) == 3
    for d in deals:
        card = CardData(origin=d["origin"], dest=d["dest"],
                        price=d["price"], currency=d["currency"],
                        normal=d["price"] * 2, pct_below=50.0,
                        dates_line="10 → 14 sep", carrier="Volotea")
        img = render_deal_card(card, [90, 84, 95, 88, d["price"]],
                               tmp_path / f"card_{d['id']}.png")
        assert img.size == (1080, 1350)
        raw = (tmp_path / f"card_{d['id']}.png").read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_card_renders_without_history(tmp_path):
    card = CardData(origin="VLC", dest="SVQ", price=30, currency="EUR",
                    normal=None, pct_below=None,
                    dates_line="fechas flexibles", carrier=None)
    img = render_deal_card(card, [], tmp_path / "c.png")
    assert img.size == (1080, 1350)


def test_subscribers_minus_suppressions(conn):
    conn.execute("INSERT INTO subscribers (email, status, unsub_token, "
                 "created_at) VALUES ('a@x.com','active','t1','2026-08-01')")
    conn.execute("INSERT INTO subscribers (email, status, unsub_token, "
                 "created_at) VALUES ('b@x.com','active','t2','2026-08-01')")
    conn.execute("INSERT INTO subscribers (email, status, unsub_token, "
                 "created_at) VALUES ('c@x.com','unsub','t3','2026-08-01')")
    conn.execute("INSERT INTO suppressions (email, reason, ts) "
                 "VALUES ('b@x.com','one_click_unsubscribe','2026-08-02')")
    assert dg.active_subscribers(conn) == ["a@x.com"]
