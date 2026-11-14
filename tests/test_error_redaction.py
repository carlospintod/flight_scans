"""No secret rides inside a network-error message (CLAUDE.md #6).

requests puts the request URL into its exception messages, and several
callers carry a secret in that URL: SerpAPI and Explore send `api_key` as a
query param, Aviasales' /v3 sends `token`, Telegram puts the bot token in
the path, and the ntfy topic (a password) is the path. Until 2026-11-14 a
refused connection carried them into logs, ledger_runs.summary_json and the
"rail caído" push. Offline: the fake session raises the exception requests
itself raises, built from the request the caller really made, without
opening a socket. Each test first checks that requests' own message does
carry the secret, so a pass means the redaction worked.
"""

import logging
import traceback
from datetime import date
from urllib.parse import urlsplit

import pytest
import requests
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import MaxRetryError

from lib import explore_api
from lib.aviasales_api import AviasalesClient, AviasalesError
from lib.credentials import redact_network_error
from lib.serpapi_io import SerpApiClient, SerpApiError
from lib.telegram_api import TelegramClient, TelegramError

FAKE_KEY = "fake-key-6f1c0e2d9b8a7f6e5d4c3b2a1f0e9d8c"
FAKE_TOKEN = "fake0token0b8a7f6e5d4c3b2a1f0e9d8c"
FAKE_BOT_TOKEN = "123456789:fakeBotTokenAAbbCCddEEffGGhhIIjj"  # <bot id>:<secret>
FAKE_TOPIC = "fake-topic-9f8e7d6c5b4a"


class _RefusingSession:
    """get()/post() fail like requests on a refused connection: a
    ConnectionError around urllib3's MaxRetryError, whose message holds the
    request path and query string, secrets included."""

    raised: requests.ConnectionError | None = None
    host: str | None = None

    def get(self, url, **kw):
        return self._refuse("GET", url, **kw)

    def post(self, url, **kw):
        return self._refuse("POST", url, **kw)

    def _refuse(self, method, url, *, timeout=None, **kw):
        prepared = requests.Request(method, url, **kw).prepare()
        parts = urlsplit(prepared.url)
        self.host = parts.hostname
        pool = HTTPSConnectionPool(parts.hostname, parts.port or 443)
        self.raised = requests.ConnectionError(
            MaxRetryError(pool, prepared.path_url,
                          ConnectionRefusedError(111, "Connection refused")),
            request=prepared)
        raise self.raised


def _assert_clean(text: str, session: _RefusingSession, secret: str) -> None:
    # The fake reproduces the leak: requests' own message has the secret.
    assert secret in str(session.raised)
    assert secret not in text
    assert "REDACTED" in text
    # What debugging needs stays: the error type and the host.
    assert "ConnectionError: " in text
    assert f"host='{session.host}'" in text


def _assert_clean_error(err: Exception, session: _RefusingSession,
                        secret: str) -> None:
    _assert_clean(str(err), session, secret)
    # Raised `from None`, so no traceback prints the original message.
    assert secret not in "".join(traceback.format_exception(err))


# --- SerpAPI and Explore: the key is the api_key query param ----------------

SERPAPI_CALLS = {
    "point_query": lambda c: c.point_query(
        origin="MAD", destination="NBO", outbound=date(2027, 1, 15),
        return_=date(2027, 3, 16), currency="EUR"),
    "booking_options": lambda c: c.booking_options(
        booking_token="tok", origin="MAD", destination="NBO",
        outbound=date(2027, 1, 15), return_=date(2027, 3, 16), currency="EUR"),
    "calendar": lambda c: c.calendar(
        origin="MAD", destination="NBO", stay_days=60,
        outbound_start=date(2027, 1, 15), outbound_end=date(2027, 2, 15),
        currency="EUR"),
}


@pytest.mark.parametrize("call", list(SERPAPI_CALLS.values()),
                         ids=list(SERPAPI_CALLS))
def test_serpapi_search_errors_carry_no_key(call):
    session = _RefusingSession()
    with pytest.raises(SerpApiError) as ei:
        call(SerpApiClient(api_key=FAKE_KEY, session=session))
    assert ei.value.status_code == 0
    _assert_clean_error(ei.value, session, FAKE_KEY)
    assert "api_key=REDACTED" in str(ei.value)
    assert "departure_id=MAD" in str(ei.value)  # the rest of the query stays


def test_serpapi_account_probe_error_carries_no_key():
    session = _RefusingSession()
    with pytest.raises(SerpApiError) as ei:
        SerpApiClient(api_key=FAKE_KEY, session=session).check_quota()
    _assert_clean_error(ei.value, session, FAKE_KEY)
    assert "api_key=REDACTED" in str(ei.value)


@pytest.mark.parametrize("provider", sorted(explore_api.PROVIDERS))
def test_explore_errors_carry_no_key_on_either_vendor(provider):
    session = _RefusingSession()
    client = explore_api.ExploreClient(FAKE_KEY, provider=provider)
    client._session = session
    with pytest.raises(explore_api.ExploreError) as ei:
        client.explore(origin="MAD", month="2027-01", area="africa")
    _assert_clean_error(ei.value, session, FAKE_KEY)
    assert "api_key=REDACTED" in str(ei.value)
    assert "departure_id=MAD" in str(ei.value)


# --- the other secrets that ride in a URL ------------------------------------

def test_aviasales_errors_carry_no_token():
    """/v3/prices_for_dates takes the token as a query param."""
    session = _RefusingSession()
    with pytest.raises(AviasalesError) as ei:
        AviasalesClient(token=FAKE_TOKEN, session=session).prices_for_dates(
            origin="MAD", destination="NBO", depart_month="2027-01")
    assert ei.value.status_code == 0
    _assert_clean_error(ei.value, session, FAKE_TOKEN)
    assert "token=REDACTED" in str(ei.value)


def test_telegram_errors_carry_no_bot_token():
    """The Bot API puts the token in the path: /bot<token>/<method>."""
    session = _RefusingSession()
    with pytest.raises(TelegramError) as ei:
        TelegramClient(bot_token=FAKE_BOT_TOKEN, session=session).send_message(
            chat_id="-1001234567890", text="hola")
    assert ei.value.status_code == 0
    _assert_clean_error(ei.value, session, FAKE_BOT_TOKEN)
    assert "/botREDACTED/sendMessage" in str(ei.value)


def test_the_deal_pipeline_push_logs_no_topic(monkeypatch, caplog):
    from lib import pushes

    session = _RefusingSession()
    monkeypatch.setenv("NTFY_TOPIC", FAKE_TOPIC)
    monkeypatch.setattr(pushes.requests, "post", session.post)
    with caplog.at_level(logging.WARNING, logger="lib.pushes"):
        assert pushes.push("Vuelazo: rail caído", "detalle") is False
    _assert_clean(caplog.text, session, FAKE_TOPIC)


def test_the_scan_notifier_prints_no_topic(monkeypatch, capsys):
    import scripts.notify_ntfy as notify

    session = _RefusingSession()
    monkeypatch.setattr(notify.requests, "post", session.post)
    notify._push(FAKE_TOPIC, title="t", body="b", priority="default", tags="")
    _assert_clean(capsys.readouterr().out, session, FAKE_TOPIC)


# --- the helper on its own -------------------------------------------------------

@pytest.mark.parametrize("message", [
    # urllib3's MaxRetryError: the request path and query, then the cause
    "HTTPSConnectionPool(host='serpapi.com', port=443): Max retries exceeded "
    f"with url: /search?engine=google_flights&api_key={FAKE_KEY}&hl=en "
    "(Caused by ConnectionRefusedError(111, 'Connection refused'))",
    # requests' InvalidURL: a quoted full URL, key as the last param
    f"Invalid URL 'https://serpapi.com/account?api_key={FAKE_KEY}': No host",
    # no api_key= in front of it: only the literal replacement catches it
    f'echoed body {{"api_key": "{FAKE_KEY}"}}',
])
def test_the_key_is_cut_from_any_message_shape(message):
    text = redact_network_error(requests.ConnectionError(message), FAKE_KEY)
    assert FAKE_KEY not in text
    assert text.startswith("network error: ConnectionError: ")


def test_any_api_key_value_is_cut_even_when_the_key_is_not_given():
    text = redact_network_error(requests.ConnectionError(
        "url: /search?API_KEY=someone-elses-key&hl=en"))
    assert "someone-elses-key" not in text
    assert "API_KEY=REDACTED&hl=en" in text
