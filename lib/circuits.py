"""Track overtaking multiplier m, per 02-winner-prediction-algo.md sec5.1.

Hand-set judgements, not measurements (flagged as an open item in the spec for
replacement with real overtake data in Phase A3). Keyed by Jolpica circuitId.
Anything not listed defaults to 1.00.
"""

OVERTAKING_MULTIPLIER = {
    # m = 1.15 -- position hard to change
    "zandvoort": 1.15,
    "monaco": 1.15,
    "hungaroring": 1.15,
    "marina_bay": 1.15,  # Singapore
    "imola": 1.15,       # not on the 2026 calendar; kept for completeness

    # m = 1.00 -- default, listed explicitly per spec sec5.1
    "silverstone": 1.00,
    "suzuka": 1.00,
    "catalunya": 1.00,   # Barcelona
    "americas": 1.00,    # Austin (COTA)
    "albert_park": 1.00, # Melbourne

    # m = 0.85 -- position easy to change
    "monza": 0.85,
    "baku": 0.85,
    "jeddah": 0.85,
    "spa": 0.85,
    "interlagos": 0.85,
}


def multiplier_for(circuit_id):
    return OVERTAKING_MULTIPLIER.get(circuit_id, 1.00)


# 05-trained-model.md sec3.5 / sec10 item 2: the fitted tier interaction needs
# a tier label, not the multiplier value itself. Circuits missing from
# OVERTAKING_MULTIPLIER (17 of the corpus's 32, added by the sec5 backfill)
# get "default" here too, deliberately -- assigning them "hard" or "easy" by
# hand would re-import the judgement the interaction exists to test. This is
# the explicit "default tier" bucket sec3.5 calls for, not a guess.
TIER_HARD = "hard"     # m = 1.15, position hard to change
TIER_DEFAULT = "default"  # m = 1.00, and every circuit not in the table
TIER_EASY = "easy"     # m = 0.85, position easy to change

_TIER_BY_MULTIPLIER = {1.15: TIER_HARD, 0.85: TIER_EASY}


def tier_for(circuit_id):
    return _TIER_BY_MULTIPLIER.get(OVERTAKING_MULTIPLIER.get(circuit_id), TIER_DEFAULT)
