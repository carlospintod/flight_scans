"""Resend email adapter (Vuelazo publish fan-out, M0).

PLAIN email API only — never the contacts-priced Marketing track (D4);
the list lives in Turso. M0 sends the approved deal to Carlos. Bulk-mail
obligations (List-Unsubscribe headers, batch sends) arrive with the first
member sends in M2; the suppression check is honored from day one by the
runner (non-negotiable #7).

Env (infra secret — .env / Actions secrets, never /ops):
  RESEND_API_KEY

`send_email` is the metered method (monthly self-pool 3000, margin 100 =
one worst-case free-tier day held in reserve).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

LOG = logging.getLogger(__name__)

BASE_URL = "https://api.resend.com"
DEFAULT_TIMEOUT_S = 30
SOURCE_ID = "resend"


class ResendError(RuntimeError):
    def __init__(self, status_code: int, message: str, *, payload: Any = None):
        super().__init__(f"resend HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload


@dataclass(frozen=True)
class SentEmail:
    email_id: str
    to: str


class ResendClient:
    source_id = SOURCE_ID

    def __init__(self, api_key: str, *,
                 session: requests.Session | None = None,
                 timeout_s: int = DEFAULT_TIMEOUT_S):
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout_s = timeout_s

    @classmethod
    def from_env(cls, var: str = "RESEND_API_KEY") -> "ResendClient":
        key = os.environ.get(var, "").strip()
        if not key:
            raise RuntimeError(
                f"{var} is not set. Create a free Resend account "
                "(resend.com) and put the API key in .env / Actions "
                "secrets (never /ops).")
        return cls(api_key=key)

    def send_email(self, *, from_: str, to: str, subject: str,
                   text: str, headers: dict[str, str] | None = None
                   ) -> SentEmail:
        body: dict[str, Any] = {
            "from": from_,
            "to": [to],
            "subject": subject,
            "text": text,
        }
        if headers:
            body["headers"] = headers
        LOG.info("resend send to=%s subject=%r", to, subject)
        try:
            r = self._session.post(
                f"{BASE_URL}/emails", json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout_s)
        except requests.RequestException as exc:
            raise ResendError(0, f"network error: {exc}") from exc
        try:
            payload = r.json()
        except ValueError:
            payload = None
        if not r.ok:
            msg = ""
            if isinstance(payload, dict):
                msg = str(payload.get("message") or payload.get("error")
                          or payload)
            raise ResendError(r.status_code, msg or r.text[:300],
                              payload=payload)
        email_id = (payload or {}).get("id")
        if not email_id:
            raise ResendError(r.status_code, "response missing id",
                              payload=payload)
        return SentEmail(email_id=str(email_id), to=to)
