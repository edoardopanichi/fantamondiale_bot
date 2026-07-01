from __future__ import annotations

import json
from dataclasses import replace
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .config import Config
from .goalscorers import rank_goalscorers
from .lineup_provider import resolve_api_football_fixture_id, urlopen_with_headers
from .matches import ODDS_API_BASE
from .models import Match, PipelineResult, RankedOutcome
from .probability import average, implied_probability
from .score_predictions import rank_exact_scores


def _fetch_json(url: str, timeout: int = 20) -> object:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _api_error_message(exc: Exception) -> str:
    if not isinstance(exc, HTTPError):
        return str(exc)
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return str(exc)
    if not isinstance(payload, dict):
        return str(exc)
    error_code = payload.get("error_code")
    message = payload.get("message") or payload.get("error")
    if error_code and message:
        return f"{exc} ({error_code}: {message})"
    if message:
        return f"{exc} ({message})"
    return str(exc)


def _odds_api_configs(config: Config) -> tuple[Config, ...]:
    if not config.odds_api_key:
        return ()
    configs = [config]
    if config.odds_api_secondary_key and config.odds_api_secondary_key != config.odds_api_key:
        configs.append(replace(config, odds_api_key=config.odds_api_secondary_key))
    return tuple(configs)


def _fetch_odds_api_outcomes(
    config: Config,
    match: Match,
    market_keys: tuple[str, ...],
) -> tuple[list[dict], set[str], str | None, bool]:
    provider_error = None
    for odds_config in _odds_api_configs(config):
        try:
            available = _fetch_available_markets(odds_config, match)
            markets = tuple(key for key in market_keys if key in available)
            if not markets:
                return [], set(), provider_error, False
            payload = _fetch_event_odds(odds_config, match, markets)
            outcomes, sources = _collect_market_outcomes(payload, markets)
            return outcomes, sources, None, True
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            provider_error = _api_error_message(exc)
    return [], set(), provider_error, True


def _collect_market_outcomes(payload: object, market_keys: tuple[str, ...]) -> tuple[list[dict], set[str]]:
    outcomes: list[dict] = []
    sources: set[str] = set()
    event_payloads = payload if isinstance(payload, list) else [payload]
    for event in event_payloads:
        if not isinstance(event, dict):
            continue
        for bookmaker in event.get("bookmakers", []):
            title = str(bookmaker.get("title") or bookmaker.get("key") or "Unknown")
            for market in bookmaker.get("markets", []):
                if market.get("key") not in market_keys:
                    continue
                sources.add(title)
                for outcome in market.get("outcomes", []):
                    if "price" in outcome:
                        item = dict(outcome)
                        item["source"] = title
                        item["market_key"] = market.get("key")
                        outcomes.append(item)
    return outcomes, sources


def _rank_named_outcomes(outcomes: list[dict], limit: int) -> list[RankedOutcome]:
    grouped: dict[str, list[tuple[float, str]]] = {}
    for outcome in outcomes:
        name = str(outcome.get("name") or "").strip()
        if not name:
            continue
        price = float(outcome["price"])
        source = str(outcome.get("source", "Unknown"))
        grouped.setdefault(name, []).append((implied_probability(price), source))
    ranked = [
        RankedOutcome(
            name=name,
            probability=average([prob for prob, _ in values]),
            sources=tuple(sorted({source for _, source in values})),
        )
        for name, values in grouped.items()
    ]
    return sorted(ranked, key=lambda item: item.probability, reverse=True)[:limit]


def _fetch_event_odds(config: Config, match: Match, markets: tuple[str, ...]) -> object:
    params = urlencode(
        {
            "apiKey": config.odds_api_key,
            "regions": config.odds_regions,
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
    )
    url = f"{ODDS_API_BASE}/sports/{config.odds_sport_key}/events/{match.id}/odds?{params}"
    return _fetch_json(url)


def _fetch_available_markets(config: Config, match: Match) -> set[str]:
    params = urlencode({"apiKey": config.odds_api_key, "regions": config.odds_regions})
    url = f"{ODDS_API_BASE}/sports/{config.odds_sport_key}/events/{match.id}/markets?{params}"
    payload = _fetch_json(url)
    available: set[str] = set()
    if not isinstance(payload, dict):
        return available
    for bookmaker in payload.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            key = market.get("key")
            if key:
                available.add(str(key))
    return available


def _fetch_api_football_exact_score_outcomes(config: Config, match: Match) -> tuple[list[dict], set[str]]:
    if not config.lineup_api_key:
        return [], set()
    fixture_id = match.id if match.id.isdigit() else resolve_api_football_fixture_id(config, match)
    if not fixture_id:
        return [], set()
    params = urlencode({"fixture": fixture_id})
    payload = json.loads(
        urlopen_with_headers(
            f"https://v3.football.api-sports.io/odds?{params}",
            {"x-apisports-key": config.lineup_api_key},
        ).decode("utf-8")
    )
    outcomes: list[dict] = []
    sources: set[str] = set()
    for item in payload.get("response", []):
        for bookmaker in item.get("bookmakers", []):
            source = str(bookmaker.get("name") or "API-Football")
            for bet in bookmaker.get("bets", []):
                if str(bet.get("name", "")).lower() != "exact score":
                    continue
                sources.add(source)
                for value in bet.get("values", []):
                    odd = value.get("odd")
                    name = value.get("value")
                    if odd and name:
                        outcomes.append({"name": str(name), "price": float(odd), "source": source})
    return outcomes, sources


def run_exact_score_pipeline(config: Config, match: Match) -> PipelineResult:
    if not config.odds_api_key:
        return PipelineResult(False, data=[], error="ODDS_API_KEY is not configured", source="The Odds API")
    try:
        outcomes, sources, odds_error, tried_odds_api_markets = _fetch_odds_api_outcomes(
            config,
            match,
            config.exact_score_markets,
        )
        if outcomes:
            source_name = ", ".join(sorted(sources)) or "The Odds API"
        else:
            outcomes, sources = _fetch_api_football_exact_score_outcomes(config, match)
            source_name = ", ".join(sorted(sources)) or "API-Football"
        ranked = rank_exact_scores(outcomes)
        if not ranked:
            return PipelineResult(
                False,
                data=[],
                error=odds_error if tried_odds_api_markets and odds_error else "No exact score odds available",
                source="The Odds API",
            )
        return PipelineResult(True, data=ranked, source=source_name)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return PipelineResult(False, data=[], error=_api_error_message(exc), source="The Odds API")


def run_goalscorer_pipeline(
    config: Config,
    match: Match,
    lineup_result: PipelineResult | None = None,
    exact_score_result: PipelineResult | None = None,
) -> PipelineResult:
    if not config.odds_api_key:
        return PipelineResult(False, data=[], error="ODDS_API_KEY is not configured", source="The Odds API")
    try:
        outcomes, sources, odds_error, tried_odds_api_markets = _fetch_odds_api_outcomes(
            config,
            match,
            config.goalscorer_markets + config.clean_sheet_markets + config.exact_score_markets,
        )
        if not tried_odds_api_markets:
            return PipelineResult(False, data=[], error="No goalscorer market available for this event", source="The Odds API")
        if exact_score_result and not any(
            str(outcome.get("market_key") or "") in config.exact_score_markets for outcome in outcomes
        ):
            outcomes.extend(_ranked_exact_scores_as_outcomes(exact_score_result))
        ranked = rank_goalscorers(
            outcomes,
            lineup_result=lineup_result,
            match=match,
            goalscorer_markets=set(config.goalscorer_markets),
            clean_sheet_markets=set(config.clean_sheet_markets),
            exact_score_markets={*config.exact_score_markets, "ranked_exact_score"},
        )
        if not ranked:
            return PipelineResult(
                False,
                data=[],
                error=odds_error or "No goalscorer odds available",
                source="The Odds API",
            )
        return PipelineResult(True, data=ranked, source=", ".join(sorted(sources)) or "The Odds API")
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return PipelineResult(False, data=[], error=_api_error_message(exc), source="The Odds API")


def _ranked_exact_scores_as_outcomes(result: PipelineResult) -> list[dict]:
    if not result.success or not isinstance(result.data, list):
        return []
    outcomes = []
    for item in result.data:
        if not isinstance(item, RankedOutcome) or item.probability <= 0:
            continue
        outcomes.append(
            {
                "name": item.name,
                "price": 1 / item.probability,
                "source": result.source or "Exact-score pipeline",
                "market_key": "ranked_exact_score",
            }
        )
    return outcomes


def run_half_time_result_pipeline(config: Config, match: Match) -> PipelineResult:
    if not config.odds_api_key:
        return PipelineResult(False, data=[], error="ODDS_API_KEY is not configured", source="The Odds API")
    try:
        outcomes, sources, odds_error, tried_odds_api_markets = _fetch_odds_api_outcomes(
            config,
            match,
            config.half_time_result_markets,
        )
        if not tried_odds_api_markets:
            return PipelineResult(False, data=[], error="No half-time result market available for this event", source="The Odds API")
        ranked = _rank_named_outcomes(outcomes, limit=3)
        if not ranked:
            return PipelineResult(
                False,
                data=[],
                error=odds_error or "No half-time result odds available",
                source="The Odds API",
            )
        return PipelineResult(True, data=ranked, source=", ".join(sorted(sources)) or "The Odds API")
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return PipelineResult(False, data=[], error=_api_error_message(exc), source="The Odds API")
