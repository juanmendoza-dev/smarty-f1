"""`delta` -- the time a pit stop costs, served as a constant. 12 sec5.1.

**Fitted offline, read at serve time, never computed from the race in progress.**
That is `08` sec11.1's rule for `theta` and it holds here for the same reason: a
live consumer sees one tick at a time and cannot take a median over a race that
has not finished. `pit_fit.py` is the fitter; this module is the serve half and
the table below is what it printed.

**Keyed on (season, circuit)** -- the owner's decision of 2026-09-04 on 12 sec9
item 2. The archive holds one scoreable season, so today the season key changes
no number; what it buys is that a 2027 regulation change gets its own row rather
than being averaged into 2026's. It is deliberately NOT a per-circuit *trend*:
286 stops over 12 races does not support one (12 sec2.1).

**The fallback chain, and what gets flagged.** A circuit with no measured delta,
or with fewer than `MIN_STOPS` of them, falls back to the pooled figure and the
projection it produces carries `flagged = True`. China (n = 4) is the case that
exists today. MIN_STOPS is a stated judgement, not a measurement (12 sec5.1).

The table is a checked-in constant rather than a gitignored artifact, on the
`08` `THETA` precedent: it is twelve fitted aggregates, already published in
`docs/12` sec2.1, not the derived per-lap timing data `03` sec11.2 keeps out of
a public repo.
"""

from .invariants import require

# 12 sec5.1: below this many measured stops a circuit takes the pooled value.
MIN_STOPS = 10

# 12 sec2.1's pooled figure over 286 stops, tightened green filter.
POOLED_DELTA_S = 22.8
POOLED_MAD_S = 3.7
POOLED_N = 286

# (season, circuit_id) -> (delta_s, mad_s, n_stops). Circuit ids are
# `lib/winprob_replay._circuit_id`'s, so the layer and the table share one key
# space rather than joining on an event name.
DELTA_TABLE = {
    (2026, "albert_park"):   (26.0,  7.1,  12),
    (2026, "catalunya"):     (25.2,  1.7,  42),
    (2026, "hungaroring"):   (22.9,  2.4,  45),
    (2026, "miami"):         (19.4,  0.9,  19),
    (2026, "monaco"):        (22.5,  2.2,  19),
    (2026, "red_bull_ring"): (22.0,  1.9,  36),
    (2026, "shanghai"):      (28.3,  5.0,   4),
    (2026, "silverstone"):   (21.8,  5.1,  29),
    (2026, "spa"):           (25.7,  7.5,  16),
    (2026, "suzuka"):        (24.4,  2.2,  10),
    (2026, "villeneuve"):    (28.2,  7.8,  17),
    (2026, "zandvoort"):     (19.6,  1.9,  37),
}


class PitLoss:
    """One circuit's served delta, and whether it is the circuit's own."""

    __slots__ = ("delta_s", "mad_s", "n_stops", "flagged", "season", "circuit_id")

    def __init__(self, delta_s, mad_s, n_stops, flagged, season, circuit_id):
        require(delta_s >= 0.0,
                "12 sec7 assertion 2: a negative delta would hand a stopping car "
                "places it did not earn (%s/%s = %r)" % (season, circuit_id, delta_s))
        self.delta_s = float(delta_s)
        self.mad_s = float(mad_s)
        self.n_stops = int(n_stops)
        self.flagged = bool(flagged)
        self.season = season
        self.circuit_id = circuit_id

    def __repr__(self):
        return ("PitLoss(%s/%s delta=%.1fs mad=%.1f n=%d%s)"
                % (self.season, self.circuit_id, self.delta_s, self.mad_s,
                   self.n_stops, " FLAGGED" if self.flagged else ""))


def delta_for(season, circuit_id, table=None):
    """The served delta for one circuit, with 12 sec5.1's fallback.

    Falls back to the pooled figure -- flagged -- when the circuit is absent
    from the table or was measured on fewer than MIN_STOPS stops.
    """
    tbl = DELTA_TABLE if table is None else table
    row = tbl.get((season, circuit_id))
    if row is None or row[2] < MIN_STOPS:
        return PitLoss(POOLED_DELTA_S, POOLED_MAD_S,
                       row[2] if row else 0, True, season, circuit_id)
    return PitLoss(row[0], row[1], row[2], False, season, circuit_id)


def table_from_fit(blob):
    """The `pit_fit.py` artifact, in DELTA_TABLE's shape."""
    season = int(blob["season"])
    return {(season, cid): (row["delta"], row["mad"], row["n"])
            for cid, row in blob["circuits"].items()}
