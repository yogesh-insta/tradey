"""Fire-and-forget Telegram notifications. Never raises into trading code."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_ERR_LOG = _LOG_DIR / "notify_errors.log"


def notify_enabled() -> bool:
    flag = os.getenv("NOTIFY_ENABLED", "true").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return bool(token and chat_id)


def notify(title: str, body: str, priority: str = "default") -> None:
    """Send a Telegram message. Swallows all errors."""
    if not notify_enabled():
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    text = f"*{_escape_md(title)}*\n{_escape_md(body)}"
    if priority == "high":
        text = f"⚠ {text}"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code >= 400:
                _log_err(f"telegram HTTP {resp.status_code}: {resp.text[:500]}")
    except Exception as exc:  # noqa: BLE001 — never break callers
        _log_err(f"telegram notify failed: {exc}")


def _escape_md(value: str) -> str:
    # Minimal escaping for Telegram legacy Markdown
    for ch in ("_", "*", "`", "["):
        value = value.replace(ch, f"\\{ch}")
    return value


def _log_err(msg: str) -> None:
    logger.warning(msg)
    try:
        with _ERR_LOG.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass
