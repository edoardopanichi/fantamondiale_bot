from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.matches import odds_api_datetime


class Args:
    dry_run = True
    lookahead_hours = 48
    match_id = None
    send_test_telegram = False
    manual_override = True
    save_dry_run = False


def fetch_json(url: str, headers: dict[str, str] | None = None) -> tuple[int, object]:
    request = Request(url, headers=headers or {})
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload: object = json.loads(body)
        except json.JSONDecodeError:
            payload = body
        return exc.code, payload


def main() -> int:
    config = load_config(Args())
    now = datetime.now(UTC)
    later = now + timedelta(hours=48)
    print(f"Has Odds API key: {bool(config.odds_api_key)}")
    print(f"Has API-Football key: {bool(config.lineup_api_key)}")
    print(f"Window UTC: {now.isoformat()} -> {later.isoformat()}")

    if config.odds_api_key:
        sports_url = (
            "https://api.the-odds-api.com/v4/sports?"
            + urlencode({"apiKey": config.odds_api_key, "all": "true"})
        )
        status, sports = fetch_json(sports_url)
        print(f"The Odds API sports status: {status}")
        if isinstance(sports, list):
            matches = [
                {key: item.get(key) for key in ("key", "title", "active", "has_outrights")}
                for item in sports
                if "cup" in str(item.get("key", "")).lower()
                or "fifa" in str(item.get("key", "")).lower()
                or "world" in str(item.get("title", "")).lower()
            ]
            print("The Odds API relevant sports:")
            print(json.dumps(matches, indent=2))
        else:
            print(json.dumps(sports, indent=2))

        for sport_key in ("soccer_fifa_world_cup", "soccer_fifa_world_cup_winner", "upcoming"):
            events_url = (
                f"https://api.the-odds-api.com/v4/sports/{sport_key}/events?"
                + urlencode(
                    {
                        "apiKey": config.odds_api_key,
                        "dateFormat": "iso",
                        "commenceTimeFrom": odds_api_datetime(now),
                        "commenceTimeTo": odds_api_datetime(later),
                    }
                )
            )
            status, events = fetch_json(events_url)
            print(f"The Odds API events status for {sport_key}: {status}")
            if isinstance(events, list):
                print(json.dumps(events[:5], indent=2))
                for event in events[:2]:
                    event_id = event.get("id")
                    if not event_id:
                        continue
                    markets_url = (
                        f"https://api.the-odds-api.com/v4/sports/{sport_key}/events/{event_id}/markets?"
                        + urlencode({"apiKey": config.odds_api_key, "regions": config.odds_regions})
                    )
                    market_status, markets = fetch_json(markets_url)
                    print(f"The Odds API markets status for {sport_key}/{event_id}: {market_status}")
                    print(json.dumps(markets, indent=2)[:5000])
            else:
                print(json.dumps(events, indent=2))

    if config.lineup_api_key:
        headers = {"x-apisports-key": config.lineup_api_key}
        for url in (
            "https://v3.football.api-sports.io/leagues?"
            + urlencode({"search": "world cup"}),
            "https://v3.football.api-sports.io/fixtures?"
            + urlencode({"league": "1", "season": "2026", "date": "2026-06-11"}),
            "https://v3.football.api-sports.io/fixtures?"
            + urlencode({"date": "2026-06-11"}),
        ):
            status, payload = fetch_json(url, headers=headers)
            print(f"API-Football status {status}: {url.split('?')[0]}")
            if isinstance(payload, dict) and isinstance(payload.get("response"), list):
                relevant = [
                    item
                    for item in payload["response"]
                    if any(
                        needle in json.dumps(item).lower()
                        for needle in ("mexico", "south africa", "world cup")
                    )
                ]
                payload = {**payload, "response": relevant[:10], "filtered_results": len(relevant)}
            print(json.dumps(payload, indent=2)[:8000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
