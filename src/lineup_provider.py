from __future__ import annotations

from datetime import UTC
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .config import Config
from .models import Match, PipelineResult


def run_lineup_pipeline(config: Config, match: Match) -> PipelineResult:
    errors = []
    sources = []
    if config.lineup_api_key:
        sources.append("API-Football")
    if config.sportmonks_api_token:
        sources.append("Sportmonks")
    source_label = ", ".join(sources) or "API-Football, Sportmonks"
    if not config.lineup_api_key and not config.sportmonks_api_token:
        return PipelineResult(
            False,
            data=None,
            error="No lineup provider key is configured",
            source=source_label,
        )

    if config.lineup_api_key:
        try:
            lineup = _fetch_api_football_lineups(config, match)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            lineup = None
            errors.append(f"API-Football: {exc}")
        if lineup:
            return PipelineResult(True, data=lineup, source="API-Football")
        errors.append("API-Football: Probable lineup unavailable")

    if config.sportmonks_api_token:
        try:
            lineup = _fetch_sportmonks_lineups(config, match)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            lineup = None
            errors.append(f"Sportmonks: {exc}")
        if lineup:
            return PipelineResult(True, data=lineup, source="Sportmonks")
        errors.append("Sportmonks: Probable lineup unavailable")

    return PipelineResult(False, data=None, error="; ".join(errors), source=source_label)


def _fetch_api_football_lineups(config: Config, match: Match) -> dict[str, list[str]] | None:
    fixture_id = match.id if match.id.isdigit() else resolve_api_football_fixture_id(config, match)
    if not fixture_id:
        return None
    params = urlencode({"fixture": fixture_id})
    request = urlopen_with_headers(
        f"https://v3.football.api-sports.io/fixtures/lineups?{params}",
        {"x-apisports-key": config.lineup_api_key or ""},
    )
    payload = json.loads(request.decode("utf-8"))
    teams: dict[str, list[str]] = {}
    for item in payload.get("response", []):
        team_name = item.get("team", {}).get("name")
        players = [
            player.get("player", {}).get("name")
            for player in item.get("startXI", [])
            if player.get("player", {}).get("name")
        ]
        if team_name and players:
            teams[str(team_name)] = [str(player) for player in players]
    return teams or None


def _fetch_sportmonks_lineups(config: Config, match: Match) -> dict[str, list[str]] | None:
    official = _fetch_sportmonks_lineups_with_include(config, match, "participants;lineups.player")
    if official:
        return official
    try:
        return _fetch_sportmonks_lineups_with_include(config, match, "participants;expectedLineups.player")
    except HTTPError as exc:
        if exc.code == 403:
            return None
        raise


def _fetch_sportmonks_lineups_with_include(config: Config, match: Match, include: str) -> dict[str, list[str]] | None:
    date = match.kickoff_time_utc.astimezone(UTC).date().isoformat()
    params = urlencode(
        {
            "api_token": config.sportmonks_api_token or "",
            "include": include,
            "filters": f"participantSearch:{match.home_team}",
        }
    )
    payload = json.loads(
        urlopen_with_headers(
            f"https://api.sportmonks.com/v3/football/fixtures/date/{date}?{params}",
            {},
        ).decode("utf-8")
    )
    for item in payload.get("data", []):
        name = _normalize_team(str(item.get("name") or ""))
        if _normalize_team(match.home_team) not in name or _normalize_team(match.away_team) not in name:
            continue
        parsed = _parse_sportmonks_lineups(item)
        if parsed:
            return parsed
    return None


def _parse_sportmonks_lineups(item: dict) -> dict[str, list[str]] | None:
    raw_lineups = item.get("lineups") or item.get("expectedLineups") or []
    if isinstance(raw_lineups, dict):
        raw_lineups = raw_lineups.get("data", [])
    if not isinstance(raw_lineups, list):
        return None

    participant_names = _sportmonks_participant_names(item)
    teams: dict[str, list[str]] = {}
    for lineup in raw_lineups:
        if not isinstance(lineup, dict) or not _is_starting_lineup_entry(lineup):
            continue
        team_name = _sportmonks_team_name(lineup, participant_names)
        player_name = _sportmonks_player_name(lineup)
        if team_name and player_name:
            teams.setdefault(team_name, []).append(player_name)
    return {team: players for team, players in teams.items() if players} or None


def _sportmonks_participant_names(item: dict) -> dict[str, str]:
    participants = item.get("participants") or []
    if isinstance(participants, dict):
        participants = participants.get("data", [])
    names = {}
    for participant in participants:
        participant_id = participant.get("id")
        name = participant.get("name")
        if participant_id and name:
            names[str(participant_id)] = str(name)
    return names


def _is_starting_lineup_entry(lineup: dict) -> bool:
    type_name = str(lineup.get("type", {}).get("name") or lineup.get("type") or "").lower()
    type_id = str(lineup.get("type_id") or "").lower()
    position = lineup.get("position_id") or lineup.get("formation_position")
    return bool(position) or "lineup" in type_name or type_id in {"11", "starting"}


def _sportmonks_team_name(lineup: dict, participant_names: dict[str, str]) -> str | None:
    participant_id = lineup.get("participant_id") or lineup.get("team_id")
    if participant_id and str(participant_id) in participant_names:
        return participant_names[str(participant_id)]
    team = lineup.get("team")
    if isinstance(team, dict) and team.get("name"):
        return str(team["name"])
    return None


def _sportmonks_player_name(lineup: dict) -> str | None:
    player = lineup.get("player")
    if isinstance(player, dict):
        name = player.get("display_name") or player.get("name") or player.get("common_name")
        if name:
            return str(name)
    for key in ("player_name", "name", "display_name"):
        if lineup.get(key):
            return str(lineup[key])
    return None


def resolve_api_football_fixture_id(config: Config, match: Match) -> str | None:
    date = match.kickoff_time_utc.astimezone(UTC).date().isoformat()
    params = urlencode({"date": date})
    request = urlopen_with_headers(
        f"https://v3.football.api-sports.io/fixtures?{params}",
        {"x-apisports-key": config.lineup_api_key or ""},
    )
    payload = json.loads(request.decode("utf-8"))
    home = _normalize_team(match.home_team)
    away = _normalize_team(match.away_team)
    for item in payload.get("response", []):
        teams = item.get("teams", {})
        api_home = _normalize_team(teams.get("home", {}).get("name", ""))
        api_away = _normalize_team(teams.get("away", {}).get("name", ""))
        if home in api_home and away in api_away:
            fixture_id = item.get("fixture", {}).get("id")
            return str(fixture_id) if fixture_id else None
    return None


def _normalize_team(value: str) -> str:
    aliases = {
        "usa": "united states",
        "korea republic": "south korea",
        "czechia": "czech republic",
        "bosnia and herzegovina": "bosnia & herzegovina",
    }
    normalized = " ".join(value.lower().replace("-", " ").split())
    return aliases.get(normalized, normalized)


def urlopen_with_headers(url: str, headers: dict[str, str], timeout: int = 20) -> bytes:
    from urllib.request import Request

    request_headers = {"User-Agent": "BettingMatchNotifier/1.0", "Accept": "application/json", **headers}
    request = Request(url, headers=request_headers)
    with urlopen(request, timeout=timeout) as response:
        return response.read()
