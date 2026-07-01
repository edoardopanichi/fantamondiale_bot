from __future__ import annotations

import unicodedata

from .models import PipelineResult, RankedOutcome, TeamLineup
from .probability import average, implied_probability


def rank_goalscorers(
    outcomes: list[dict],
    limit: int = 4,
    lineup_result: PipelineResult | None = None,
) -> list[RankedOutcome]:
    lineup_confidence = _lineup_confidence(lineup_result)
    grouped: dict[str, list[tuple[float, str]]] = {}
    for outcome in outcomes:
        side = str(outcome.get("name") or "").strip().lower()
        if side in {"no", "under"}:
            continue
        name = str(outcome.get("description") or outcome.get("name") or "").strip()
        if not name or name.lower() in {"yes", "over"}:
            continue
        if not name:
            continue
        price = float(outcome["price"])
        source = str(outcome.get("source", "Unknown"))
        score = implied_probability(price) * _appearance_multiplier(name, lineup_confidence)
        grouped.setdefault(name, []).append((score, source))

    ranked = [
        RankedOutcome(
            name=name,
            probability=average([prob for prob, _ in values]),
            sources=tuple(sorted({source for _, source in values})),
        )
        for name, values in grouped.items()
    ]
    return sorted(ranked, key=lambda item: item.probability, reverse=True)[:limit]


def _lineup_confidence(lineup_result: PipelineResult | None) -> tuple[set[str], set[str], bool, bool] | None:
    if not lineup_result or not lineup_result.success or not isinstance(lineup_result.data, dict):
        return None

    starters: set[str] = set()
    substitutes: set[str] = set()
    lineups_seen = 0
    for lineup in lineup_result.data.values():
        if not isinstance(lineup, TeamLineup):
            continue
        lineups_seen += 1
        for player in lineup.players:
            starters.update(_player_keys(player))
        for player in lineup.substitutes or []:
            substitutes.update(_player_keys(player))

    if not starters and not substitutes:
        return None

    source = str(lineup_result.source or "").lower()
    is_official = source in {"api-football", "sportmonks official lineups"}
    is_complete = lineups_seen >= 2
    return starters, substitutes, is_official, is_complete


def _appearance_multiplier(name: str, lineup_confidence: tuple[set[str], set[str], bool, bool] | None) -> float:
    if not lineup_confidence:
        return 1.0

    starters, substitutes, is_official, is_complete = lineup_confidence
    keys = _player_keys(name)
    if keys & starters:
        return 1.0 if is_official else 0.9
    if keys & substitutes:
        return 0.35 if is_official else 0.55
    if is_official and is_complete:
        return 0.03
    return 0.65


def _player_keys(name: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    parts = [
        part
        for part in "".join(char.lower() if char.isalnum() else " " for char in ascii_name).split()
        if part
    ]
    if not parts:
        return set()
    keys = {" ".join(parts)}
    if len(parts[-1]) > 2:
        keys.add(parts[-1])
    return keys
