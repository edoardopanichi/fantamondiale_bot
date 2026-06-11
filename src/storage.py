from __future__ import annotations

import json
from pathlib import Path

STATE_PATH = Path("data/sent_notifications.json")
DEFAULT_STAGE = "first"


def load_notified(path: Path = STATE_PATH) -> set[str]:
    return {
        key.removesuffix(f":{DEFAULT_STAGE}") if key.endswith(f":{DEFAULT_STAGE}") else key
        for key in load_notified_keys(path)
        if key.endswith(f":{DEFAULT_STAGE}") or ":" not in key
    }


def load_notified_keys(path: Path = STATE_PATH) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    if not isinstance(payload, list):
        return set()
    return {str(item) for item in payload}


def notification_key(match_id: str, stage: str = DEFAULT_STAGE) -> str:
    return f"{match_id}:{stage}"


def _normalize_stage_path(stage: str | Path, path: Path) -> tuple[str, Path]:
    if isinstance(stage, Path):
        return DEFAULT_STAGE, stage
    return stage, path


def already_notified(match_id: str, stage: str | Path = DEFAULT_STAGE, path: Path = STATE_PATH) -> bool:
    stage, path = _normalize_stage_path(stage, path)
    keys = load_notified_keys(path)
    return notification_key(match_id, stage) in keys or (stage == DEFAULT_STAGE and match_id in keys)


def save_notified_match(match_id: str, stage: str | Path = DEFAULT_STAGE, path: Path = STATE_PATH) -> None:
    stage, path = _normalize_stage_path(stage, path)
    notified = load_notified_keys(path)
    notified.discard(match_id)
    notified.add(notification_key(match_id, stage))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(notified), indent=2) + "\n", encoding="utf-8")
