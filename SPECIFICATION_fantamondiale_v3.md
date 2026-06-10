# Betting Match Notifier

## Goal

Build a Python application that automatically sends a Telegram message approximately 3 hours before each FIFA World Cup match.

Before implementation starts, the application must perform an initial research phase to identify:

- Reliable sources for probable lineups (Italian or English)
- Reliable sources for betting odds
- APIs preferred over scraping whenever available
- Betting providers or odds aggregators with stable and script-friendly access

The application must be designed so that every information pipeline operates independently. Failure of one pipeline must never stop the others.

Example:

- Probable lineup retrieval fails → continue
- Exact score prediction retrieval fails → continue
- Goalscorer retrieval fails → continue
- Telegram notification still sent with available data

The message should contain:

- Match details
- Probable lineups
- Top 3 most likely exact scores based on betting odds
- Top 3 most likely goalscorers based on betting odds
- Odds sources used
- Lineup source used

The application must run automatically using GitHub Actions and must not require a continuously running server.

---

# Functional Requirements

## Source Discovery Layer

Before implementing data providers, Codex shall perform a deep inspection of the web and evaluate:

### Probable Lineup Sources

Potential examples:

- Rotowire
- Sports Mole
- Transfermarkt
- Goal.com
- Fantacalcio
- Sky Sport
- Gazzetta dello Sport
- Other reliable lineup providers

The implementation shall select the source that provides the most reliable and consistently structured lineup information.

Italian and English sources are both acceptable.

### Odds Sources

Potential examples:

- The Odds API
- OddsAPI.io
- Bet365
- Betfair
- Pinnacle
- William Hill
- Unibet
- Other accessible bookmakers or aggregators

Preference order:

1. Official API
2. Odds aggregation API
3. Stable structured endpoint
4. Scraping only if no better alternative exists

The selected source and rationale must be documented in README.md.

---

## Match Discovery

The system shall retrieve upcoming World Cup matches.

Each match must contain:

```python
@dataclass
class Match:
    id: str
    home_team: str
    away_team: str
    kickoff_time_utc: datetime
```

The implementation must allow future replacement of the match source without modifying the rest of the system.

Possible future sources:

- Football-Data API
- API-Football
- The Odds API
- Other fixtures providers

---

## Notification Timing

The system shall notify users approximately 3 hours before kickoff.

Because GitHub Actions is not guaranteed to start exactly on schedule, use a notification window.

Default configuration:

```text
Target: 3 hours before kickoff

Window:
2h45m <= time_until_kickoff <= 3h15m
```

---

## Manual Execution and Testing Mode

The repository must support both automatic execution through GitHub Actions and manual execution from a local machine.

The script must be runnable manually multiple times without breaking duplicate-prevention logic.

Example commands:

```bash
python -m src.main
python -m src.main --dry-run
python -m src.main --lookahead-hours 24
python -m src.main --lookahead-hours 48 --dry-run
```

The `--lookahead-hours` argument shall define how far forward the script searches for matches.

Default:

```text
lookahead-hours = 3
```

Expected behavior if no match is found:

```text
No match present in the selected lookahead window.
```

Example:

```bash
python -m src.main --lookahead-hours 24
```

This should allow the user to check whether the application can detect matches scheduled later, for example tomorrow's matches, and verify that the lineup, exact score odds, goalscorer odds, formatting, and Telegram sending pipelines work.

The script must also support:

```bash
python -m src.main --match-id MATCH_ID --dry-run
```

if the chosen fixture provider makes stable match IDs available.

Manual runs must clearly print:

```text
Matches found
Matches skipped
Lineup pipeline result
Exact score pipeline result
Goalscorer pipeline result
Telegram send result
Errors, if any
```

Dry runs must not save the match as notified unless explicitly configured.

---

## Independent Data Pipelines

Each information source shall be retrieved independently.

Required pipelines:

1. Match information
2. Probable lineups
3. Exact score probabilities
4. Goalscorer probabilities
5. Telegram notification

Each pipeline must:

```python
class PipelineResult:
    success: bool
    data: Any
    error: str | None
```

Example flow:

```text
Get Match
      ↓
Get Lineups
      ↓
Failure?
      ↓
Yes → Log error and continue

Get Exact Scores
      ↓
Failure?
      ↓
Yes → Log error and continue

Get Goalscorers
      ↓
Failure?
      ↓
Yes → Log error and continue

Send Telegram Message
```

---

## Probable Lineups

For each match retrieve:

```python
home_predicted_lineup
away_predicted_lineup
```

Desired format:

```text
Italy

Donnarumma
Di Lorenzo
Bastoni
Calafiori
Dimarco
Barella
Tonali
Frattesi
Chiesa
Retegui
Zaccagni
```

If lineup information is unavailable:

```text
Probable lineup unavailable
```

The notification must still be sent.

---

## Odds Collection

Retrieve odds from multiple configured sources.

Preferred implementation:

- Odds aggregation API

Avoid scraping bookmaker websites whenever possible.

Supported markets:

### Exact Score

Examples:

```text
0-0
1-0
1-1
2-1
3-1
```

### Anytime Goalscorer

```text
Anytime Goalscorer
```

---

## Probability Calculation

Convert decimal odds into implied probability.

Formula:

```python
probability = 1 / decimal_odds
```

---

## Exact Score Ranking

The system shall rank exact score outcomes by average implied probability.

Example:

```text
1. 1-1 (14.3%)
2. 2-1 (11.1%)
3. 1-0 (10.0%)
```

Return only the top 3 exact scores.

The application must not use:

```text
Home Win
Draw
Away Win
```

as final predictions.

The objective is to identify the most likely exact scorelines.

---

## Goalscorer Ranking

Rank all available players by average implied probability.

Return only the top 3.

Example:

```text
1. Mbappé
2. Dembélé
3. Griezmann
```

---

## Telegram Notification

Send one Telegram message per match.

Example:

```text
🏆 World Cup Match Alert

Match:
Italy vs Germany

Kickoff:
18:00 CET

Probable Lineups

Italy
...
...

Germany
...
...

Most Likely Exact Scores

1. 1-1 (14.3%)
2. 2-1 (11.1%)
3. 1-0 (10.0%)

Most Likely Goalscorers

1. Retegui (39%)
2. Frattesi (34%)
3. Musiala (30%)

Lineup Source:
Fantacalcio

Odds Sources:
The Odds API
Bookmaker A
Bookmaker B
```
---

# Duplicate Prevention

A match must never generate more than one notification.

Store notified match IDs in:

```text
data/sent_notifications.json
```

---

# README Requirements

The repository must include a clear and practical `README.md`.

The README must be written for a user who has never used GitHub Actions before.

It must include:

## General Explanation

Explain:

- What the application does
- When it runs
- Which information it sends to Telegram
- How probable lineups are retrieved
- How exact score odds are retrieved
- How goalscorer odds are retrieved
- How the independent pipelines work
- What happens when one pipeline fails
- Where logs and notification state are stored

## Local Setup

Include step-by-step instructions for:

```bash
git clone <repo-url>
cd betting-match-notifier
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For Windows, include the equivalent activation command:

```bash
.venv\Scripts\activate
```

## Manual Usage

Document how to run the script manually:

```bash
python -m src.main
python -m src.main --dry-run
python -m src.main --lookahead-hours 24
python -m src.main --lookahead-hours 48 --dry-run
```

Explain that `--lookahead-hours` can be used to test future matches even when no match is happening in the next 3 hours.

Example expected output:

```text
No match present in the selected lookahead window.
```

The README must explain how to use manual mode to verify:

- Match discovery
- Probable lineup retrieval
- Exact score odds retrieval
- Goalscorer odds retrieval
- Telegram message formatting
- Telegram message sending

## Telegram Bot Setup

Unless Codex implements automatic Telegram bot creation, the README must include detailed manual setup instructions.

The README must explain how to:

1. Open Telegram
2. Search for `@BotFather`
3. Create a new bot with `/newbot`
4. Copy the generated bot token
5. Start a chat with the new bot
6. Retrieve the Telegram chat ID
7. Store both values as environment variables or GitHub secrets

Required variables:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

The README must also include a quick test command or script command to send a test Telegram message.

Example:

```bash
python -m src.main --send-test-telegram
```

## GitHub Actions Deployment

The README must include detailed deployment instructions for GitHub Actions.

It must explain:

1. How to create a GitHub repository
2. How to push the project to GitHub
3. Where to place `.github/workflows/notify.yml`
4. How the cron schedule works
5. How to manually trigger the workflow using `workflow_dispatch`
6. How to add GitHub Actions secrets
7. How to check workflow logs
8. How to confirm that `data/sent_notifications.json` is updated
9. How to troubleshoot common failures

Required GitHub secrets:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
ODDS_API_KEY
LINEUP_API_KEY
```

Only the keys actually required by the selected providers shall be mandatory.

The README must explain that no server, VPS, Docker container, or always-on process is required.

## Configuration Reference

Document all supported configuration values:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
ODDS_API_KEY
LINEUP_API_KEY
NOTIFICATION_TARGET_HOURS
NOTIFICATION_WINDOW_MINUTES
LOOKAHEAD_HOURS
TIMEZONE
DRY_RUN
```

Document equivalent CLI arguments where available:

```text
--dry-run
--lookahead-hours
--match-id
--send-test-telegram
```

## Troubleshooting

Include troubleshooting guidance for:

```text
No matches found
Telegram message not received
Invalid Telegram token
Invalid Telegram chat ID
Missing GitHub secret
Odds API unavailable
Lineup source unavailable
GitHub Actions workflow not running
Duplicate notification state problem
```

---

# Repository Structure

```text
betting-match-notifier/
│
├── .github/
│   └── workflows/
│       └── notify.yml
│
├── src/
│   ├── main.py
│   ├── matches.py
│   ├── lineup_provider.py
│   ├── odds.py
│   ├── score_predictions.py
│   ├── goalscorers.py
│   ├── probability.py
│   ├── telegram_client.py
│   ├── storage.py
│   ├── formatter.py
│   ├── source_discovery.py
│   ├── config.py
│   └── cli.py
│
├── data/
│   └── sent_notifications.json
│
├── tests/
│
├── requirements.txt
├── README.md
└── .gitignore
```

---

# Environment Variables

Required:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Optional:

```text
ODDS_API_KEY
LINEUP_API_KEY

NOTIFICATION_TARGET_HOURS=3
NOTIFICATION_WINDOW_MINUTES=15
LOOKAHEAD_HOURS=3
TIMEZONE=Europe/Amsterdam
DRY_RUN=false
```

---

# GitHub Actions

Create:

```text
.github/workflows/notify.yml
```

Schedule:

```yaml
on:
  schedule:
    - cron: "*/30 * * * *"

  workflow_dispatch:
```

The workflow shall:

1. Checkout repository
2. Install dependencies
3. Execute notifier
4. Save notification state
5. Commit updated state file

---

# Main Application Flow

```python
def main():
    config = load_config_from_env_and_cli()

    matches = get_upcoming_matches(
        lookahead_hours=config.lookahead_hours
    )

    if not matches:
        log("No match present in the selected lookahead window.")
        return

    for match in matches:

        if already_notified(match.id) and not config.dry_run:
            continue

        if not inside_notification_window(match) and not config.manual_override:
            continue

        lineup_result = run_lineup_pipeline(match)

        exact_score_result = run_exact_score_pipeline(match)

        goalscorer_result = run_goalscorer_pipeline(match)

        message = format_message(
            match=match,
            lineup_result=lineup_result,
            exact_score_result=exact_score_result,
            goalscorer_result=goalscorer_result
        )

        telegram_result = send_telegram(message)

        if telegram_result.success and not config.dry_run:
            save_notified_match(match.id)
```

---

# Error Handling

The application must gracefully handle:

```text
Missing Telegram token
Missing API key
Network failure
API timeout
No odds available
No lineup available
No matches available
Corrupted notification file
Telegram send failure
```

Failures in one pipeline must never stop the remaining pipelines.

---

# Logging

Log at minimum:

```text
Application started
Source discovery completed
Matches retrieved
Lineups retrieved
Lineups failed
Exact score odds retrieved
Goalscorer odds retrieved
Telegram message sent
Telegram message failed
Notification state saved
Application finished
```

---

# Tests

Implement automated tests for:

```text
Probability calculation
Exact score ranking
Goalscorer ranking
Notification window logic
Duplicate prevention
Telegram message formatting
Pipeline isolation
Manual CLI execution
Lookahead-hours behavior
Dry-run behavior
Telegram test message command
README deployment instructions presence
```

Mock all external APIs.

---

# Deployment

Deployment target:

```text
GitHub Actions
```

No VPS.

No Docker required.

No continuously running service required.

Expected runtime:

```text
< 60 seconds per execution
```

---

# Future Improvements

- Multiple competitions
- Multiple Telegram recipients
- Injury analysis
- Team form analysis
- Historical statistics
- AI-assisted score prediction
- Daily summary messages
- Web dashboard
- SQLite/PostgreSQL persistence
