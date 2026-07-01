from __future__ import annotations

import re

from .fantamondiale import canonical_team, load_player_bonuses, match_player_bonus, player_keys
from .models import FantamondialePick, Match, PipelineResult, RankedOutcome, TeamLineup
from .probability import average, implied_probability


def rank_goalscorers(
    outcomes: list[dict],
    limit: int = 4,
    lineup_result: PipelineResult | None = None,
    match: Match | None = None,
    goalscorer_markets: set[str] | None = None,
    clean_sheet_markets: set[str] | None = None,
    exact_score_markets: set[str] | None = None,
) -> list[RankedOutcome] | list[FantamondialePick]:
    if match is not None:
        return rank_fantamondiale_picks(
            outcomes,
            match,
            limit=6,
            lineup_result=lineup_result,
            goalscorer_markets=goalscorer_markets or set(),
            clean_sheet_markets=clean_sheet_markets or set(),
            exact_score_markets=exact_score_markets or set(),
        )

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


def rank_fantamondiale_picks(
    outcomes: list[dict],
    match: Match,
    limit: int = 6,
    lineup_result: PipelineResult | None = None,
    goalscorer_markets: set[str] | None = None,
    clean_sheet_markets: set[str] | None = None,
    exact_score_markets: set[str] | None = None,
) -> list[FantamondialePick]:
    goalscorer_markets = goalscorer_markets or set()
    clean_sheet_markets = clean_sheet_markets or set()
    exact_score_markets = exact_score_markets or set()
    teams = {canonical_team(match.home_team), canonical_team(match.away_team)}
    lineup_confidence = _lineup_confidence(lineup_result)

    grouped: dict[tuple[str, str, str], list[tuple[float, str, int, str]]] = {}
    for outcome in outcomes:
        market_key = str(outcome.get("market_key") or "")
        if goalscorer_markets and market_key not in goalscorer_markets:
            continue
        side = str(outcome.get("name") or "").strip().lower()
        if side in {"no", "under"}:
            continue
        name = str(outcome.get("description") or outcome.get("name") or "").strip()
        if not name or name.lower() in {"yes", "over"}:
            continue
        player = match_player_bonus(name, allowed_teams=teams, include_goalkeepers=False)
        if not player:
            continue
        price = float(outcome["price"])
        source = str(outcome.get("source", "Unknown"))
        probability = implied_probability(price) * _appearance_multiplier(name, lineup_confidence)
        grouped.setdefault((player.name, player.team, player.role), []).append(
            (probability, source, player.bonus, player.role)
        )

    picks = [
        FantamondialePick(
            name=name,
            team=team,
            role=role,
            bonus=values[0][2],
            probability=average([prob for prob, _, _, _ in values]),
            expected_points=average([prob for prob, _, _, _ in values]) * values[0][2],
            sources=tuple(sorted({source for _, source, _, _ in values})),
        )
        for (name, team, role), values in grouped.items()
    ]
    picks.extend(
        _rank_goalkeeper_clean_sheets(
            outcomes,
            match,
            lineup_result=lineup_result,
            clean_sheet_markets=clean_sheet_markets,
            exact_score_markets=exact_score_markets,
        )
    )
    return _ensure_both_match_teams(sorted(picks, key=lambda item: item.expected_points, reverse=True), teams, limit)


def _rank_goalkeeper_clean_sheets(
    outcomes: list[dict],
    match: Match,
    lineup_result: PipelineResult | None,
    clean_sheet_markets: set[str],
    exact_score_markets: set[str],
) -> list[FantamondialePick]:
    teams = {canonical_team(match.home_team), canonical_team(match.away_team)}
    probabilities = _direct_clean_sheet_probabilities(outcomes, teams, clean_sheet_markets)
    inferred = _clean_sheet_probabilities_from_exact_scores(outcomes, match, exact_score_markets)
    for team, probability in inferred.items():
        probabilities.setdefault(team, []).append((probability, "correct-score clean-sheet inference"))

    picks: list[FantamondialePick] = []
    for team, values in probabilities.items():
        goalkeeper = _starting_goalkeeper(team, lineup_result) or _first_goalkeeper(team)
        if not goalkeeper:
            continue
        probability = min(average([probability for probability, _ in values]), 1.0)
        picks.append(
            FantamondialePick(
                name=goalkeeper.name,
                team=goalkeeper.team,
                role=goalkeeper.role,
                bonus=goalkeeper.bonus,
                probability=probability,
                expected_points=probability * goalkeeper.bonus,
                sources=tuple(sorted({source for _, source in values})),
            )
        )
    return picks


def _direct_clean_sheet_probabilities(
    outcomes: list[dict],
    teams: set[str],
    clean_sheet_markets: set[str],
) -> dict[str, list[tuple[float, str]]]:
    grouped: dict[str, list[tuple[float, str]]] = {}
    if not clean_sheet_markets:
        return grouped
    for outcome in outcomes:
        if str(outcome.get("market_key") or "") not in clean_sheet_markets:
            continue
        side = str(outcome.get("name") or "").strip().lower()
        if side in {"no", "under"}:
            continue
        label = f"{outcome.get('name') or ''} {outcome.get('description') or ''}"
        team = _team_from_label(label, teams)
        if not team:
            continue
        grouped.setdefault(team, []).append((implied_probability(float(outcome["price"])), str(outcome.get("source", "Unknown"))))
    return grouped


def _clean_sheet_probabilities_from_exact_scores(
    outcomes: list[dict],
    match: Match,
    exact_score_markets: set[str],
) -> dict[str, float]:
    if not exact_score_markets:
        return {}
    home_team = canonical_team(match.home_team)
    away_team = canonical_team(match.away_team)
    source_scores: dict[str, list[tuple[float, int, int]]] = {}
    for outcome in outcomes:
        if str(outcome.get("market_key") or "") not in exact_score_markets:
            continue
        score = _parse_score(str(outcome.get("name") or ""))
        if not score:
            continue
        home_goals, away_goals = score
        source = str(outcome.get("source", "Unknown"))
        probability = implied_probability(float(outcome["price"]))
        source_scores.setdefault(source, []).append((probability, home_goals, away_goals))

    source_totals: dict[str, dict[str, float]] = {}
    for source, scores in source_scores.items():
        top_scores = sorted(scores, key=lambda item: item[0], reverse=True)[:10]
        for probability, home_goals, away_goals in top_scores:
            if away_goals == 0:
                source_totals.setdefault(source, {}).setdefault(home_team, 0.0)
                source_totals[source][home_team] += probability
            if home_goals == 0:
                source_totals.setdefault(source, {}).setdefault(away_team, 0.0)
                source_totals[source][away_team] += probability
    result: dict[str, float] = {}
    for team in (home_team, away_team):
        values = [totals[team] for totals in source_totals.values() if team in totals]
        if values:
            result[team] = min(average(values), 1.0)
    return result


def _parse_score(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", value)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _team_from_label(label: str, teams: set[str]) -> str | None:
    canonical_label = canonical_team(label)
    if canonical_label in teams:
        return canonical_label
    label_keys = set(player_keys(label))
    for team in teams:
        if player_keys(team) & label_keys:
            return team
    return None


def _starting_goalkeeper(team: str, lineup_result: PipelineResult | None):
    if not lineup_result or not lineup_result.success or not isinstance(lineup_result.data, dict):
        return None
    lineup = _lineup_for_team(lineup_result, team)
    if not lineup or not lineup.players:
        return None
    goalkeepers = [player for player in load_player_bonuses() if player.team == team and player.role == "P"]
    for name in lineup.players[:2]:
        keys = player_keys(name)
        for goalkeeper in goalkeepers:
            if keys & player_keys(goalkeeper.name):
                return goalkeeper
    first_player = lineup.players[0]
    matched = match_player_bonus(first_player, allowed_teams={team}, include_goalkeepers=True)
    if matched and matched.role == "P":
        return matched
    return None


def _lineup_for_team(lineup_result: PipelineResult, team: str) -> TeamLineup | None:
    for lineup_team, lineup in lineup_result.data.items():
        if canonical_team(str(lineup_team)) == team and isinstance(lineup, TeamLineup):
            return lineup
    return None


def _first_goalkeeper(team: str):
    for player in load_player_bonuses():
        if player.team == team and player.role == "P":
            return player
    return None


def _ensure_both_match_teams(
    picks: list[FantamondialePick],
    teams: set[str],
    limit: int,
) -> list[FantamondialePick]:
    selected = picks[:limit]
    selected_teams = {pick.team for pick in selected}
    missing = [team for team in teams if team not in selected_teams]
    for team in missing:
        replacement = next((pick for pick in picks if pick.team == team and pick not in selected), None)
        if replacement and len(selected) >= limit:
            selected[-1] = replacement
        elif replacement:
            selected.append(replacement)
    return sorted(selected, key=lambda item: item.expected_points, reverse=True)[:limit]


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
        return 1.0 if is_official else 0.95
    if keys & substitutes:
        return 0.35 if is_official else 0.40
    if is_official and is_complete:
        return 0.03
    if is_official:
        return 0.65
    return 0.40


def _player_keys(name: str) -> set[str]:
    return player_keys(name)
