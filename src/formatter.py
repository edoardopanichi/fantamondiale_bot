from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

from .models import Match, PipelineResult, RankedOutcome, TeamLineup


def _format_probability(value: float) -> str:
    return f"{value * 100:.1f}%"


def _html(value: object) -> str:
    return escape(str(value), quote=False)


def _format_ranked(items: list[RankedOutcome], unavailable: str) -> str:
    if not items:
        return f"<i>{_html(unavailable)}</i>"
    return "\n".join(
        f"{index}. <b>{_html(item.name)}</b> <i>{_format_probability(item.probability)}</i>"
        for index, item in enumerate(items, start=1)
    )


def _format_lineups(match: Match, result: PipelineResult) -> str:
    source = _format_lineup_source(result)
    if not result.success or not isinstance(result.data, dict):
        return f"{source}\n<i>Probable lineup unavailable</i>"
    blocks = []
    for team in (match.home_team, match.away_team):
        lineup = result.data.get(team) or result.data.get(team.replace("USA", "United States"))
        if isinstance(lineup, TeamLineup) and lineup.players:
            lines = [f"<b>{_html(team)}</b>"]
            if lineup.formation:
                lines.append(f"<i>Module: {_html(lineup.formation)}</i>")
            lines.extend(f"- {_html(player)}" for player in lineup.players)
            blocks.append("\n".join(lines))
        elif isinstance(lineup, list) and lineup:
            blocks.append(f"<b>{_html(team)}</b>\n" + "\n".join(f"- {_html(player)}" for player in lineup))
        else:
            blocks.append(f"<b>{_html(team)}</b>\n<i>Probable lineup unavailable</i>")
    return f"{source}\n\n" + "\n\n".join(blocks)


def _format_lineup_source(result: PipelineResult) -> str:
    if result.success and result.source:
        return f"<i>Lineup source: {_html(result.source)}</i>"
    if result.source:
        return f"<i>Lineup source: unavailable after trying {_html(result.source)}</i>"
    return "<i>Lineup source: unavailable</i>"


def format_message(
    match: Match,
    lineup_result: PipelineResult,
    exact_score_result: PipelineResult,
    goalscorer_result: PipelineResult,
    timezone: str,
    notification_stage: str | None = None,
    half_time_result: PipelineResult | None = None,
) -> str:
    local_kickoff = match.kickoff_time_utc.astimezone(ZoneInfo(timezone))
    exact_scores = exact_score_result.data if isinstance(exact_score_result.data, list) else []
    goalscorers = goalscorer_result.data if isinstance(goalscorer_result.data, list) else []
    half_time_results = half_time_result.data if half_time_result and isinstance(half_time_result.data, list) else []
    title = "🏆 World Cup Match Alert"
    if notification_stage == "lineup":
        title = "📋 World Cup Lineup Alert"
    return "\n".join(
        [
            f"<b>{title}</b>",
            "",
            f"⚽ <b>{_html(match.home_team)} vs {_html(match.away_team)}</b>",
            f"🕒 <i>{_html(local_kickoff.strftime('%Y-%m-%d %H:%M %Z'))}</i>",
            "",
            "👥 <b>Probable Lineups</b>",
            "",
            _format_lineups(match, lineup_result),
            "",
            "🎯 <b>Most Likely Exact Scores</b>",
            "",
            _format_ranked(exact_scores, "Exact score odds unavailable"),
            "",
            "⏱ <b>Most Likely Half-Time Results</b>",
            "",
            _format_ranked(half_time_results, "Half-time result odds unavailable"),
            "",
            "🥅 <b>Most Likely Goalscorers</b>",
            "",
            _format_ranked(goalscorers, "Goalscorer odds unavailable"),
        ]
    )
