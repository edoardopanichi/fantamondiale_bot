from __future__ import annotations

import json
from pathlib import Path

STATE_PATH = Path("data/sent_notifications.json")


def load_notified(path: Path = STATE_PATH) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    if not isinstance(payload, list):
        return set()
    return {str(item) for item in payload}


def already_notified(match_id: str, path: Path = STATE_PATH) -> bool:
    return match_id in load_notified(path)


def save_notified_match(match_id: str, path: Path = STATE_PATH) -> None:
    notified = load_notified(path)
    notified.add(match_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(notified), indent=2) + "\n", encoding="utf-8")
