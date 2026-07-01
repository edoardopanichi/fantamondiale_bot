from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime, timedelta
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs

import pytest

from src.config import load_config
from src.formatter import format_message
from src.goalscorers import rank_goalscorers
from src.lineup_provider import _normalize_team, _team_url_terms, run_lineup_pipeline
from src.main import run
from src.matches import LINEUP_NOTIFICATION, due_notification_stages, get_upcoming_matches, inside_notification_window
from src.models import Match, PipelineResult, RankedOutcome, TeamLineup
from src.odds import run_exact_score_pipeline, run_goalscorer_pipeline
from src.probability import implied_probability
from src.score_predictions import rank_exact_scores
from src.storage import already_notified, load_notified, save_notified_match
from src.telegram_client import send_telegram_message


@pytest.fixture(autouse=True)
def _ignore_local_secrets(monkeypatch):
    monkeypatch.setattr("src.config._load_local_secrets", lambda path=None: {})


def _args(**overrides):
    values = {
        "dry_run": False,
        "lookahead_hours": None,
        "match_id": None,
        "send_test_telegram": False,
        "manual_override": False,
        "force_notifications": False,
        "save_dry_run": False,
    }
    values.update(overrides)
    return Namespace(**values)


def test_probability_calculation():
    assert implied_probability(2.0) == 0.5


def test_exact_score_ranking_ignores_match_winner_markets():
    ranked = rank_exact_scores(
        [
            {"name": "Home Win", "price": 1.5, "source": "Book A"},
            {"name": "1-1", "price": 7.0, "source": "Book A"},
            {"name": "2-1", "price": 8.0, "source": "Book A"},
            {"name": "1:0", "price": 10.0, "source": "Book A"},
            {"name": "0-0", "price": 11.0, "source": "Book A"},
            {"name": "3-3", "price": 50.0, "source": "Book A"},
        ]
    )
    assert [item.name for item in ranked] == ["1-1", "2-1", "1-0", "0-0"]


def test_goalscorer_ranking_averages_sources():
    ranked = rank_goalscorers(
        [
            {"name": "Player A", "price": 2.0, "source": "Book A"},
            {"name": "Player A", "price": 4.0, "source": "Book B"},
            {"name": "Player B", "price": 3.0, "source": "Book A"},
        ]
    )
    assert ranked[0].name == "Player A"
    assert round(ranked[0].probability, 3) == 0.375


def test_goalscorer_ranking_discounts_official_substitutes():
    ranked = rank_goalscorers(
        [
            {"description": "Bench Striker", "name": "Yes", "price": 3.0, "source": "Book A"},
            {"description": "Starting Forward", "name": "Yes", "price": 5.0, "source": "Book A"},
            {"description": "Absent Star", "name": "Yes", "price": 2.0, "source": "Book A"},
        ],
        lineup_result=PipelineResult(
            True,
            data={
                "England": TeamLineup(
                    ["Starting Forward"],
                    substitutes=["Bench Striker"],
                ),
                "Opponent": TeamLineup(["Opponent Starter"]),
            },
            source="API-Football",
        ),
    )

    assert [item.name for item in ranked] == ["Starting Forward", "Bench Striker", "Absent Star"]
    assert round(ranked[0].probability, 3) == 0.2
    assert round(ranked[1].probability, 3) == 0.117


def test_goalscorer_ranking_keeps_unknown_players_viable_for_partial_official_lineups():
    ranked = rank_goalscorers(
        [
            {"description": "Known Substitute", "name": "Yes", "price": 3.0, "source": "Book A"},
            {"description": "Unknown Opponent", "name": "Yes", "price": 4.0, "source": "Book A"},
        ],
        lineup_result=PipelineResult(
            True,
            data={"England": TeamLineup(["Known Starter"], substitutes=["Known Substitute"])},
            source="API-Football",
        ),
    )

    assert [item.name for item in ranked] == ["Unknown Opponent", "Known Substitute"]
    assert round(ranked[0].probability, 3) == 0.163


def test_goalscorer_ranking_only_softly_discounts_predicted_non_starters():
    ranked = rank_goalscorers(
        [
            {"description": "Elite Bench Forward", "name": "Yes", "price": 2.0, "source": "Book A"},
            {"description": "Starting Defender", "name": "Yes", "price": 40.0, "source": "Book A"},
        ],
        lineup_result=PipelineResult(
            True,
            data={"Argentina": TeamLineup(["Starting Defender"])},
            source="Local static team database",
        ),
    )

    assert [item.name for item in ranked] == ["Elite Bench Forward", "Starting Defender"]
    assert round(ranked[0].probability, 3) == 0.325
    assert round(ranked[1].probability, 3) == 0.023


def _quota_http_error() -> HTTPError:
    return HTTPError(
        url="https://example.test",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=BytesIO(
            b'{"message":"Usage quota has been reached. See usage plans at https://the-odds-api.com",'
            b'"error_code":"OUT_OF_USAGE_CREDITS"}'
        ),
    )


def test_exact_score_pipeline_falls_back_when_odds_api_quota_is_exhausted(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "odds-key")
    monkeypatch.setenv("LINEUP_API_KEY", "lineup-key")
    monkeypatch.setattr("src.odds._fetch_available_markets", lambda config, match: (_ for _ in ()).throw(_quota_http_error()))
    monkeypatch.setattr(
        "src.odds._fetch_api_football_exact_score_outcomes",
        lambda config, match: ([{"name": "1:0", "price": 5.0, "source": "API-Football"}], {"API-Football"}),
    )
    config = load_config(_args())
    match = Match("event-1", "A", "B", datetime(2026, 6, 22, 21, 0, tzinfo=UTC))

    result = run_exact_score_pipeline(config, match)

    assert result.success
    assert [item.name for item in result.data] == ["1-0"]
    assert result.source == "API-Football"


def test_exact_score_pipeline_uses_secondary_odds_key_before_api_football(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "primary-key")
    monkeypatch.setenv("ODDS_API_SECONDARY_KEY", "secondary-key")
    monkeypatch.setenv("LINEUP_API_KEY", "lineup-key")
    calls = []

    def fake_available_markets(config, match):
        calls.append(config.odds_api_key)
        if config.odds_api_key == "primary-key":
            raise _quota_http_error()
        return {"correct_score"}

    def fake_event_odds(config, match, markets):
        assert config.odds_api_key == "secondary-key"
        assert markets == ("correct_score",)
        return {
            "bookmakers": [
                {
                    "title": "Book A",
                    "markets": [
                        {
                            "key": "correct_score",
                            "outcomes": [{"name": "2-1", "price": 8.0}],
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr("src.odds._fetch_available_markets", fake_available_markets)
    monkeypatch.setattr("src.odds._fetch_event_odds", fake_event_odds)
    monkeypatch.setattr(
        "src.odds._fetch_api_football_exact_score_outcomes",
        lambda config, match: pytest.fail("API-Football should not be tried when the secondary Odds API key works"),
    )
    config = load_config(_args())
    match = Match("event-1", "A", "B", datetime(2026, 6, 22, 21, 0, tzinfo=UTC))

    result = run_exact_score_pipeline(config, match)

    assert result.success
    assert calls == ["primary-key", "secondary-key"]
    assert [item.name for item in result.data] == ["2-1"]
    assert result.source == "Book A"


def test_odds_pipeline_reports_provider_error_body(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "odds-key")
    monkeypatch.setattr("src.odds._fetch_available_markets", lambda config, match: (_ for _ in ()).throw(_quota_http_error()))
    config = load_config(_args())
    match = Match("event-1", "A", "B", datetime(2026, 6, 22, 21, 0, tzinfo=UTC))

    result = run_goalscorer_pipeline(config, match)

    assert not result.success
    assert "OUT_OF_USAGE_CREDITS" in result.error


def test_notification_window_logic():
    config = load_config(_args())
    now = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    inside = Match("a", "A", "B", now + timedelta(hours=3), "test")
    late = Match("late", "A", "B", now + timedelta(hours=2), "test")
    outside = Match("b", "A", "B", now + timedelta(hours=4), "test")
    assert inside_notification_window(inside, config, now)
    assert inside_notification_window(late, config, now)
    assert not inside_notification_window(outside, config, now)


def test_first_notification_catch_up_stops_when_lineup_window_starts():
    config = load_config(_args())
    kickoff = datetime(2026, 6, 11, 19, 0, tzinfo=UTC)
    match = Match("catch-up", "A", "B", kickoff, "test")
    assert due_notification_stages(match, config, kickoff - timedelta(hours=2)) == ["first"]
    assert due_notification_stages(match, config, kickoff - timedelta(hours=1)) == [LINEUP_NOTIFICATION]
    assert due_notification_stages(match, config, kickoff) == []


def test_evening_notification_for_overnight_match():
    config = load_config(_args())
    now = datetime(2026, 6, 10, 19, 0, tzinfo=UTC)  # 21:00 Europe/Amsterdam.
    overnight = Match("night", "A", "B", datetime(2026, 6, 11, 4, 30, tzinfo=UTC), "test")
    assert due_notification_stages(overnight, config, now) == ["first"]


def test_evening_notification_for_overnight_match_catches_late_runs():
    config = load_config(_args())
    now = datetime(2026, 6, 10, 20, 15, tzinfo=UTC)  # 22:15 Europe/Amsterdam.
    overnight = Match("night", "A", "B", datetime(2026, 6, 11, 4, 30, tzinfo=UTC), "test")
    assert due_notification_stages(overnight, config, now) == ["first"]


def test_lineup_notification_window():
    config = load_config(_args())
    match = Match("lineup", "A", "B", datetime(2026, 6, 11, 19, 0, tzinfo=UTC), "test")
    assert due_notification_stages(match, config, datetime(2026, 6, 11, 18, 0, tzinfo=UTC)) == [
        LINEUP_NOTIFICATION
    ]
    assert due_notification_stages(match, config, datetime(2026, 6, 11, 18, 15, tzinfo=UTC)) == [
        LINEUP_NOTIFICATION
    ]
    assert due_notification_stages(match, config, datetime(2026, 6, 11, 18, 40, tzinfo=UTC)) == [
        LINEUP_NOTIFICATION
    ]
    assert due_notification_stages(match, config, datetime(2026, 6, 11, 18, 59, tzinfo=UTC)) == [
        LINEUP_NOTIFICATION
    ]
    assert due_notification_stages(match, config, datetime(2026, 6, 11, 17, 59, tzinfo=UTC)) == ["first"]


def test_late_evening_and_midnight_matches_get_first_and_lineup_alerts():
    config = load_config(_args())
    late_evening = Match("late", "A", "B", datetime(2026, 6, 11, 21, 0, tzinfo=UTC), "test")
    midnight = Match("midnight", "A", "B", datetime(2026, 6, 11, 22, 0, tzinfo=UTC), "test")

    assert due_notification_stages(late_evening, config, datetime(2026, 6, 11, 18, 0, tzinfo=UTC)) == ["first"]
    assert due_notification_stages(late_evening, config, datetime(2026, 6, 11, 20, 15, tzinfo=UTC)) == [
        LINEUP_NOTIFICATION,
    ]
    assert due_notification_stages(midnight, config, datetime(2026, 6, 11, 19, 0, tzinfo=UTC)) == ["first"]
    assert due_notification_stages(midnight, config, datetime(2026, 6, 11, 21, 15, tzinfo=UTC)) == [
        LINEUP_NOTIFICATION,
    ]


def test_early_morning_matches_do_not_get_lineup_alert():
    config = load_config(_args())
    early_morning = Match("early", "A", "B", datetime(2026, 6, 11, 2, 0, tzinfo=UTC), "test")

    assert due_notification_stages(early_morning, config, datetime(2026, 6, 10, 19, 0, tzinfo=UTC)) == ["first"]
    assert due_notification_stages(early_morning, config, datetime(2026, 6, 11, 1, 15, tzinfo=UTC)) == ["first"]


def test_duplicate_prevention(tmp_path: Path):
    path = tmp_path / "sent.json"
    assert load_notified(path) == set()
    assert not already_notified("match-1", path=path)
    save_notified_match("match-1", path=path)
    save_notified_match("match-1", path=path)
    assert load_notified(path) == {"match-1"}
    assert not already_notified("match-1", LINEUP_NOTIFICATION, path)
    save_notified_match("match-1", LINEUP_NOTIFICATION, path)
    assert already_notified("match-1", LINEUP_NOTIFICATION, path)


def test_formatter_handles_unavailable_pipelines():
    match = Match("m1", "Mexico", "South Africa", datetime(2026, 6, 11, 19, 0, tzinfo=UTC))
    message = format_message(
        match,
        PipelineResult(False, error="no lineup", source="API-Football"),
        PipelineResult(True, data=[RankedOutcome("1-0", 0.2, ("Book A",))], source="Book A"),
        PipelineResult(False, data=[], error="no scorers", source="The Odds API"),
        "Europe/Amsterdam",
    )
    assert "Mexico vs South Africa" in message
    assert "🏆" in message
    assert "<b>Mexico vs South Africa</b>" in message
    assert "Probable lineup unavailable" in message
    assert "1. <b>1-0</b> <i>20.0%</i>" in message
    assert "Half-time result odds unavailable" in message
    assert "Goalscorer odds unavailable" in message


def test_formatter_includes_team_modules():
    match = Match("m1", "Mexico", "South Africa", datetime(2026, 6, 11, 19, 0, tzinfo=UTC))
    message = format_message(
        match,
        PipelineResult(
            True,
            data={
                "Mexico": TeamLineup(["Player A", "Player B"], formation="4-4-2"),
                "South Africa": TeamLineup(["Player C", "Player D"], formation="5-3-2"),
            },
            source="API-Football",
        ),
        PipelineResult(False, data=[]),
        PipelineResult(False, data=[]),
        "Europe/Amsterdam",
        half_time_result=PipelineResult(True, data=[RankedOutcome("Draw", 0.45, ("Book A",))], source="Book A"),
    )
    assert "Module: 4-4-2" in message
    assert "Module: 5-3-2" in message
    assert "Lineup source: API-Football" in message
    assert "Most Likely Half-Time Results" in message
    assert "1. <b>Draw</b> <i>45.0%</i>" in message


def test_formatter_includes_unavailable_lineup_source():
    match = Match("m1", "Mexico", "South Africa", datetime(2026, 6, 11, 19, 0, tzinfo=UTC))
    message = format_message(
        match,
        PipelineResult(False, data=None, error="none", source="API-Football, Goal.com predicted lineups"),
        PipelineResult(False, data=[]),
        PipelineResult(False, data=[]),
        "Europe/Amsterdam",
    )
    assert "Lineup source: unavailable after trying API-Football, Goal.com predicted lineups" in message


def test_lineup_pipeline_discovers_goal_article_from_sitemap(monkeypatch):
    match = Match("m1", "USA", "Paraguay", datetime(2026, 6, 12, 19, 0, tzinfo=UTC))
    sitemap = (
        "https://www.goal.com/it/notizie/dove-vedere-usa-paraguay-diretta-tv-streaming-online/blt1"
        "<loc>https://www.goal.com/it/notizie/formazioni-usa-paraguay-le-ultime-sulla-partita-e-dove-vederla-in-tv-e-in-streaming/blt2</loc>"
    )
    players = "".join(
        f'<li class="fco-paired-player-list__row">'
        f'<span class="fco-player-row-content__player-name">Home {index}</span>'
        f'<span class="fco-player-row-content__player-name">Away {index}</span>'
        f"</li>"
        for index in range(1, 12)
    )
    article = (
        '<section class="fco-football-match-lineups">'
        '<span class="fco-pitch-lineup-header__formation">3-4-2-1</span>'
        '<span>4-3-3</span></div></div><div class="fco-field">'
        f"{players}</section>"
    )
    calls = []

    def fake_urlopen(url, headers, timeout=20):
        calls.append(url)
        if "sitemap" in url:
            return sitemap.encode("utf-8")
        if "formazioni-usa-paraguay" in url:
            return article.encode("utf-8")
        return b"<html></html>"

    monkeypatch.setattr("src.lineup_provider.urlopen_with_headers", fake_urlopen)
    config = load_config(_args())
    result = run_lineup_pipeline(config, match)

    assert result.success
    assert result.source == "Goal.com predicted lineups"
    assert result.data["USA"].formation == "3-4-2-1"
    assert result.data["Paraguay"].players[-1] == "Away 11"
    assert any("formazioni-usa-paraguay" in call for call in calls)


def test_goal_sitemap_discovery_handles_italian_and_encoded_team_names(monkeypatch):
    match = Match("m1", "Germany", "Curaçao", datetime(2026, 6, 14, 16, 0, tzinfo=UTC))
    sitemap = (
        "<loc>https://www.goal.com/it/notizie/"
        "dove-vedere-germania-curac%CC%A7ao-diretta-tv-streaming-online/blt1</loc>"
    )
    players = "".join(
        f'<li class="fco-paired-player-list__row">'
        f'<span class="fco-player-row-content__player-name">Germany {index}</span>'
        f'<span class="fco-player-row-content__player-name">Curacao {index}</span>'
        f"</li>"
        for index in range(1, 12)
    )
    article = (
        '<section class="fco-football-match-lineups">'
        '<span class="fco-pitch-lineup-header__formation">4-2-3-1</span>'
        '<span>4-3-1-2</span></div></div><div class="fco-field">'
        f"{players}</section>"
    )

    def fake_urlopen(url, headers, timeout=20):
        if "sitemap" in url:
            return sitemap.encode("utf-8")
        if "germania-curac%CC%A7ao" in url:
            return article.encode("utf-8")
        return b"<html></html>"

    monkeypatch.setattr("src.lineup_provider.urlopen_with_headers", fake_urlopen)
    config = load_config(_args())
    result = run_lineup_pipeline(config, match)

    assert result.success
    assert result.source == "Goal.com predicted lineups"
    assert result.data["Germany"].formation == "4-2-3-1"
    assert result.data["Curaçao"].players[-1] == "Curacao 11"


@pytest.mark.parametrize(
    ("team", "expected_terms"),
    [
        ("Belgium", {"belgio"}),
        ("Brazil", {"brasile"}),
        ("Czech Republic", {"repubblica-ceca", "cechia"}),
        ("DR Congo", {"rd-congo", "repubblica-democratica-del-congo"}),
        ("Egypt", {"egitto"}),
        ("England", {"inghilterra"}),
        ("France", {"francia"}),
        ("Germany", {"germania"}),
        ("Ivory Coast", {"costa-d-avorio"}),
        ("Japan", {"giappone"}),
        ("Mexico", {"messico"}),
        ("Netherlands", {"olanda", "paesi-bassi"}),
        ("Saudi Arabia", {"arabia-saudita"}),
        ("South Africa", {"sudafrica"}),
        ("South Korea", {"corea-del-sud"}),
        ("Spain", {"spagna"}),
        ("Switzerland", {"svizzera"}),
        ("Turkey", {"turchia"}),
        ("United States", {"usa", "stati-uniti"}),
    ],
)
def test_goal_url_terms_include_italian_team_names(team, expected_terms):
    assert expected_terms <= _team_url_terms(team)


def test_goal_url_terms_cover_static_world_cup_teams():
    payload = json.loads(Path("data/static_team_lineups.json").read_text(encoding="utf-8"))
    missing = [team for team in payload if not _team_url_terms(team)]
    assert missing == []


@pytest.mark.parametrize(
    ("italian_name", "canonical"),
    [
        ("Francia", "france"),
        ("Inghilterra", "england"),
        ("Olanda", "netherlands"),
        ("Paesi Bassi", "netherlands"),
        ("Costa d'Avorio", "ivory coast"),
        ("Arabia Saudita", "saudi arabia"),
        ("Corea del Sud", "south korea"),
        ("Repubblica Ceca", "czech republic"),
    ],
)
def test_normalize_team_accepts_common_italian_names(italian_name, canonical):
    assert _normalize_team(italian_name) == canonical


def test_lineup_pipeline_uses_talksport_after_goal(monkeypatch):
    match = Match("m1", "Canada", "Bosnia", datetime(2026, 6, 12, 19, 0, tzinfo=UTC))
    search_payload = json_bytes(
        [
            {
                "id": 4318267,
                "title": "Canada vs Bosnia prediction",
                "url": "https://talksport.com/betting/4318267/canada-vs-bosnia-world-cup-preview/",
            }
        ]
    )
    post_payload = json_bytes(
        {
            "content": {
                "rendered": (
                    "<p>Canada vs Bosnia predicted lineups</p>"
                    "<p>Canada (4-4-2): Crepeau (GK); Johnston, Cornelius, Bombito, Laryea; "
                    "Buchanan, Kone, Eustaquio, Millar; J David, Larin</p>"
                    "<p>Bosnia (4-4-2): Vasilj (GK); Dedic, Katic, Muharemovic, Kolasinac; "
                    "Bajraktarevic, Basic, Sunjic, Memic; Demirovic, Dzeko</p>"
                )
            }
        }
    )

    def fake_urlopen(url, headers, timeout=20):
        if "goal.com" in url:
            return b"<urlset></urlset>" if "sitemap" in url else b"<html></html>"
        if "wp-json/wp/v2/search" in url:
            return search_payload
        if "wp-json/wp/v2/posts/4318267" in url:
            return post_payload
        raise AssertionError(url)

    monkeypatch.setattr("src.lineup_provider.urlopen_with_headers", fake_urlopen)
    config = load_config(_args())
    result = run_lineup_pipeline(config, match)

    assert result.success
    assert result.source == "TalkSport predicted lineups"
    assert result.data["Canada"].players[0] == "Crepeau"
    assert result.data["Bosnia"].formation == "4-4-2"


def test_lineup_pipeline_uses_static_database_when_editorial_sources_fail(monkeypatch):
    match = Match("m1", "USA", "Paraguay", datetime(2026, 6, 12, 19, 0, tzinfo=UTC))

    def fake_urlopen(url, headers, timeout=20):
        if "goal.com" in url:
            return b"<urlset></urlset>" if "sitemap" in url else b"<html></html>"
        if "wp-json/wp/v2/search" in url:
            return b"[]"
        raise AssertionError(url)

    monkeypatch.setattr("src.lineup_provider.urlopen_with_headers", fake_urlopen)
    config = load_config(_args())
    result = run_lineup_pipeline(config, match)

    assert result.success
    assert result.source == "Local static team database"
    assert result.data["USA"].formation == "4-3-3"
    assert result.data["Paraguay"].players[-1] == "Avalos"


def test_static_lineup_database_covers_all_world_cup_teams():
    import json

    payload = json.loads(Path("data/static_team_lineups.json").read_text(encoding="utf-8"))

    assert len(payload) == 48
    assert all(len(team["players"]) == 11 for team in payload.values())
    assert all(team["formation"] for team in payload.values())


def json_bytes(payload):
    import json

    return json.dumps(payload).encode("utf-8")


def test_telegram_client_requests_html_parse_mode(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(url, data, timeout):
        captured["url"] = url
        captured["payload"] = parse_qs(data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("src.telegram_client.urlopen", fake_urlopen)
    config = load_config(_args())
    config = config.__class__(
        **{
            **config.__dict__,
            "telegram_bot_token": "token",
            "telegram_chat_id": "chat",
        }
    )

    result = send_telegram_message(config, "<b>Hello</b>")

    assert result.success
    assert captured["payload"]["parse_mode"] == ["HTML"]
    assert captured["payload"]["text"] == ["<b>Hello</b>"]


def test_lookahead_hours_detects_opening_match(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    config = load_config(_args(lookahead_hours=24, dry_run=True))
    now = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
    matches = get_upcoming_matches(config, now)
    assert [match.id for match in matches] == ["fifa-2026-001-mexico-south-africa"]


def test_match_discovery_uses_secondary_odds_key_when_primary_fails(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "primary-key")
    monkeypatch.setenv("ODDS_API_SECONDARY_KEY", "secondary-key")
    calls = []

    def fake_fetch_json(url, timeout=20):
        calls.append(url)
        if "primary-key" in url:
            raise _quota_http_error()
        return [
            {
                "id": "event-1",
                "home_team": "A",
                "away_team": "B",
                "commence_time": "2026-06-22T21:00:00Z",
            }
        ]

    monkeypatch.setattr("src.matches._fetch_json", fake_fetch_json)
    config = load_config(_args(lookahead_hours=24))
    now = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)

    matches = get_upcoming_matches(config, now)

    assert [match.id for match in matches] == ["event-1"]
    assert len(calls) == 2
    assert "primary-key" in calls[0]
    assert "secondary-key" in calls[1]


def test_dry_run_does_not_save_notification_state(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.delenv("LINEUP_API_KEY", raising=False)
    config = load_config(_args(lookahead_hours=24, dry_run=True, manual_override=True))
    code = run(config, now=datetime(2026, 6, 10, 20, 0, tzinfo=UTC))
    assert code == 0
    assert not Path("data/sent_notifications.json").exists()
    output = capsys.readouterr().out
    assert "Matches found" in output
    assert "Lineup pipeline result: success" in output
    assert "Lineup source: Local static team database" in output
    assert "Exact score pipeline result: failed" in output
    assert "Goalscorer pipeline result: failed" in output
    assert "Telegram send result: success" in output


def test_real_run_saves_state_and_second_run_skips(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    match = Match("match-1", "Mexico", "South Africa", datetime(2026, 6, 11, 19, 0, tzinfo=UTC))
    monkeypatch.setattr("src.main.get_upcoming_matches", lambda config, now=None: [match])
    monkeypatch.setattr("src.main.due_notification_stages", lambda match, config, now=None: ["first"])
    monkeypatch.setattr("src.main.run_lineup_pipeline", lambda config, match: PipelineResult(False, error="none", source="test"))
    monkeypatch.setattr("src.main.run_exact_score_pipeline", lambda config, match: PipelineResult(False, data=[], error="none", source="test"))
    monkeypatch.setattr("src.main.run_half_time_result_pipeline", lambda config, match: PipelineResult(False, data=[], error="none", source="test"))
    monkeypatch.setattr(
        "src.main.run_goalscorer_pipeline",
        lambda config, match, lineup_result=None: PipelineResult(False, data=[], error="none", source="test"),
    )
    sent_messages = []

    def fake_send(config, message):
        sent_messages.append(message)
        return PipelineResult(True, data={"ok": True}, source="Telegram")

    monkeypatch.setattr("src.main.send_telegram_message", fake_send)
    config = load_config(_args())

    first_code = run(config, now=datetime(2026, 6, 11, 16, 0, tzinfo=UTC))
    second_code = run(config, now=datetime(2026, 6, 11, 16, 30, tzinfo=UTC))

    assert first_code == 0
    assert second_code == 0
    assert len(sent_messages) == 1
    assert load_notified(Path("data/sent_notifications.json")) == {"match-1"}
    assert "already notified" in capsys.readouterr().out


def test_force_notifications_resends_even_when_state_exists(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    match = Match("match-1", "Mexico", "South Africa", datetime(2026, 6, 11, 19, 0, tzinfo=UTC))
    monkeypatch.setattr("src.main.get_upcoming_matches", lambda config, now=None: [match])
    monkeypatch.setattr("src.main.due_notification_stages", lambda match, config, now=None: ["first"])
    monkeypatch.setattr("src.main.run_lineup_pipeline", lambda config, match: PipelineResult(False, error="none", source="test"))
    monkeypatch.setattr("src.main.run_exact_score_pipeline", lambda config, match: PipelineResult(False, data=[], error="none", source="test"))
    monkeypatch.setattr("src.main.run_half_time_result_pipeline", lambda config, match: PipelineResult(False, data=[], error="none", source="test"))
    monkeypatch.setattr(
        "src.main.run_goalscorer_pipeline",
        lambda config, match, lineup_result=None: PipelineResult(False, data=[], error="none", source="test"),
    )
    sent_messages = []

    def fake_send(config, message):
        sent_messages.append(message)
        return PipelineResult(True, data={"ok": True}, source="Telegram")

    monkeypatch.setattr("src.main.send_telegram_message", fake_send)
    save_notified_match("match-1", "first")
    config = load_config(_args(force_notifications=True))

    code = run(config, now=datetime(2026, 6, 11, 16, 0, tzinfo=UTC))

    assert code == 0
    assert len(sent_messages) == 1
    assert "already notified" not in capsys.readouterr().out


def test_manual_cli_match_id_execution(monkeypatch, capsys):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    config = load_config(
        _args(match_id="fifa-2026-001-mexico-south-africa", dry_run=True)
    )
    code = run(config, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert code == 0
    assert "Mexico vs South Africa" in capsys.readouterr().out


def test_telegram_test_message_command_dry_run(monkeypatch, capsys):
    config = load_config(_args(send_test_telegram=True, dry_run=True))
    code = run(config, now=datetime(2026, 6, 10, tzinfo=UTC))
    assert code == 0
    assert "Telegram send result: success" in capsys.readouterr().out


def test_readme_deployment_instructions_presence():
    text = Path("README.md").read_text(encoding="utf-8")
    for required in [
        "GitHub Actions",
        "workflow_dispatch",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "ODDS_API_KEY",
        "ODDS_API_SECONDARY_KEY",
        "python -m src.main --send-test-telegram",
        "No server",
    ]:
        assert required in text
