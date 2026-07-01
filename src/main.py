from __future__ import annotations

from datetime import UTC, datetime

from .cli import build_parser
from .config import Config, load_config
from .formatter import format_message
from .lineup_provider import run_lineup_pipeline
from .matches import due_notification_stages, get_upcoming_matches, manual_notification_stage
from .odds import run_exact_score_pipeline, run_goalscorer_pipeline, run_half_time_result_pipeline
from .source_discovery import SOURCE_DISCOVERY_SUMMARY
from .storage import already_notified, save_notified_match
from .telegram_client import send_telegram_message


def log(message: str) -> None:
    print(message, flush=True)


def send_test_telegram(config: Config) -> int:
    result = send_telegram_message(config, "Betting Match Notifier test message.")
    log(f"Telegram send result: {'success' if result.success else 'failed'}")
    if result.error:
        log(f"Errors, if any: {result.error}")
    return 0 if result.success else 1


def run(config: Config, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    log("Application started")
    log("Source discovery completed")
    log(SOURCE_DISCOVERY_SUMMARY.strip())

    if config.send_test_telegram:
        return send_test_telegram(config)

    matches = get_upcoming_matches(config, now=now)
    log(f"Matches retrieved: {len(matches)}")
    if not matches:
        log("No match present in the selected lookahead window.")
        log("Application finished")
        return 0

    log("Matches found")
    for match in matches:
        log(f"- {match.id}: {match.home_team} vs {match.away_team} at {match.kickoff_time_utc.isoformat()}")

    skipped = 0
    processed = 0
    for match in matches:
        if config.manual_override:
            stages = [stage for stage in [manual_notification_stage(config)] if stage]
        else:
            stages = due_notification_stages(match, config, now=now)

        stages_to_send = [
            stage
            for stage in stages
            if config.dry_run or config.force_notifications or not already_notified(match.id, stage)
        ]
        if stages and not stages_to_send and not config.dry_run:
            skipped += 1
            log(f"Matches skipped: {match.id} already notified for due notification stage")
            continue
        if not stages_to_send:
            skipped += 1
            log(f"Matches skipped: {match.id} outside notification window")
            continue

        processed += 1
        lineup_result = run_lineup_pipeline(config, match)
        log(f"Lineup pipeline result: {'success' if lineup_result.success else 'failed'}")
        if lineup_result.error:
            log(f"Lineup error: {lineup_result.error}")

        exact_score_result = run_exact_score_pipeline(config, match)
        log(f"Exact score pipeline result: {'success' if exact_score_result.success else 'failed'}")
        if exact_score_result.error:
            log(f"Exact score error: {exact_score_result.error}")

        half_time_result = run_half_time_result_pipeline(config, match)
        log(f"Half-time result pipeline result: {'success' if half_time_result.success else 'failed'}")
        if half_time_result.error:
            log(f"Half-time result error: {half_time_result.error}")

        goalscorer_result = run_goalscorer_pipeline(
            config,
            match,
            lineup_result=lineup_result,
            exact_score_result=exact_score_result,
        )
        log(f"Goalscorer pipeline result: {'success' if goalscorer_result.success else 'failed'}")
        if goalscorer_result.error:
            log(f"Goalscorer error: {goalscorer_result.error}")

        for stage in stages_to_send:
            message = format_message(
                match,
                lineup_result,
                exact_score_result,
                goalscorer_result,
                config.timezone,
                notification_stage=stage,
                half_time_result=half_time_result,
            )
            log(f"Formatted Telegram message for {stage} notification:")
            log(message)

            telegram_result = send_telegram_message(config, message)
            log(f"Telegram send result: {'success' if telegram_result.success else 'failed'}")
            if telegram_result.error:
                log(f"Errors, if any: {telegram_result.error}")
            if telegram_result.success and (not config.dry_run or config.save_dry_run):
                save_notified_match(match.id, stage)
                log(f"Notification state saved for {stage}")

    if skipped and not processed:
        log(f"Matches skipped: {skipped}")
    log("Application finished")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(load_config(args))


if __name__ == "__main__":
    raise SystemExit(main())
