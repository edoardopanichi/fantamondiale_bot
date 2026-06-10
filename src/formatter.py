from __future__ import annotations

from zoneinfo import ZoneInfo

from .models import Match, PipelineResult, RankedOutcome


def _format_probability(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_ranked(items: list[RankedOutcome], unavailable: str) -> str:
    if not items:
        return unavailable
    return "\n".join(
        f"{index}. {item.name} ({_format_probability(item.probability)})"
        for index, item in enumerate(items, start=1)
    )


def _format_lineups(match: Match, result: PipelineResult) -> str:
    if not result.success or not isinstance(result.data, dict):
        return "Probable lineup unavailable"
    blocks = []
    for team in (match.home_team, match.away_team):
        players = result.data.get(team) or result.data.get(team.replace("USA", "United States"))
        if players:
            blocks.append(f"{team}\n" + "\n".join(players))
        else:
            blocks.append(f"{team}\nProbable lineup unavailable")
    return "\n\n".join(blocks)


def format_message(
    match: Match,
    lineup_result: PipelineResult,
    exact_score_result: PipelineResult,
    goalscorer_result: PipelineResult,
    timezone: str,
) -> str:
    local_kickoff = match.kickoff_time_utc.astimezone(ZoneInfo(timezone))
    exact_scores = exact_score_result.data if isinstance(exact_score_result.data, list) else []
    goalscorers = goalscorer_result.data if isinstance(goalscorer_result.data, list) else []
    lineup_source = lineup_result.source or "Unavailable"
    odds_sources = sorted(
        {
            source
            for result in (exact_score_result, goalscorer_result)
            for source in (result.source or "").split(", ")
            if source
        }
    )
    if not odds_sources:
        odds_sources = ["Unavailable"]
    return "\n".join(
        [
            "World Cup Match Alert",
            "",
            "Match:",
            f"{match.home_team} vs {match.away_team}",
            "",
            "Kickoff:",
            local_kickoff.strftime("%Y-%m-%d %H:%M %Z"),
            "",
            "Probable Lineups",
            "",
            _format_lineups(match, lineup_result),
            "",
            "Most Likely Exact Scores",
            "",
            _format_ranked(exact_scores, "Exact score odds unavailable"),
            "",
            "Most Likely Goalscorers",
            "",
            _format_ranked(goalscorers, "Goalscorer odds unavailable"),
            "",
            "Lineup Source:",
            lineup_source,
            "",
            "Odds Sources:",
            "\n".join(odds_sources),
        ]
    )
