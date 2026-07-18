from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


PLAYER_BONUS_PATH = Path(__file__).resolve().parents[1] / "data" / "fantamondiale_quarti_players.json"

TEAM_ALIASES = {
    "argentina": "Argentina",
    "australia": "Australia",
    "austria": "Austria",
    "belgium": "Belgio",
    "belgio": "Belgio",
    "bosnia and herzegovina": "Bosnia-Erzegovina",
    "bosnia erzegegovina": "Bosnia-Erzegovina",
    "bosnia erzegovina": "Bosnia-Erzegovina",
    "bosnia herzegovina": "Bosnia-Erzegovina",
    "bosnia & herzegovina": "Bosnia-Erzegovina",
    "bosnia-herzegovina": "Bosnia-Erzegovina",
    "brazil": "Brasile",
    "brasile": "Brasile",
    "canada": "Canada",
    "cape verde": "Capo Verde",
    "capo verde": "Capo Verde",
    "colombia": "Colombia",
    "croatia": "Croazia",
    "croazia": "Croazia",
    "cote d ivoire": "Costa d'Avorio",
    "costa d avorio": "Costa d'Avorio",
    "dr congo": "RD Congo",
    "rd congo": "RD Congo",
    "d r congo": "RD Congo",
    "democratic republic of the congo": "RD Congo",
    "ecuador": "Ecuador",
    "egypt": "Egitto",
    "egitto": "Egitto",
    "england": "Inghilterra",
    "france": "Francia",
    "francia": "Francia",
    "germany": "Germania",
    "germania": "Germania",
    "ghana": "Ghana",
    "inghilterra": "Inghilterra",
    "ivory coast": "Costa d'Avorio",
    "japan": "Giappone",
    "giappone": "Giappone",
    "mexico": "Messico",
    "messico": "Messico",
    "morocco": "Marocco",
    "marocco": "Marocco",
    "netherlands": "Olanda",
    "olanda": "Olanda",
    "norway": "Norvegia",
    "norvegia": "Norvegia",
    "paraguay": "Paraguay",
    "portugal": "Portogallo",
    "portogallo": "Portogallo",
    "senegal": "Senegal",
    "south africa": "Sudafrica",
    "sudafrica": "Sudafrica",
    "spain": "Spagna",
    "spagna": "Spagna",
    "sweden": "Svezia",
    "svezia": "Svezia",
    "switzerland": "Svizzera",
    "svizzera": "Svizzera",
    "united states": "Stati Uniti",
    "united states of america": "Stati Uniti",
    "usa": "Stati Uniti",
    "us": "Stati Uniti",
    "stati uniti": "Stati Uniti",
    "algeria": "Algeria",
}


@dataclass(frozen=True)
class PlayerBonus:
    name: str
    team: str
    bonus: int
    role: str


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(
        "".join(char.lower() if char.isalnum() else " " for char in ascii_value).split()
    )


def player_keys(name: str) -> set[str]:
    parts = normalize_text(name).split()
    if not parts:
        return set()
    keys = {" ".join(parts)}
    if len(parts[-1]) > 2:
        keys.add(parts[-1])
    return keys


def canonical_team(team: str) -> str:
    normalized = normalize_text(team)
    return TEAM_ALIASES.get(normalized, team)


@lru_cache(maxsize=1)
def load_player_bonuses() -> tuple[PlayerBonus, ...]:
    payload = json.loads(PLAYER_BONUS_PATH.read_text(encoding="utf-8"))
    return tuple(
        PlayerBonus(
            name=str(item["name"]),
            team=str(item["team"]),
            bonus=int(item["bonus"]),
            role=str(item["role"]),
        )
        for item in payload
    )


def match_player_bonus(name: str, allowed_teams: set[str] | None = None, include_goalkeepers: bool = True) -> PlayerBonus | None:
    keys = player_keys(name)
    if not keys:
        return None
    candidates = [
        player
        for player in load_player_bonuses()
        if (include_goalkeepers or player.role != "P")
        and (allowed_teams is None or player.team in allowed_teams)
        and keys.intersection(player_keys(player.name))
    ]
    if not candidates:
        return None
    exact_name = normalize_text(name)
    exact = [player for player in candidates if normalize_text(player.name) == exact_name]
    if exact:
        return exact[0]
    non_goalkeepers = [player for player in candidates if player.role != "P"]
    return sorted(non_goalkeepers or candidates, key=lambda player: player.bonus, reverse=True)[0]
