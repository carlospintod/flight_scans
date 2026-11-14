"""No provider secret reaches quota_snapshots.raw_json (CLAUDE.md #6).

SerpAPI's GET /account echoes the account's api_key, email and id next to
the plan counters, and run_batch stored that payload verbatim on every run
until 2026-11-14. These tests pin the fix at the adapter (check_quota), at
the run-start writer (run_batch._anchor_from_account), and in the one-off
scrub of the rows stored before it. Offline: fake session, tmp SQLite.
"""

import copy
import json
from pathlib import Path

import pytest

from lib.db import connect, ensure_schema, record_quota
from lib.quota import QuotaLedger
from lib.serpapi_io import ACCOUNT_SAFE_FIELDS, SerpApiClient, safe_account_fields

FAKE_KEY = "fake-serpapi-key-0000000000000000"
FAKE_EMAIL = "owner@example.test"
FAKE_ACCOUNT_ID = "fake-account-id-0001"

# Field names as the live table stored them (2026-11-14); values invented.
ACCOUNT_PAYLOAD = {
    "account_id": FAKE_ACCOUNT_ID,
    "api_key": FAKE_KEY,
    "account_email": FAKE_EMAIL,
    "account_status": "Active",
    "plan_id": "free",
    "plan_name": "Free Plan",
    "plan_monthly_price": 0.0,
    "plan_renewal_date": "2026-11-28",
    "searches_per_month": 250,
    "plan_searches_left": 161,
    "extra_credits": 0,
    "total_searches_left": 161,
    "this_month_usage": 89,
    "this_hour_searches": 0,
    "last_hour_searches": 62,
    "account_rate_limit_per_hour": 250,
}
# Neither the values nor the field names may survive.
FORBIDDEN = (FAKE_KEY, FAKE_EMAIL, FAKE_ACCOUNT_ID,
             "api_key", "account_email", "account_id")


class _Resp:
    ok = True
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _AccountSession:
    """Answers GET /account the way SerpAPI does, with no network."""

    def __init__(self, payload):
        self._payload = payload

    def get(self, url, **kw):
        assert url.endswith("/account"), url
        return _Resp(self._payload)


def _client(payload=ACCOUNT_PAYLOAD) -> SerpApiClient:
    return SerpApiClient(api_key=FAKE_KEY,
                         session=_AccountSession(copy.deepcopy(payload)))


def _assert_clean(text: str) -> None:
    for s in FORBIDDEN:
        assert s not in text, f"{s!r} leaked"


@pytest.fixture()
def conn(tmp_path: Path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        yield c


# --- adapter ---------------------------------------------------------------

def test_check_quota_returns_only_the_plan_counters():
    q = _client().check_quota()
    assert (q["remaining"], q["limit_total"]) == (161, 250)
    assert set(q["raw"]) == set(ACCOUNT_SAFE_FIELDS)
    _assert_clean(json.dumps(q))


def test_a_field_serpapi_adds_later_stays_out():
    """An allowlist, not a denylist: unknown fields are dropped too."""
    q = _client({**ACCOUNT_PAYLOAD, "account_phone": "+34 600 000 000"}).check_quota()
    assert "account_phone" not in q["raw"]


def test_a_non_object_account_answer_is_an_error_not_a_crash():
    from lib.serpapi_io import SerpApiError
    with pytest.raises(SerpApiError):
        _client(["not", "an", "object"]).check_quota()


# --- the writer run_batch uses at run start --------------------------------

def test_run_start_probe_stores_no_key_or_email(conn):
    import run_batch

    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    run_batch._anchor_from_account(conn, ledger, "serpapi", _client())

    rows = conn.execute(
        "SELECT remaining, limit_total, raw_json FROM quota_snapshots "
        "WHERE source = 'serpapi'").fetchall()
    assert len(rows) == 1
    _assert_clean(rows[0]["raw_json"])
    assert json.loads(rows[0]["raw_json"])["plan_searches_left"] == 161
    assert (rows[0]["remaining"], rows[0]["limit_total"]) == (161, 250)
    # The probe still anchors the pool.
    assert ledger.pool_state("serpapi").provider_view == 161


def test_a_failed_probe_writes_nothing(conn):
    import run_batch

    class _Down:
        def check_quota(self):
            raise RuntimeError("HTTP 503: /account unavailable")

    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    run_batch._anchor_from_account(conn, ledger, "serpapi", _Down())
    assert conn.execute("SELECT COUNT(*) FROM quota_snapshots").fetchone()[0] == 0


# --- the one-off scrub of rows stored before the fix -------------------------

def _snapshot(conn, payload, *, source="serpapi"):
    record_quota(conn, source=source, remaining=161, limit_total=250,
                 raw_json=payload if isinstance(payload, str) else json.dumps(payload))


def _rows(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT rowid, checked_at, source, remaining, limit_total, raw_json "
        "FROM quota_snapshots ORDER BY rowid").fetchall()]


def _seed_mixed(conn):
    _snapshot(conn, ACCOUNT_PAYLOAD)                       # stored before the fix
    _snapshot(conn, safe_account_fields(ACCOUNT_PAYLOAD))  # already clean
    _snapshot(conn, "not json")                            # left alone
    _snapshot(conn, {"X-RateLimit-Requests-Remaining": "5"}, source="kiwi")


def test_scrub_dry_run_counts_and_writes_nothing(conn):
    from scripts.scrub_quota_secrets import scrub

    _seed_mixed(conn)
    before = _rows(conn)
    rep = scrub(conn, apply=False)
    assert (rep.scanned, rep.to_rewrite, rep.rewritten, rep.unparseable) == (3, 1, 0, 1)
    assert set(rep.dropped) == set(ACCOUNT_PAYLOAD) - set(ACCOUNT_SAFE_FIELDS)
    assert _rows(conn) == before


def test_scrub_apply_rewrites_only_the_leaking_rows(conn):
    from scripts.scrub_quota_secrets import MARKERS, leftovers, scrub

    _seed_mixed(conn)
    before = _rows(conn)
    rep = scrub(conn, apply=True)
    assert rep.rewritten == rep.to_rewrite == 1
    after = _rows(conn)

    # Row 1: only raw_json changed, cut to exactly the safe subset.
    assert after[0][:5] == before[0][:5]
    _assert_clean(after[0][5])
    assert json.loads(after[0][5]) == safe_account_fields(ACCOUNT_PAYLOAD)
    # The clean serpapi row, the unparseable one and the kiwi row: untouched.
    assert after[1:] == before[1:]
    assert leftovers(conn) == {m: 0 for m in MARKERS}
    # Re-running finds nothing left to do.
    assert scrub(conn, apply=True).to_rewrite == 0
