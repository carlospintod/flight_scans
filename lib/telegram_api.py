"""Telegram Bot API adapter (Vuelazo publish fan-out, M0).

Native Bot API, no third-party gatekeepers (D4). M0 posts the approved
deal to the PRIVATE TEST channel; the member-gating machinery (deep-link
binding, join requests, lapse removal) is M2.

Env (infra secrets — .env / Actions secrets, never /ops):
  TELEGRAM_BOT_TOKEN     from @BotFather
  TELEGRAM_TEST_CHAT_ID  the private test channel id (e.g. -100xxxxxxxxxx)
                         — the bot must be an admin of the channel.

`send_message` is the metered method (rate_only pool; the per-run bound
comes from the reservation).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

LOG = logging.getLogger(__name__)

BASE_URL = "https://api.telegram.org"
DEFAULT_TIMEOUT_S = 30
SOURCE_ID = "telegram"


class TelegramError(RuntimeError):
    def __init__(self, status_code: int, message: str, *, payload: Any = None):
        super().__init__(f"telegram HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload


@dataclass(frozen=True)
class SentMessage:
    message_id: int
    chat_id: int | str


class TelegramClient:
    source_id = SOURCE_ID

    def __init__(self, bot_token: str, *,
                 session: requests.Session | None = None,
                 timeout_s: int = DEFAULT_TIMEOUT_S):
        if not bot_token:
            raise ValueError("bot_token is required")
        self._token = bot_token
        self._session = session or requests.Session()
        self._timeout_s = timeout_s

    @classmethod
    def from_env(cls, var: str = "TELEGRAM_BOT_TOKEN") -> "TelegramClient":
        token = os.environ.get(var, "").strip()
        if not token:
            raise RuntimeError(
                f"{var} is not set. Create a bot with @BotFather and put "
                "the token in .env / Actions secrets (never /ops).")
        return cls(bot_token=token)

    def _call(self, method: str, body: dict) -> dict:
        """POST one Bot API method; returns the `result` object.
        Defensive: Telegram answers ok=false with HTTP 200 too."""
        url = f"{BASE_URL}/bot{self._token}/{method}"
        try:
            r = self._session.post(url, json=body, timeout=self._timeout_s)
        except requests.RequestException as exc:
            raise TelegramError(0, f"network error: {exc}") from exc
        try:
            payload = r.json()
        except ValueError:
            payload = None
        if not r.ok or not isinstance(payload, dict) or not payload.get("ok"):
            desc = ""
            if isinstance(payload, dict):
                desc = str(payload.get("description") or payload)
            raise TelegramError(r.status_code, desc or r.text[:300],
                                payload=payload)
        result = payload.get("result")
        return result if isinstance(result, dict) else {"value": result}

    def send_message(self, *, chat_id: int | str, text: str,
                     disable_preview: bool = True) -> SentMessage:
        """Plain-text sendMessage (no parse_mode: deal drafts are prose;
        Markdown parsing failures would bounce the whole send)."""
        LOG.info("telegram sendMessage chat=%s len=%d", chat_id, len(text))
        result = self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_preview,
        })
        mid = result.get("message_id")
        if not isinstance(mid, int):
            raise TelegramError(200, "response missing result.message_id",
                                payload=result)
        return SentMessage(message_id=mid, chat_id=chat_id)

    # -- membership gating (M2, D4: native Bot API, no gatekeepers) -----

    def create_invite_link(self, *, chat_id: int | str,
                           member_limit: int = 1,
                           expire_seconds: int = 86400) -> str:
        """Single-use invite into the private channel — minted per member
        at bind time, never reused."""
        import time
        result = self._call("createChatInviteLink", {
            "chat_id": chat_id,
            "member_limit": member_limit,
            "expire_date": int(time.time()) + expire_seconds,
        })
        link = result.get("invite_link")
        if not isinstance(link, str) or not link:
            raise TelegramError(200, "response missing invite_link",
                                payload=result)
        return link

    def remove_member(self, *, chat_id: int | str, user_id: int) -> None:
        """Lapse/refund removal: ban then immediately unban, so the user
        leaves the channel but can rejoin via a fresh invite after
        renewing (banChatMember alone would block forever)."""
        self._call("banChatMember", {"chat_id": chat_id, "user_id": user_id})
        self._call("unbanChatMember", {"chat_id": chat_id, "user_id": user_id,
                                       "only_if_banned": True})


def chat_id_from_env(var: str = "TELEGRAM_TEST_CHAT_ID") -> str:
    chat = os.environ.get(var, "").strip()
    if not chat:
        raise RuntimeError(
            f"{var} is not set. Create a private test channel, add the bot "
            "as admin, and put the channel id (-100...) in .env.")
    return chat
