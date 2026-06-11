from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .config import Config
from .models import PipelineResult


def send_telegram_message(config: Config, message: str) -> PipelineResult:
    if config.dry_run:
        return PipelineResult(True, data={"dry_run": True, "message": message}, source="Telegram")
    if not config.telegram_bot_token:
        return PipelineResult(False, error="Missing TELEGRAM_BOT_TOKEN", source="Telegram")
    if not config.telegram_chat_id:
        return PipelineResult(False, error="Missing TELEGRAM_CHAT_ID", source="Telegram")
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    body = urlencode({"chat_id": config.telegram_chat_id, "text": message, "parse_mode": "HTML"}).encode("utf-8")
    try:
        with urlopen(url, data=body, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return PipelineResult(False, error=str(exc), source="Telegram")
    if not payload.get("ok"):
        return PipelineResult(False, error=str(payload), source="Telegram")
    return PipelineResult(True, data=payload, source="Telegram")
