from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Match:
    id: str
    home_team: str
    away_team: str
    kickoff_time_utc: datetime
    source: str = "Unknown"


@dataclass(frozen=True)
class PipelineResult:
    success: bool
    data: Any = None
    error: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class TeamLineup:
    players: list[str]
    formation: str | None = None
    substitutes: list[str] | None = None


@dataclass(frozen=True)
class RankedOutcome:
    name: str
    probability: float
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class FantamondialePick:
    name: str
    team: str
    role: str
    bonus: int
    probability: float
    expected_points: float
    sources: tuple[str, ...] = ()
