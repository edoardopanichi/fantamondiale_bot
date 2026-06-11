from __future__ import annotations

import re

from .models import RankedOutcome
from .probability import average, implied_probability

_SCORE_RE = re.compile(r"^\s*\d+\s*[-:]\s*\d+\s*$")


def normalize_score_name(name: str) -> str:
    return name.strip().replace(":", "-").replace(" ", "")


def rank_exact_scores(outcomes: list[dict], limit: int = 4) -> list[RankedOutcome]:
    grouped: dict[str, list[tuple[float, str]]] = {}
    for outcome in outcomes:
        name = normalize_score_name(str(outcome.get("name", "")))
        if not _SCORE_RE.match(name):
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
