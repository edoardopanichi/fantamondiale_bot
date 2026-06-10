from __future__ import annotations


def implied_probability(decimal_odds: float) -> float:
    if decimal_odds <= 0:
        raise ValueError("decimal odds must be greater than zero")
    return 1 / decimal_odds


def average(values: list[float]) -> float:
    if not values:
        raise ValueError("cannot average an empty list")
    return sum(values) / len(values)
