from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from .config import Config
from .models import Match

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
FIRST_NOTIFICATION = "first"
LINEUP_NOTIFICATION = "lineup"

STATIC_WORLD_CUP_MATCHES = [
    Match(
        id="fifa-2026-001-mexico-south-africa",
        home_team="Mexico",
        away_team="South Africa",
        kickoff_time_utc=datetime(2026, 6, 11, 19, 0, tzinfo=UTC),
        source="FIFA 2026 published schedule fallback",
    ),
    Match(
        id="fifa-2026-002-korea-republic-czechia",
        home_team="Korea Republic",
        away_team="Czechia",
        kickoff_time_utc=datetime(2026, 6, 11, 22, 0, tzinfo=UTC),
        source="FIFA 2026 published schedule fallback",
    ),
    Match(
        id="fifa-2026-003-canada-bosnia-herzegovina",
        home_team="Canada",
        away_team="Bosnia and Herzegovina",
        kickoff_time_utc=datetime(2026, 6, 12, 19, 0, tzinfo=UTC),
        source="FIFA 2026 published schedule fallback",
    ),
    Match(
        id="fifa-2026-004-usa-paraguay",
        home_team="USA",
        away_team="Paraguay",
        kickoff_time_utc=datetime(2026, 6, 12, 22, 0, tzinfo=UTC),
        source="FIFA 2026 published schedule fallback",
    ),
]


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC)


def _fetch_json(url: str, timeout: int = 20) -> object:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def odds_api_datetime(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_upcoming_matches(config: Config, now: datetime | None = None) -> list[Match]:
    now = now or datetime.now(UTC)
    until = now + timedelta(hours=config.lookahead_hours)
    matches = []
    if config.odds_api_key:
        matches.extend(_get_odds_api_matches(config, now, until))
    if not matches:
        matches.extend(_get_static_matches(now, until))
    if config.match_id:
        all_known = {match.id: match for match in [*matches, *STATIC_WORLD_CUP_MATCHES]}
        return [all_known[config.match_id]] if config.match_id in all_known else []
    return sorted(matches, key=lambda match: match.kickoff_time_utc)


def _get_static_matches(now: datetime, until: datetime) -> list[Match]:
    return [match for match in STATIC_WORLD_CUP_MATCHES if now <= match.kickoff_time_utc <= until]


def _get_odds_api_matches(config: Config, now: datetime, until: datetime) -> list[Match]:
    params = urlencode(
        {
            "apiKey": config.odds_api_key,
            "dateFormat": "iso",
            "commenceTimeFrom": odds_api_datetime(now),
            "commenceTimeTo": odds_api_datetime(until),
        }
    )
    url = f"{ODDS_API_BASE}/sports/{config.odds_sport_key}/events?{params}"
    try:
        payload = _fetch_json(url)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [
        Match(
            id=str(item["id"]),
            home_team=str(item["home_team"]),
            away_team=str(item["away_team"]),
            kickoff_time_utc=_parse_datetime(str(item["commence_time"])),
            source="The Odds API events",
        )
        for item in payload
        if {"id", "home_team", "away_team", "commence_time"} <= set(item)
    ]


def inside_notification_window(match: Match, config: Config, now: datetime | None = None) -> bool:
    return FIRST_NOTIFICATION in due_notification_stages(match, config, now)


def due_notification_stages(match: Match, config: Config, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    stages = []
    if config.first_notifications_enabled and _inside_first_notification_window(match, config, now):
        stages.append(FIRST_NOTIFICATION)
    if config.lineup_notifications_enabled and _inside_lineup_notification_window(match, config, now):
        stages.append(LINEUP_NOTIFICATION)
    return stages


def manual_notification_stage(config: Config) -> str | None:
    if config.first_notifications_enabled:
        return FIRST_NOTIFICATION
    if config.lineup_notifications_enabled:
        return LINEUP_NOTIFICATION
    return None


def _inside_first_notification_window(match: Match, config: Config, now: datetime) -> bool:
    local_tz = ZoneInfo(config.timezone)
    local_kickoff = match.kickoff_time_utc.astimezone(local_tz)
    if _is_overnight_early_morning(local_kickoff.time()):
        target_local = datetime.combine(
            local_kickoff.date() - timedelta(days=1),
            time(21, 0),
            tzinfo=local_tz,
        )
        target_utc = target_local.astimezone(UTC)
        if target_utc <= now < match.kickoff_time_utc:
            return True
    else:
        target_utc = match.kickoff_time_utc - timedelta(hours=config.notification_target_hours)
    return _inside_target_window(target_utc, now, timedelta(minutes=config.notification_window_minutes))


def _inside_lineup_notification_window(match: Match, config: Config, now: datetime) -> bool:
    local_tz = ZoneInfo(config.timezone)
    local_kickoff = match.kickoff_time_utc.astimezone(local_tz)
    if _is_overnight_early_morning(local_kickoff.time()):
        return False
    target_utc = match.kickoff_time_utc - timedelta(minutes=config.lineup_notification_lead_minutes)
    return _inside_target_window(target_utc, now, timedelta(minutes=config.lineup_notification_window_minutes))


def _is_overnight_early_morning(local_kickoff_time: time) -> bool:
    return time(1, 0) <= local_kickoff_time < time(12, 0)


def _inside_target_window(target_utc: datetime, now: datetime, window: timedelta) -> bool:
    return target_utc - window <= now <= target_utc + window
