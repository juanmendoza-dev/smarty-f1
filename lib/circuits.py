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
