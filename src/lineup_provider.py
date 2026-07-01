from __future__ import annotations

from datetime import UTC
from html import unescape
import json
from pathlib import Path
import re
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, unquote, urlencode
from urllib.request import urlopen

from .config import Config
from .models import Match, PipelineResult, TeamLineup

GOAL_SITEMAPS = (
    "https://www.goal.com/it/sitemap/google-news.xml",
    "https://www.goal.com/it/sitemap/editorial-where-to-watch.xml",
)
TALKSPORT_SEARCH_URL = "https://talksport.com/wp-json/wp/v2/search"
TALKSPORT_POST_URL = "https://talksport.com/wp-json/wp/v2/posts/{post_id}"
STATIC_LINEUPS_PATH = Path(__file__).resolve().parents[1] / "data" / "static_team_lineups.json"
GOAL_TEAM_URL_ALIASES = {
    "belgium": {"belgio"},
    "brazil": {"brasile"},
    "bosnia & herzegovina": {"bosnia", "bosnia-erzegovina", "bosnia-ed-erzegovina"},
    "cape verde": {"capo-verde"},
    "croatia": {"croazia"},
    "czech republic": {"cechia", "repubblica-ceca"},
    "dr congo": {"rd-congo", "repubblica-democratica-del-congo", "repubblica-democratica-congo"},
    "egypt": {"egitto"},
    "england": {"inghilterra"},
    "france": {"francia"},
    "germany": {"germania"},
    "ivory coast": {"costa-d-avorio", "costa-avorio"},
    "japan": {"giappone"},
    "jordan": {"giordania"},
    "mexico": {"messico"},
    "morocco": {"marocco"},
    "netherlands": {"olanda", "paesi-bassi"},
    "new zealand": {"nuova-zelanda"},
    "norway": {"norvegia"},
    "portugal": {"portogallo"},
    "saudi arabia": {"arabia-saudita"},
    "scotland": {"scozia"},
    "south africa": {"sudafrica", "sud-africa"},
    "south korea": {"corea-del-sud", "corea-sud"},
    "spain": {"spagna"},
    "sweden": {"svezia"},
    "switzerland": {"svizzera"},
    "turkey": {"turchia"},
    "united states": {"usa", "stati-uniti", "nazionale-usa"},
}


def run_lineup_pipeline(config: Config, match: Match) -> PipelineResult:
    errors = []
    sources = []
    if config.lineup_api_key:
        sources.append("API-Football")
    if config.sportmonks_api_token:
        sources.append("Sportmonks")
    sources.extend(["Goal.com predicted lineups", "TalkSport predicted lineups", "Local static team database"])
    source_label = ", ".join(sources)

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
            sportmonks_result = _fetch_sportmonks_lineups(config, match)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            sportmonks_result = None
            errors.append(f"Sportmonks: {exc}")
        if sportmonks_result:
            lineup, source = sportmonks_result
            return PipelineResult(True, data=lineup, source=source)
        errors.append("Sportmonks: Probable lineup unavailable")

    try:
        lineup = _fetch_goal_lineups(match)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
        lineup = None
        errors.append(f"Goal.com: {exc}")
    if lineup:
        return PipelineResult(True, data=lineup, source="Goal.com predicted lineups")
    errors.append("Goal.com: Probable lineup unavailable")

    try:
        lineup = _fetch_talksport_lineups(match)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
        lineup = None
        errors.append(f"TalkSport: {exc}")
    if lineup:
        return PipelineResult(True, data=lineup, source="TalkSport predicted lineups")
    errors.append("TalkSport: Probable lineup unavailable")

    try:
        lineup = _fetch_static_lineups(match)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        lineup = None
        errors.append(f"Local static team database: {exc}")
    if lineup:
        return PipelineResult(True, data=lineup, source="Local static team database")
    errors.append("Local static team database: Probable lineup unavailable")

    if not config.lineup_api_key and not config.sportmonks_api_token:
        errors.insert(0, "API-Football/Sportmonks: No lineup provider key is configured")

    return PipelineResult(False, data=None, error="; ".join(errors), source=source_label)


def _fetch_api_football_lineups(config: Config, match: Match) -> dict[str, TeamLineup] | None:
    fixture_id = match.id if match.id.isdigit() else resolve_api_football_fixture_id(config, match)
    if not fixture_id:
        return None
    params = urlencode({"fixture": fixture_id})
    request = urlopen_with_headers(
        f"https://v3.football.api-sports.io/fixtures/lineups?{params}",
        {"x-apisports-key": config.lineup_api_key or ""},
    )
    payload = json.loads(request.decode("utf-8"))
    teams: dict[str, TeamLineup] = {}
    for item in payload.get("response", []):
        team_name = item.get("team", {}).get("name")
        formation = item.get("formation")
        players = [
            player.get("player", {}).get("name")
            for player in item.get("startXI", [])
            if player.get("player", {}).get("name")
        ]
        substitutes = [
            player.get("player", {}).get("name")
            for player in item.get("substitutes", [])
            if player.get("player", {}).get("name")
        ]
        if team_name and players:
            teams[str(team_name)] = TeamLineup(
                players=[str(player) for player in players],
                formation=str(formation) if formation else None,
                substitutes=[str(player) for player in substitutes],
            )
    return teams or None


def _fetch_sportmonks_lineups(config: Config, match: Match) -> tuple[dict[str, TeamLineup], str] | None:
    official = _fetch_sportmonks_lineups_with_include(config, match, "participants;lineups.player")
    if official:
        return official, "Sportmonks official lineups"
    try:
        expected = _fetch_sportmonks_lineups_with_include(config, match, "participants;expectedLineups.player")
        if expected:
            return expected, "Sportmonks expected lineups"
        return None
    except HTTPError as exc:
        if exc.code == 403:
            return None
        raise


def _fetch_sportmonks_lineups_with_include(config: Config, match: Match, include: str) -> dict[str, TeamLineup] | None:
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


def _parse_sportmonks_lineups(item: dict) -> dict[str, TeamLineup] | None:
    raw_lineups = item.get("lineups") or item.get("expectedLineups") or []
    if isinstance(raw_lineups, dict):
        raw_lineups = raw_lineups.get("data", [])
    if not isinstance(raw_lineups, list):
        return None

    participant_names = _sportmonks_participant_names(item)
    teams: dict[str, list[str]] = {}
    formations: dict[str, str] = {}
    for lineup in raw_lineups:
        if not isinstance(lineup, dict) or not _is_starting_lineup_entry(lineup):
            continue
        team_name = _sportmonks_team_name(lineup, participant_names)
        player_name = _sportmonks_player_name(lineup)
        if team_name and player_name:
            teams.setdefault(team_name, []).append(player_name)
            formation = _sportmonks_formation(lineup)
            if formation:
                formations.setdefault(team_name, formation)
    return {
        team: TeamLineup(players=players, formation=formations.get(team))
        for team, players in teams.items()
        if players
    } or None


def _fetch_goal_lineups(match: Match) -> dict[str, TeamLineup] | None:
    for url in _goal_candidate_urls(match):
        html = urlopen_with_headers(url, {"Accept": "text/html"}).decode("utf-8", errors="replace")
        parsed = _parse_goal_lineups(html, match)
        if parsed:
            return parsed
    return None


def _goal_candidate_urls(match: Match) -> list[str]:
    candidates: dict[str, int] = {}
    for sitemap in GOAL_SITEMAPS:
        payload = urlopen_with_headers(sitemap, {"Accept": "application/xml"}).decode("utf-8", errors="replace")
        for url in re.findall(r"https://www\.goal\.com/it/notizie/[^<]+", payload):
            score = _score_editorial_url(url, match)
            if score > 0:
                candidates[url] = max(candidates.get(url, 0), score)
    return [
        url
        for url, _score in sorted(candidates.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]


def _score_editorial_url(url: str, match: Match) -> int:
    normalized_url = _normalize_url_text(url)
    if not _contains_any(normalized_url, _team_url_terms(match.home_team)):
        return 0
    if not _contains_any(normalized_url, _team_url_terms(match.away_team)):
        return 0
    score = 10
    if "formazioni" in normalized_url:
        score += 20
    if "ultime" in normalized_url:
        score += 5
    if "dove vedere" in normalized_url or "streaming" in normalized_url:
        score += 2
    return score


def _parse_goal_lineups(html: str, match: Match) -> dict[str, TeamLineup] | None:
    section_match = re.search(
        r"<section[^>]+class=\"[^\"]*fco-football-match-lineups[^\"]*\".*?</section>",
        html,
        flags=re.DOTALL,
    )
    section = section_match.group(0) if section_match else html
    formations = _goal_formations(section)
    rows = re.findall(r"<li class=\"fco-paired-player-list__row\">(.*?)</li>", section, flags=re.DOTALL)
    home_players: list[str] = []
    away_players: list[str] = []
    for row in rows:
        names = [
            _clean_text(value)
            for value in re.findall(r"fco-player-row-content__player-name\">([^<]+)</span>", row)
        ]
        if len(names) >= 2:
            home_players.append(names[0])
            away_players.append(names[1])
    if len(home_players) < 8 or len(away_players) < 8:
        return None
    return {
        match.home_team: TeamLineup(players=home_players[:11], formation=formations[0] if formations else None),
        match.away_team: TeamLineup(
            players=away_players[:11],
            formation=formations[1] if len(formations) > 1 else None,
        ),
    }


def _goal_formations(section: str) -> list[str]:
    formations: list[str] = []
    for match in re.finditer(r"fco-pitch-lineup-header__formation[^>]*>([^<]+)</span>", section):
        formation = _clean_text(match.group(1))
        if _looks_like_formation(formation):
            formations.append(formation)
    if len(formations) < 2:
        for match in re.finditer(r"<span>(\d-\d-\d(?:-\d)?)</span>", section):
            formation = _clean_text(match.group(1))
            if formation not in formations:
                formations.append(formation)
            if len(formations) >= 2:
                break
    return formations[:2]


def _fetch_talksport_lineups(match: Match) -> dict[str, TeamLineup] | None:
    query = quote_plus(f"{match.home_team} {match.away_team} predicted lineups")
    search_payload = json.loads(
        urlopen_with_headers(f"{TALKSPORT_SEARCH_URL}?search={query}", {"Accept": "application/json"}).decode("utf-8")
    )
    if not isinstance(search_payload, list):
        return None
    for item in search_payload[:5]:
        post_id = item.get("id") if isinstance(item, dict) else None
        if not post_id:
            continue
        post_payload = json.loads(
            urlopen_with_headers(
                TALKSPORT_POST_URL.format(post_id=post_id),
                {"Accept": "application/json"},
            ).decode("utf-8")
        )
        rendered = post_payload.get("content", {}).get("rendered", "")
        text = _html_to_text(str(rendered))
        parsed = _parse_talksport_lineups(text, match)
        if parsed:
            return parsed
    return None


def _parse_talksport_lineups(text: str, match: Match) -> dict[str, TeamLineup] | None:
    home = _parse_talksport_team_lineup(text, match.home_team)
    away = _parse_talksport_team_lineup(text, match.away_team)
    if home and away:
        return {match.home_team: home, match.away_team: away}
    return None


def _parse_talksport_team_lineup(text: str, team: str) -> TeamLineup | None:
    terms = sorted(_team_text_terms(team), key=len, reverse=True)
    next_team_pattern = r"[A-Z][A-Za-z &.'-]+"
    for term in terms:
        pattern = rf"\b{re.escape(term)}\s*\(([^)]+)\):\s*(.*?)(?=\s+{next_team_pattern}\s*\(\d-\d-\d|\s+[A-Z][A-Za-z &.'-]+ vs |\s+prediction\b|$)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        formation = _clean_text(match.group(1))
        players = _split_lineup_players(match.group(2))
        if _looks_like_formation(formation) and len(players) >= 8:
            return TeamLineup(players=players[:11], formation=formation)
    return None


def _fetch_static_lineups(match: Match) -> dict[str, TeamLineup] | None:
    payload = json.loads(STATIC_LINEUPS_PATH.read_text(encoding="utf-8"))
    home = _lookup_static_lineup(payload, match.home_team)
    away = _lookup_static_lineup(payload, match.away_team)
    if home and away:
        return {match.home_team: home, match.away_team: away}
    return None


def _lookup_static_lineup(payload: dict, team: str) -> TeamLineup | None:
    for key, value in payload.items():
        if _normalize_team(key) != _normalize_team(team):
            continue
        if not isinstance(value, dict):
            return None
        players = value.get("players")
        if not isinstance(players, list) or not players:
            return None
        return TeamLineup(
            players=[str(player) for player in players],
            formation=str(value["formation"]) if value.get("formation") else None,
        )
    return None


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


def _sportmonks_formation(lineup: dict) -> str | None:
    for key in ("formation", "formation_name", "formation_position_name"):
        value = lineup.get(key)
        if value and "-" in str(value):
            return str(value)
    details = lineup.get("details")
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            value = detail.get("value") or detail.get("name")
            if value and "-" in str(value):
                return str(value)
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
        "belgio": "belgium",
        "brasile": "brazil",
        "capo verde": "cape verde",
        "cechia": "czech republic",
        "corea del sud": "south korea",
        "corea sud": "south korea",
        "croazia": "croatia",
        "egitto": "egypt",
        "francia": "france",
        "germania": "germany",
        "giappone": "japan",
        "giordania": "jordan",
        "inghilterra": "england",
        "marocco": "morocco",
        "messico": "mexico",
        "norvegia": "norway",
        "nuova zelanda": "new zealand",
        "olanda": "netherlands",
        "paesi bassi": "netherlands",
        "portogallo": "portugal",
        "repubblica ceca": "czech republic",
        "repubblica democratica congo": "dr congo",
        "repubblica democratica del congo": "dr congo",
        "scozia": "scotland",
        "spagna": "spain",
        "sudafrica": "south africa",
        "sud africa": "south africa",
        "svezia": "sweden",
        "svizzera": "switzerland",
        "turchia": "turkey",
        "usa": "united states",
        "u s a": "united states",
        "usmnt": "united states",
        "stati uniti": "united states",
        "korea republic": "south korea",
        "czechia": "czech republic",
        "cote d ivoire": "ivory coast",
        "côte d ivoire": "ivory coast",
        "costa d’avorio": "ivory coast",
        "costa d'avorio": "ivory coast",
        "costa d avorio": "ivory coast",
        "curaçao": "curacao",
        "saudi arabia": "saudi arabia",
        "arabia saudita": "saudi arabia",
        "saudi": "saudi arabia",
        "congo dr": "dr congo",
        "democratic republic of congo": "dr congo",
        "rd congo": "dr congo",
        "cape verde islands": "cape verde",
        "bosnia": "bosnia & herzegovina",
        "bosnia ed erzegovina": "bosnia & herzegovina",
        "bosnia and herzegovina": "bosnia & herzegovina",
    }
    normalized = " ".join(
        _strip_diacritics(value)
        .lower()
        .replace("-", " ")
        .replace("&", " & ")
        .replace("'", " ")
        .replace("’", " ")
        .split()
    )
    return aliases.get(normalized, normalized)


def _team_url_terms(team: str) -> set[str]:
    normalized = _normalize_team(team)
    terms = {normalized.replace(" & ", "-"), normalized.replace(" ", "-").replace("&", "and")}
    terms.update(GOAL_TEAM_URL_ALIASES.get(normalized, set()))
    return {term for term in terms if term}


def _team_text_terms(team: str) -> set[str]:
    normalized = _normalize_team(team)
    terms = {team, normalized, normalized.replace("&", "and")}
    if normalized == "united states":
        terms.update({"USA", "United States", "Stati Uniti"})
    if normalized == "bosnia & herzegovina":
        terms.update({"Bosnia", "Bosnia ed Erzegovina", "Bosnia and Herzegovina", "Bosnia & Herzegovina"})
    terms.update(term.replace("-", " ") for term in GOAL_TEAM_URL_ALIASES.get(normalized, set()))
    return {term for term in terms if term}


def _contains_any(value: str, terms: set[str]) -> bool:
    return any(term in value for term in terms)


def _normalize_url_text(value: str) -> str:
    decoded = unquote(unescape(value))
    return _strip_diacritics(decoded).lower().replace("_", "-").replace("%20", "-")


def _strip_diacritics(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )


def _clean_text(value: str) -> str:
    return " ".join(unescape(value).split())


def _html_to_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return _clean_text(text)


def _looks_like_formation(value: str) -> bool:
    return bool(re.fullmatch(r"\d-\d-\d(?:-\d)?", value.strip()))


def _split_lineup_players(value: str) -> list[str]:
    normalized = value.replace("(GK)", "")
    chunks = re.split(r"[;,]", normalized)
    return [_clean_text(chunk) for chunk in chunks if _clean_text(chunk)]


def urlopen_with_headers(url: str, headers: dict[str, str], timeout: int = 20) -> bytes:
    from urllib.request import Request

    request_headers = {"User-Agent": "BettingMatchNotifier/1.0", "Accept": "application/json", **headers}
    request = Request(url, headers=request_headers)
    with urlopen(request, timeout=timeout) as response:
        return response.read()
