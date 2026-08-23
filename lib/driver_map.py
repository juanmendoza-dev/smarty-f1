"""Polymarket groupItemTitle -> FIA three-letter code.

Polymarket exposes no driver code (01-data-pipeline.md sec8.2), so this table is
unavoidable and must be maintained by hand. Seeded 2026-08-22 from the live
f1-dutch-grand-prix-winner-2026-08-23 event's actual market names (2026 grid plus
reserve/substitute drivers who had an active market). Jolpica's own full-name
fields do NOT reliably match these strings (Jolpica: "Andrea Kimi Antonelli",
Polymarket: "Kimi Antonelli"; Jolpica: "Carlos Sainz", Polymarket: "Carlos Sainz
Jr.") so this cannot be derived automatically from Jolpica's driver list --
per sec8.2, an unmapped name must fail loudly rather than be silently dropped.
"""

POLYMARKET_NAME_TO_CODE = {
    "Lando Norris": "NOR",
    "George Russell": "RUS",
    "Kimi Antonelli": "ANT",
    "Oscar Piastri": "PIA",
    "Lewis Hamilton": "HAM",
    "Charles Leclerc": "LEC",
    "Max Verstappen": "VER",
    "Liam Lawson": "LAW",
    "Gabriel Bortoleto": "BOR",
    "Arvid Lindblad": "LIN",
    "Pierre Gasly": "GAS",
    "Yuki Tsunoda": "TSU",
    "Nico Hulkenberg": "HUL",
    "Franco Colapinto": "COL",
    "Esteban Ocon": "OCO",
    "Alexander Albon": "ALB",
    "Carlos Sainz Jr.": "SAI",
    "Fernando Alonso": "ALO",
    "Lance Stroll": "STR",
    "Oliver Bearman": "BEA",
    "Valtteri Bottas": "BOT",
    "Sergio Perez": "PER",
    "Isack Hadjar": "HAD",
}

# Polymarket pseudo-outcomes that are not a driver and must never be run through
# the mapping table. Kept as a distinct, unmapped pseudo-entry per sec8.2.
POLYMARKET_NON_DRIVER_OUTCOMES = {"Other", "Driver A", "Driver B", "Driver C", "Driver D", "Driver E"}


def polymarket_name_to_code(name):
    if name in POLYMARKET_NON_DRIVER_OUTCOMES:
        return None
    if name not in POLYMARKET_NAME_TO_CODE:
        raise KeyError(
            f"Polymarket driver name {name!r} has no entry in POLYMARKET_NAME_TO_CODE -- "
            "add it rather than silently dropping this driver (01-data-pipeline.md sec8.2)"
        )
    return POLYMARKET_NAME_TO_CODE[name]
