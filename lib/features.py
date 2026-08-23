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
    """sec3.4: Finished, or a lapped classification, counts as classified.

    Two status text conventions cover the same thing: older Jolpica/Ergast data
    spells a lapped finish as '+1 Lap' / '+2 Laps' (a lap-count suffix), while
    the 2026 season collapses this to the literal string 'Lapped' -- confirmed
    live against Jolpica's own /status.json (statusId 143, 'Lapped', 87 rows in
    2026 so far) and against 2026 R1/R12 results, which show cars in P7-P17
    finishing genuinely classified (they scored points and started the next
    round) under this status. Checking only the '+' prefix silently treats
    every 2026 lapped finisher as a DNF. Anything else (Retired, Accident,
    Engine, Did not start, ...) is a real DNF and scores 0.0.
    """
    return status == "Finished" or status == "Lapped" or status.startswith("+")


def shrink_by_n(s, n, prior=NEUTRAL):
    """sec4 F5 small-sample shrinkage toward a prior, by appearance count n.
    Applies identically to F5 track history and F7's wet-weather branch, both
    of which use the default prior=NEUTRAL (a normalized position score, where
    0.5 is a genuine "middle of the field" prior).

    04-outcome-expansion-algo.md sec4/sec5.1 reuses this same blend table for
    DNF probability with an explicit prior= the field's own average DNF rate
    this season -- NEUTRAL=0.5 would mean "50% chance of not finishing," which
    is nonsense as a prior for an unproven driver/team, unlike for a position
    score where 0.5 is the honest middle.
    """
    if n >= 3:
        return s
    if n == 2:
        return 0.65 * s + 0.35 * prior
    if n == 1:
        return 0.40 * s + 0.60 * prior
    return prior


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
