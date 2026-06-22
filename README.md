# Betting Match Notifier

Python app that sends Telegram alerts about FIFA World Cup matches. For matches from noon through 00:59 Europe/Amsterdam, it sends a first alert from roughly 3 hours before kickoff until 1 hour before kickoff, plus a second lineup alert from 1 hour before kickoff until kickoff. For overnight and early-morning matches from 01:00 through 11:59, it sends only one alert from 21:00 the evening before kickoff. It is designed for GitHub Actions. No server, VPS, Docker container, or always-on process is required.

Each alert includes match details, probable lineups and team modules/formations when available, the top 4 exact-score outcomes from odds, the top 3 half-time result outcomes from odds, and the top 4 anytime goalscorer outcomes from odds. Every data pipeline runs independently: if lineups fail, odds and Telegram still run; if exact-score odds fail, lineups/goalscorers/Telegram still run.

## Source Choices

Research summary used for the implementation:

- Fixtures: The Odds API events endpoint is preferred when `ODDS_API_KEY` is configured because it has stable documented sports/events endpoints. The app also includes a small FIFA 2026 opening-schedule fallback so local dry-run verification can detect the first matches without credentials. Reference: <https://the-odds-api.com/liveapi/guides/v4/>
- Odds: The Odds API is preferred because it has official API access, bookmaker aggregation, event odds, and event market discovery. If `ODDS_API_SECONDARY_KEY` is configured, The Odds API is retried with that key when the primary key errors before exact-score odds fall back to API-Football. Correct-score, half-time result, and soccer player-goalscorer coverage depends on bookmaker availability, so the market keys are configurable with `EXACT_SCORE_MARKETS`, `HALF_TIME_RESULT_MARKETS`, and `GOALSCORER_MARKETS`. Reference: <https://the-odds-api.com/sports-odds-data/betting-markets.html>
- Lineups: API-Football is tried first for official lineups via keyed API access. If it returns no lineup and `SPORTMONKS_API_TOKEN` is configured, Sportmonks is tried second, including the paid `expectedLineups` include when available. If API providers do not return a lineup, the app tries Goal.com Italian sitemap discovery for match-specific predicted lineup pages, then TalkSport's WordPress JSON search for match previews, then the local static team database in `data/static_team_lineups.json`, which contains all 48 national-team lineups from the Sky Sport formation guide. The Telegram message states which source was used. References: <https://www.api-football.com/documentation-v3>, <https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/premium-expected-lineups>

Provider keys that are actually mandatory:

- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are required for real sends.
- `ODDS_API_KEY` is required for live exact-score/goalscorer odds.
- `ODDS_API_SECONDARY_KEY` is optional and is used only if the primary Odds API key fails.
- `LINEUP_API_KEY` enables API-Football lineup retrieval.
- `SPORTMONKS_API_TOKEN` optionally enables Sportmonks as a second lineup provider.

Without odds or lineup keys, the app still sends the Telegram message. Lineups can still be filled by the editorial or static fallbacks; unavailable sections remain clearly marked.

## Repository Structure

```text
.github/workflows/notify.yml
data/sent_notifications.json
src/
tests/
requirements.txt
README.md
```

Notification state is stored in `data/sent_notifications.json`. GitHub Actions commits this file after a successful real notification so the same notification stage for the same match is not sent twice. First alerts and lineup alerts are tracked separately.

## Local Setup

```bash
git clone <repo-url>
cd betting-match-notifier
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows activation:

```bash
.venv\Scripts\activate
```

For local runs, you can store API keys in `config/secrets.local.json`. This file is ignored by Git and must not be committed:

```json
{
  "title": "Local API credentials for Betting Match Notifier",
  "description": "Ignored by Git. Used only for local runs and tests on this machine.",
  "ODDS_API_KEY": "...",
  "ODDS_API_SECONDARY_KEY": "...",
  "LINEUP_API_KEY": "...",
  "SPORTMONKS_API_TOKEN": "..."
}
```

Environment variables still take priority over the local JSON file.

The local JSON file is not used by GitHub Actions because it is not committed. For deployment, put the same values in GitHub Actions secrets.

## Manual Usage

```bash
python -m src.main
python -m src.main --dry-run
python -m src.main --lookahead-hours 24
python -m src.main --lookahead-hours 48 --dry-run
python -m src.main --match-id fifa-2026-001-mexico-south-africa --dry-run
python -m src.main --send-test-telegram
```

Use `--lookahead-hours` to test future matches even when no match is in the next 3 hours. If nothing is found, the app prints:

```text
No match present in the selected lookahead window.
```

Manual runs print matches found, matches skipped, lineup pipeline result, exact score pipeline result, goalscorer pipeline result, Telegram send result, and errors. Dry-runs print the formatted Telegram message, do not send Telegram, and do not save duplicate-prevention state unless `--save-dry-run` is passed.

To verify tomorrow's opening match data discovery from this repo on June 10, 2026:

```bash
python -m src.main --lookahead-hours 24 --dry-run --manual-override
```

## Telegram Bot Setup

1. Open Telegram.
2. Search for `@BotFather`.
3. Send `/newbot`.
4. Follow the prompts and copy the generated bot token.
5. Start a chat with the new bot and send any message.
6. Retrieve your chat ID by opening this URL in a browser, replacing the token:

```text
https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates
```

7. Store both values locally:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="123456789"
```

8. Send a test message:

```bash
python -m src.main --send-test-telegram
```

## GitHub Actions Deployment

1. Create a GitHub repository.
2. Push this project:

```bash
git init
git add .
git commit -m "Initial betting match notifier"
git branch -M main
git remote add origin <repo-url>
git push -u origin main
```

3. Keep the workflow at `.github/workflows/notify.yml`.
4. The cron schedule runs every 30 minutes between noon and midnight in Europe/Amsterdam during the June/July World Cup period. GitHub cron is UTC, so the workflow uses offset minutes in 10:00-22:00 UTC to avoid the busier `:00` and `:30` GitHub Actions schedule slots:

```yaml
- cron: "7,37 10-21 * * *"
- cron: "7 22 * * *"
```

GitHub cron may start a few minutes late or skip scheduled runs under high load. The app therefore accepts the first-alert stage from 3h15m before kickoff until 1h before kickoff for noon-through-00:59 matches, keeps overnight/early-morning first alerts eligible from 21:00 until kickoff if they have not already been sent, and accepts the lineup-alert stage from 1h before kickoff until kickoff for noon-through-00:59 matches. When the lineup alert is due, it takes precedence over a missed first alert because it contains the more complete match information.

The workflow also uses a concurrency group, so two notification runs cannot overlap. This avoids a race where two jobs could both read the old notification state before either one commits the updated `data/sent_notifications.json` file.

5. To run manually, open the GitHub repository, go to Actions, select "Notify World Cup Matches", click "Run workflow". This uses `workflow_dispatch`. Manual workflow runs force a notification for every discovered match in the next 8 hours, even if the same match/stage was already sent before.
6. Add secrets in Settings -> Secrets and variables -> Actions:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
ODDS_API_KEY
ODDS_API_SECONDARY_KEY
LINEUP_API_KEY
SPORTMONKS_API_TOKEN
```

Only the Telegram secrets are required for real sending. Odds and lineup keys are optional but needed for complete data. `ODDS_API_SECONDARY_KEY` is optional unless you want a backup The Odds API key; in GitHub, add it in the same Settings -> Secrets and variables -> Actions page, or in the `Notify World Cup Matches` Environment secrets if you keep this workflow environment-bound. `SPORTMONKS_API_TOKEN` is optional unless you want the second lineup provider. If a secret is missing, the corresponding pipeline fails gracefully and the message is still sent with the data that is available.

7. Check workflow logs in the Actions tab. The logs show pipeline success/failure independently.
8. Confirm `data/sent_notifications.json` changes after a successful real send. The workflow commits that file back to the repository.
9. If the workflow does not run, confirm Actions are enabled, the workflow file is on the default branch, and the repository is not archived.

## Configuration Reference

Environment variables:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
ODDS_API_KEY
ODDS_API_SECONDARY_KEY
LINEUP_API_KEY
SPORTMONKS_API_TOKEN
NOTIFICATION_TARGET_HOURS=3
NOTIFICATION_WINDOW_MINUTES=15
ENABLE_FIRST_NOTIFICATION=true
ENABLE_LINEUP_NOTIFICATION=true
LINEUP_NOTIFICATION_LEAD_MINUTES=45
LINEUP_NOTIFICATION_WINDOW_MINUTES=15
LOOKAHEAD_HOURS=12
TIMEZONE=Europe/Amsterdam
DRY_RUN=false
ODDS_SPORT_KEY=soccer_fifa_world_cup
ODDS_REGIONS=eu,uk
EXACT_SCORE_MARKETS=correct_score,exact_score
GOALSCORER_MARKETS=player_goal_scorer_anytime,anytime_goalscorer,goalscorer_anytime
HALF_TIME_RESULT_MARKETS=h2h_3_way_h1,h2h_h1
```

CLI arguments:

```text
--dry-run
--lookahead-hours
--match-id
--send-test-telegram
--manual-override
--force-notifications
--save-dry-run
```

## Troubleshooting

No matches found: increase `--lookahead-hours`, check the current date/time, or configure a provider key.

Telegram message not received: run `python -m src.main --send-test-telegram`, then check bot token, chat ID, and whether you started a chat with the bot.

Invalid Telegram token: regenerate the token with `@BotFather` and update the local environment variable or GitHub secret.

Invalid Telegram chat ID: call `getUpdates` after sending a message to the bot and copy the numeric chat ID.

Missing GitHub secret: add it under repository Settings -> Secrets and variables -> Actions, then rerun the workflow.

Odds API unavailable: the exact-score and goalscorer sections become unavailable, but the notification still sends.

Lineup source unavailable: the lineup section shows "Lineup source: unavailable after trying ..." and "Probable lineup unavailable", but the notification still sends.

API-Football free-plan limitation: as of the June 10, 2026 verification run, API-Football exposes the 2026 World Cup fixture by date and fixture-level odds by fixture ID, but its league-season query reports that free plans do not have access to the 2026 season and the lineup endpoint returns no lineup rows for the Mexico vs South Africa opener.

Sportmonks lineup limitation: official lineups are usually available close to kickoff. Predicted lineups before official publication require Sportmonks' paid Expected Lineups add-on, which was listed as a partner add-on in their documentation. If that add-on is not available, the app continues to Goal.com, TalkSport, and static fallbacks.

GitHub Actions workflow not running: ensure `.github/workflows/notify.yml` exists on the default branch and Actions are enabled.

Duplicate notification state problem: inspect or reset `data/sent_notifications.json`. Removing a match ID allows it to be sent again on a real run.

First alert disabled: set `ENABLE_FIRST_NOTIFICATION=false`. This leaves only the lineup alert enabled.

Lineup alert disabled: set `ENABLE_LINEUP_NOTIFICATION=false`. This leaves only the first alert stage enabled.

## Tests

```bash
pytest -q
```

All external APIs are mocked or bypassed by dry-run paths in tests.
