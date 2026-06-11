from __future__ import annotations

from .models import RankedOutcome
from .probability import average, implied_probability


def rank_goalscorers(outcomes: list[dict], limit: int = 4) -> list[RankedOutcome]:
    grouped: dict[str, list[tuple[float, str]]] = {}
    for outcome in outcomes:
        side = str(outcome.get("name") or "").strip().lower()
        if side in {"no", "under"}:
            continue
        name = str(outcome.get("description") or outcome.get("name") or "").strip()
        if not name or name.lower() in {"yes", "over"}:
            continue
        if not name:
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
