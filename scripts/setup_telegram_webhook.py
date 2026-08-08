#!/usr/bin/env python
"""One-time Telegram webhook registration (M2).

Usage:
    python scripts/setup_telegram_webhook.py https://vuelazo.es
    python scripts/setup_telegram_webhook.py --delete

Env: TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET (any long random
string; must equal the Vercel env var of the same name — the webhook
route rejects requests that don't echo it).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=REPO / ".env")


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN not set")
        return 1
    base = f"https://api.telegram.org/bot{token}"

    if "--delete" in sys.argv:
        r = requests.post(f"{base}/deleteWebhook", timeout=30)
        print(r.json())
        return 0

    if len(sys.argv) < 2 or not sys.argv[1].startswith("https://"):
        print("usage: setup_telegram_webhook.py https://<site> | --delete")
        return 1
    if not secret:
        print("TELEGRAM_WEBHOOK_SECRET not set (generate a long random "
              "string; set it here AND in Vercel)")
        return 1
    url = sys.argv[1].rstrip("/") + "/api/telegram/webhook"
    r = requests.post(f"{base}/setWebhook", json={
        "url": url,
        "secret_token": secret,
        "allowed_updates": ["message"],
    }, timeout=30)
    print(r.json())
    r2 = requests.post(f"{base}/getWebhookInfo", timeout=30)
    print(r2.json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
