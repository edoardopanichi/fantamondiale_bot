from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


LOCAL_SECRETS_PATH = Path("config/secrets.local.json")


def _load_local_secrets(path: Path = LOCAL_SECRETS_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in payload.items()
        if isinstance(value, str) and value.strip()
    }


def _get_config_value(name: str, secrets: dict[str, str], default: str | None = None) -> str | None:
    return os.getenv(name) or secrets.get(name) or default


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    odds_api_key: str | None
    lineup_api_key: str | None
    sportmonks_api_token: str | None
    notification_target_hours: float
    notification_window_minutes: int
    first_notifications_enabled: bool
    lineup_notifications_enabled: bool
    lineup_notification_lead_minutes: int
    lineup_notification_window_minutes: int
    lookahead_hours: float
    timezone: str
    dry_run: bool
    match_id: str | None
    send_test_telegram: bool
    manual_override: bool
    save_dry_run: bool
    odds_sport_key: str
    odds_regions: str
    exact_score_markets: tuple[str, ...]
    goalscorer_markets: tuple[str, ...]
    half_time_result_markets: tuple[str, ...]


def load_config(args: object) -> Config:
    secrets = _load_local_secrets()
    lookahead = float(getattr(args, "lookahead_hours", None) or _get_config_value("LOOKAHEAD_HOURS", secrets, "12"))
    dry_run = bool(getattr(args, "dry_run", False)) or _bool(_get_config_value("DRY_RUN", secrets))
    match_id = getattr(args, "match_id", None)
    return Config(
        telegram_bot_token=_get_config_value("TELEGRAM_BOT_TOKEN", secrets),
        telegram_chat_id=_get_config_value("TELEGRAM_CHAT_ID", secrets),
        odds_api_key=_get_config_value("ODDS_API_KEY", secrets),
        lineup_api_key=_get_config_value("LINEUP_API_KEY", secrets),
        sportmonks_api_token=_get_config_value("SPORTMONKS_API_TOKEN", secrets),
        notification_target_hours=float(_get_config_value("NOTIFICATION_TARGET_HOURS", secrets, "3") or "3"),
        notification_window_minutes=int(_get_config_value("NOTIFICATION_WINDOW_MINUTES", secrets, "15") or "15"),
        first_notifications_enabled=_bool(_get_config_value("ENABLE_FIRST_NOTIFICATION", secrets), True),
        lineup_notifications_enabled=_bool(_get_config_value("ENABLE_LINEUP_NOTIFICATION", secrets), True),
        lineup_notification_lead_minutes=int(_get_config_value("LINEUP_NOTIFICATION_LEAD_MINUTES", secrets, "45") or "45"),
        lineup_notification_window_minutes=int(_get_config_value("LINEUP_NOTIFICATION_WINDOW_MINUTES", secrets, "15") or "15"),
        lookahead_hours=lookahead,
        timezone=_get_config_value("TIMEZONE", secrets, "Europe/Amsterdam") or "Europe/Amsterdam",
        dry_run=dry_run,
        match_id=match_id,
        send_test_telegram=bool(getattr(args, "send_test_telegram", False)),
        manual_override=bool(match_id) or bool(getattr(args, "manual_override", False)),
        save_dry_run=bool(getattr(args, "save_dry_run", False)),
        odds_sport_key=_get_config_value("ODDS_SPORT_KEY", secrets, "soccer_fifa_world_cup") or "soccer_fifa_world_cup",
        odds_regions=_get_config_value("ODDS_REGIONS", secrets, "eu,uk") or "eu,uk",
        exact_score_markets=tuple(
            item.strip()
            for item in (_get_config_value("EXACT_SCORE_MARKETS", secrets, "correct_score,exact_score") or "").split(",")
            if item.strip()
        ),
        goalscorer_markets=tuple(
            item.strip()
            for item in (_get_config_value(
                "GOALSCORER_MARKETS",
                secrets,
                "player_goal_scorer_anytime,anytime_goalscorer,goalscorer_anytime",
            ) or "").split(",")
            if item.strip()
        ),
        half_time_result_markets=tuple(
            item.strip()
            for item in (_get_config_value("HALF_TIME_RESULT_MARKETS", secrets, "h2h_3_way_h1,h2h_h1") or "").split(",")
            if item.strip()
        ),
    )
