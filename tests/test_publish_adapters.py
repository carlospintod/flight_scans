"""Telegram + Resend adapters: payloads, defensive parsing, error paths."""

import pytest

from lib.resend_api import ResendClient, ResendError
from lib.telegram_api import TelegramClient, TelegramError, chat_id_from_env


class _Resp:
    def __init__(self, payload, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    def __init__(self, payload, ok=True, status_code=200):
        self.resp = _Resp(payload, ok=ok, status_code=status_code)
        self.seen = {}

    def post(self, url, json=None, headers=None, timeout=None):
        self.seen = {"url": url, "json": json, "headers": headers}
        return self.resp


# -- telegram ---------------------------------------------------------------

def test_telegram_send_message_ok():
    session = _Session({"ok": True, "result": {"message_id": 77}})
    client = TelegramClient(bot_token="TOKEN", session=session)
    sent = client.send_message(chat_id="-1001234", text="hola")
    assert sent.message_id == 77
    assert "botTOKEN/sendMessage" in session.seen["url"]
    body = session.seen["json"]
    assert body["chat_id"] == "-1001234" and body["text"] == "hola"
    assert body["disable_web_page_preview"] is True


def test_telegram_api_level_error_raises():
    session = _Session({"ok": False, "description": "chat not found"},
                       ok=False, status_code=400)
    client = TelegramClient(bot_token="T", session=session)
    with pytest.raises(TelegramError, match="chat not found"):
        client.send_message(chat_id="x", text="hola")


def test_telegram_ok_false_with_http_200_still_raises():
    session = _Session({"ok": False, "description": "bot was blocked"})
    client = TelegramClient(bot_token="T", session=session)
    with pytest.raises(TelegramError, match="blocked"):
        client.send_message(chat_id="x", text="hola")


def test_telegram_env_helpers(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        TelegramClient.from_env()
    monkeypatch.delenv("TELEGRAM_TEST_CHAT_ID", raising=False)
    with pytest.raises(RuntimeError, match="TELEGRAM_TEST_CHAT_ID"):
        chat_id_from_env()


# -- resend -----------------------------------------------------------------

def test_resend_send_email_ok():
    session = _Session({"id": "re_123"})
    client = ResendClient(api_key="K", session=session)
    sent = client.send_email(from_="Vuelazo <x@resend.dev>",
                             to="carlos@example.com", subject="s", text="b")
    assert sent.email_id == "re_123"
    assert session.seen["headers"]["Authorization"] == "Bearer K"
    body = session.seen["json"]
    assert body["to"] == ["carlos@example.com"]
    assert body["from"].startswith("Vuelazo")
    assert "html" not in body  # plain API, plain text (M0)


def test_resend_error_payload_raises():
    session = _Session({"statusCode": 422, "message": "Invalid `from` field"},
                       ok=False, status_code=422)
    client = ResendClient(api_key="K", session=session)
    with pytest.raises(ResendError, match="Invalid"):
        client.send_email(from_="x", to="y", subject="s", text="b")


def test_resend_missing_id_raises():
    session = _Session({})
    client = ResendClient(api_key="K", session=session)
    with pytest.raises(ResendError, match="missing id"):
        client.send_email(from_="x", to="y@z.com", subject="s", text="b")


def test_resend_from_env_raises(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        ResendClient.from_env()
