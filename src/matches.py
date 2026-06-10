from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .config import Config
from .models import Match

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

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
    now = now or datetime.now(UTC)
    target = timedelta(hours=config.notification_target_hours)
    window = timedelta(minutes=config.notification_window_minutes)
    time_until = match.kickoff_time_utc - now
    return target - window <= time_until <= target + window
