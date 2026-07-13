#!/usr/bin/env python3
"""Send a test Telegram message using .env credentials.

Surfaces HTTP errors (e.g. 403 bad token/chat) — trading notify() still swallows them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from packages.notify import notify_enabled


def main() -> int:
    if not notify_enabled():
        print("Telegram not configured.")
        print("1. Create a bot with @BotFather → TELEGRAM_BOT_TOKEN")
        print("2. Get chat id from @userinfobot → TELEGRAM_CHAT_ID")
        print("3. Copy .env.example to .env and fill both values")
        print("4. Set NOTIFY_ENABLED=true")
        return 1

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = "*tradey test*\nTelegram notify is working."
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
    except Exception as exc:  # noqa: BLE001
        print(f"Telegram request failed: {exc}")
        return 1

    if resp.status_code >= 400:
        print(f"Telegram HTTP {resp.status_code}: {resp.text[:500]}")
        print("Check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env (do not commit secrets).")
        return 1

    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if isinstance(body, dict) and body.get("ok") is False:
        print(f"Telegram API error: {resp.text[:500]}")
        return 1

    print("Sent test message — check your phone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
