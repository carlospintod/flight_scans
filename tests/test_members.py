"""Membership sacred paths (M2): CAS transitions, event log, reminder
dedup, per-airport audience, tokens, Telegram gating methods."""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from lib import members_db
from lib.db import connect, ensure_schema
from lib.deals_db import ensure_deals_schema
from lib.telegram_api import TelegramClient


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        ensure_deals_schema(c)
        members_db.ensure_members_schema(c)
        yield c


def _seed_member(conn, email, *, until_days=100, status="active",
                 airports='["VLC","ALC"]', tg=None):
    until = (datetime.now(timezone.utc) + timedelta(days=until_days)
             ).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO members (email, status, member_until, plan, "
        "price_paid, airports, telegram_user_id, created_at) "
        "VALUES (?, ?, ?, 'founding', 2900, ?, ?, '2026-08-08T00:00:00Z')",
        (email, status, until, airports, tg))
    return conn.execute("SELECT id FROM members WHERE email = ?",
                        (email,)).fetchone()[0]


def test_lapse_is_cas_and_logged(conn):
    mid = _seed_member(conn, "a@x.com", until_days=-1)
    assert members_db.members_to_lapse(conn)[0]["id"] == mid
    assert members_db.lapse_member(conn, mid) is True
    assert members_db.lapse_member(conn, mid) is False  # already lapsed
    events = conn.execute(
        "SELECT event FROM member_events WHERE member_id = ?",
        (mid,)).fetchall()
    assert [e["event"] for e in events] == ["lapsed"]


def test_audience_filters_by_airport_and_expiry(conn):
    _seed_member(conn, "vlc@x.com", airports='["VLC"]')
    _seed_member(conn, "mad@x.com", airports='["MAD"]')
    _seed_member(conn, "old@x.com", airports='["VLC"]', until_days=-5)
    _seed_member(conn, "ref@x.com", airports='["VLC"]', status="refunded")
    audience = members_db.active_members_for_origin(conn, "VLC")
    assert [m["email"] for m in audience] == ["vlc@x.com"]
    assert members_db.count_active_members(conn) == 2  # vlc + mad


def test_reminders_due_and_deduped(conn):
    m30 = _seed_member(conn, "t30@x.com", until_days=25)
    _seed_member(conn, "far@x.com", until_days=200)
    due = members_db.reminder_due(conn, days_ahead=30, event="reminded_t30")
    assert [m["id"] for m in due] == [m30]
    members_db.log_event(conn, m30, "reminded_t30")
    assert members_db.reminder_due(conn, days_ahead=30,
                                   event="reminded_t30") == []
    # T-7 is a separate event stream: still due once inside 7 days.
    assert members_db.reminder_due(conn, days_ahead=7,
                                   event="reminded_t7") == []


def test_removal_queue_is_retryable(conn):
    """Lapsed AND refunded members with a bound telegram id stay in the
    removal queue until the removal SUCCEEDS (id NULLed then) — a failed
    Telegram call is retried next run, never fire-and-forgotten."""
    lapsed = _seed_member(conn, "l@x.com", status="lapsed", tg=111)
    refunded = _seed_member(conn, "r@x.com", status="refunded", tg=222)
    _seed_member(conn, "ok@x.com", status="active", tg=333)
    _seed_member(conn, "done@x.com", status="lapsed", tg=None)
    queue = members_db.members_needing_removal(conn)
    assert {m["id"] for m in queue} == {lapsed, refunded}
    conn.execute("UPDATE members SET telegram_user_id = NULL WHERE id = ?",
                 (lapsed,))
    assert {m["id"] for m in members_db.members_needing_removal(conn)} == {
        refunded}


def test_reminder_min_days_floor_keeps_t30_honest(conn):
    """After an outage, a member at T-5 belongs to the T-7 stream ONLY —
    the floored T-30 query must not claim '30 días' for them."""
    near = _seed_member(conn, "near@x.com", until_days=5)
    mid = _seed_member(conn, "mid@x.com", until_days=20)
    t30 = members_db.reminder_due(conn, days_ahead=30, min_days_ahead=7,
                                  event="reminded_t30")
    assert [m["id"] for m in t30] == [mid]
    t7 = members_db.reminder_due(conn, days_ahead=7, event="reminded_t7")
    assert [m["id"] for m in t7] == [near]


def test_mint_token_stores_hash_only(conn):
    mid = _seed_member(conn, "tok@x.com")
    token = members_db.mint_token(conn, mid, purpose="unsub")
    assert len(token) == 48
    row = conn.execute("SELECT * FROM member_tokens").fetchone()
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert row["purpose"] == "unsub" and row["consumed_at"] is None
    assert token not in row["token_hash"]


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.ok = True
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._payload


class _Session:
    """Scripted per-method responses; records every call."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def post(self, url, json=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, json))
        return _Resp(self.script.get(method, {"ok": True, "result": {}}))


def test_telegram_invite_link_single_use():
    session = _Session({"createChatInviteLink": {
        "ok": True, "result": {"invite_link": "https://t.me/+abc"}}})
    client = TelegramClient(bot_token="T", session=session)
    link = client.create_invite_link(chat_id="-100999", member_limit=1)
    assert link == "https://t.me/+abc"
    method, body = session.calls[0]
    assert method == "createChatInviteLink"
    assert body["member_limit"] == 1 and body["chat_id"] == "-100999"
    assert body["expire_date"] > 0


def test_telegram_remove_member_bans_then_unbans():
    session = _Session({})
    client = TelegramClient(bot_token="T", session=session)
    client.remove_member(chat_id="-100999", user_id=42)
    methods = [c[0] for c in session.calls]
    assert methods == ["banChatMember", "unbanChatMember"]
    assert session.calls[1][1]["only_if_banned"] is True
