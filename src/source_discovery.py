SOURCE_DISCOVERY_SUMMARY = """\
Research summary:
- Fixtures: The Odds API events endpoint is preferred when ODDS_API_KEY is configured; a built-in FIFA 2026 opening schedule fallback keeps dry-run verification possible without credentials.
- Odds: The Odds API is preferred for documented sports, events, event odds, and event market discovery endpoints, while exact-score odds are aggregated from all configured providers that expose that market, including API-Football fixture odds via LINEUP_API_KEY. Correct-score and soccer goalscorer availability varies by bookmaker, so exact-score and goalscorer market keys are configurable.
- Lineups: API-Football is tried first for script-friendly keyed access. Sportmonks can be configured as a second provider; official lineups are available close to kickoff, while predicted lineups require its paid Expected Lineups add-on. If API providers do not return a lineup, the app tries Goal.com sitemap-discovered predicted lineup pages, then TalkSport match previews, then the local static team database.
"""
