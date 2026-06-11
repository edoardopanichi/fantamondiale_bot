from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.config import load_config
from src.formatter import format_message
from src.goalscorers import rank_goalscorers
from src.main import run
from src.matches import LINEUP_NOTIFICATION, due_notification_stages, get_upcoming_matches, inside_notification_window
from src.models import Match, PipelineResult, RankedOutcome, TeamLineup
from src.probability import implied_probability
from src.score_predictions import rank_exact_scores
from src.storage import already_notified, load_notified, save_notified_match


def _args(**overrides):
    values = {
        "dry_run": False,
        "lookahead_hours": None,
        "match_id": None,
        "send_test_telegram": False,
        "manual_override": False,
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


def test_notification_window_logic():
    config = load_config(_args())
    now = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    inside = Match("a", "A", "B", now + timedelta(hours=3), "test")
    outside = Match("b", "A", "B", now + timedelta(hours=4), "test")
    assert inside_notification_window(inside, config, now)
    assert not inside_notification_window(outside, config, now)


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
    assert due_notification_stages(match, config, datetime(2026, 6, 11, 18, 0, tzinfo=UTC)) == [LINEUP_NOTIFICATION]
    assert due_notification_stages(match, config, datetime(2026, 6, 11, 18, 20, tzinfo=UTC)) == []


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
    assert "Probable lineup unavailable" in message
    assert "1. 1-0 (20.0%)" in message
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
    assert "Most Likely Half-Time Results" in message
    assert "1. Draw (45.0%)" in message


def test_lookahead_hours_detects_opening_match(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    config = load_config(_args(lookahead_hours=24, dry_run=True))
    now = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
    matches = get_upcoming_matches(config, now)
    assert [match.id for match in matches] == ["fifa-2026-001-mexico-south-africa"]


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
    assert "Lineup pipeline result: failed" in output
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
    monkeypatch.setattr("src.main.run_goalscorer_pipeline", lambda config, match: PipelineResult(False, data=[], error="none", source="test"))
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
        "python -m src.main --send-test-telegram",
        "No server",
    ]:
        assert required in text
