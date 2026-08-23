"""Shared primitives for the Phase A1 scoring function. 02-winner-prediction-algo.md sec3."""

import math

NEUTRAL = 0.5

K_GRID = 4.0
K_SPRINT = 3.5
K_FIN = 5.0


def pos_score(p, k):
    """pos_score(p, k) = exp(-(p - 1) / k). p is 1-indexed; pos_score(1, k) == 1.0."""
    return math.exp(-(p - 1) / k)


def is_classified(status):
    """sec3.4: Finished, or a lapped classification ('+1 Lap' etc), counts.
    Anything else (Retired, Accident, Engine, ...) is a DNF and scores 0.0.
    """
    return status == "Finished" or status.startswith("+")


def shrink_by_n(s, n):
    """sec4 F5 small-sample shrinkage toward NEUTRAL, by appearance count n.
    Applies identically to F5 track history and F7's wet-weather branch.
    """
    if n >= 3:
        return s
    if n == 2:
        return 0.65 * s + 0.35 * NEUTRAL
    if n == 1:
        return 0.40 * s + 0.60 * NEUTRAL
    return NEUTRAL


def field_normalize(raw_by_code):
    """Divide every value by the max observed value across the field (sec3.3).
    raw_by_code: {code: float}. Returns {code: float} with the max mapped to 1.0.
    A field of all-zero raw values maps everyone to 0.0 (max is 0 -> skip divide).
    """
    if not raw_by_code:
        return {}
    max_v = max(raw_by_code.values())
    if max_v <= 0:
        return {code: 0.0 for code in raw_by_code}
    return {code: v / max_v for code, v in raw_by_code.items()}
