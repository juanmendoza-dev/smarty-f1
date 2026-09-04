"""The background per-lap transition model, and the position-only ladder.
09 sec5.4 and 09 sec10 baseline 2.

Both are fitted from lap-level archive counts, both are fitted RACE-FORWARD
(races 1..n, applied to race n+1), and neither is ever fitted on the corpus it
is scored on (`05` sec6.1, `08` sec8).

Two things here are load-bearing and easy to get wrong:

  - **Conditioning is on `progress` as a fraction, never on laps remaining.**
    09 sec2.2 measured why: bucketing 12 races by absolute laps remaining mixes
    a 44-lap Belgian GP with a 78-lap Monaco and produces a non-monotone ladder
    that is an artifact of race length, not a fact about racing. The ladder in
    09 sec2.2's table is presented in laps-remaining buckets *for description*;
    baseline 2 is refit here on progress and the two are not interchangeable.

  - **Retirement-driven position changes are removed from the background fit**
    (09 sec5.4). 09 sec2.3's ~6%/lap already contains pit cycles, retirements
    and on-track passes mixed together. Retirement is modelled explicitly by the
    hazard, so leaving it in the background rate too is the double-count `04`
    sec6.3 rejected. Pit-cycle swaps deliberately STAY in, because 09 sec5.7
    does not model pit strategy explicitly -- that is the layer's stated
    limitation, not an omission here.
"""

import json
import math

from .circuits import multiplier_for
from .invariants import require

BANDS = (("P1-P3", 1, 3), ("P4-P6", 4, 6), ("P7-P10", 7, 10),
         ("P11-P15", 11, 15), ("P16+", 16, 99))
BAND_NAMES = tuple(b[0] for b in BANDS)

# Four progress buckets. 09 sec2.3's front band rests on 2,190 pair-observations
# and 132 swaps across twelve races; splitting that five ways by band and four
# ways by progress already puts ~30 swaps in a cell, and finer is decoration.
PROGRESS_BUCKETS = 4
# Empirical-Bayes shrinkage of a (band, progress) cell toward its band's pooled
# rate. n0 is the pair-count at which a cell carries half its own weight; at
# ~550 observations per front-band cell that is a real but not overwhelming pull.
SHRINK_N0 = 500.0
# The circuit slope is ONE fitted parameter, not twelve (09 sec5.4). m is `02`
# sec5.1's hand-set overtaking multiplier and enters as exp(c * (m - 1)):
# multiplicative, exactly 1 at m = 1, and positive for any c, where a linear
# 1 + c*(m-1) goes negative as soon as the data asks for a strong effect -- and
# it does. Monaco's measured adjacent-swap rate is about a THIRD of the m = 1.00
# circuits', which is a far larger effect than a hand-set 1.15 grid-weight bump
# implies, and clipping it at a linear form's zero would hide that.
# Ordered by distance from zero so that a tie -- which is what happens when
# every training circuit shares one value of m, as it does for the first few
# race-forward folds -- resolves to "no circuit effect" rather than to whichever
# end of the grid happened to be tried first.
CIRCUIT_SLOPE_GRID = sorted((i * 0.25 for i in range(-32, 33)), key=lambda c: (abs(c), c))


def band_of(position):
    for name, lo, hi in BANDS:
        if lo <= position <= hi:
            return name
    return BAND_NAMES[-1]


def progress_bucket(progress):
    b = int(progress * PROGRESS_BUCKETS)
    return min(max(b, 0), PROGRESS_BUCKETS - 1)


class BackgroundRate:
    """q(band, progress, circuit) -- the per-lap adjacent-pair swap probability.

    Circuit enters through `02` sec5.1's multiplier `m`, with a single fitted
    slope: q = cell_rate(band, progress) * (1 + c * (m - 1)). 09 sec5.4 requires
    this shape rather than a free per-circuit parameter, and the by-product is
    the per-circuit residual against `m` that pays down `02` sec10 item 1.
    """

    def __init__(self, cells, band_pooled, slope, per_circuit=None, meta=None):
        self.cells = cells                # (band, bucket) -> rate
        self.band_pooled = band_pooled    # band -> rate
        self.slope = slope
        self.per_circuit = per_circuit or {}
        self.meta = meta or {}

    def rate(self, position, progress, m=1.0):
        cell = self.cells.get((band_of(position), progress_bucket(progress)))
        if cell is None:
            cell = self.band_pooled.get(band_of(position), 0.06)
        q = cell * math.exp(self.slope * (m - 1.0))
        return min(max(q, 0.0), 0.5)

    def slot_rates(self, n_slots, progress, m=1.0):
        """q for every adjacent pair (slot k, slot k+1), k = 0..n_slots-2.
        The band is taken from the pair's leading position, 1-indexed."""
        return [self.rate(k + 1, progress, m) for k in range(n_slots - 1)]

    def as_dict(self):
        return {
            "cells": {"%s|%d" % k: v for k, v in sorted(self.cells.items())},
            "band_pooled": self.band_pooled,
            "slope": self.slope,
            "per_circuit": self.per_circuit,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d):
        cells = {}
        for k, v in d["cells"].items():
            band, bucket = k.rsplit("|", 1)
            cells[(band, int(bucket))] = v
        return cls(cells, d["band_pooled"], d["slope"], d.get("per_circuit"), d.get("meta"))


def swap_observations(order_by_lap, retired_lap_by_code, total_laps):
    """Adjacent-pair observations for one race.

    `order_by_lap`: {lap -> {position -> code}} from the archive.
    `retired_lap_by_code`: {code -> last lap completed} for cars that retired.

    Yields (band-leading-position, progress, swapped) per adjacent pair per lap,
    EXCLUDING any pair where either car retires within the lap -- 09 sec5.4's
    double-count guard, which is the difference between this and the raw 6%
    09 sec2.3 reports.
    """
    out = []
    for lap in range(1, total_laps):
        a, b = order_by_lap.get(lap), order_by_lap.get(lap + 1)
        if not a or not b:
            continue
        progress = lap / float(total_laps)
        where = {code: pos for pos, code in b.items()}
        for pos in sorted(a):
            if pos + 1 not in a:
                continue
            d1, d2 = a[pos], a[pos + 1]
            r1, r2 = retired_lap_by_code.get(d1), retired_lap_by_code.get(d2)
            if (r1 is not None and r1 <= lap + 1) or (r2 is not None and r2 <= lap + 1):
                continue
            p1, p2 = where.get(d1), where.get(d2)
            if p1 is None or p2 is None:
                continue
            out.append((pos, progress, 1 if p2 < p1 else 0))
    return out


def fit_background(races):
    """`races`: [{"circuit_id":.., "observations": [(pos, progress, swapped)]}].

    Fitted by counting, then shrinking each (band, progress) cell toward its
    band's pooled rate, then choosing the single circuit slope that maximises
    the Bernoulli log-likelihood of the pooled observations. One parameter for
    circuit, per 09 sec5.4.
    """
    require(races, "fit_background: no training races")
    cell_n, cell_k = {}, {}
    band_n, band_k = {}, {}
    for race in races:
        for pos, progress, swapped in race["observations"]:
            key = (band_of(pos), progress_bucket(progress))
            cell_n[key] = cell_n.get(key, 0) + 1
            cell_k[key] = cell_k.get(key, 0) + swapped
            band = key[0]
            band_n[band] = band_n.get(band, 0) + 1
            band_k[band] = band_k.get(band, 0) + swapped

    require(sum(cell_n.values()) > 0, "fit_background: no adjacent-pair observations")
    band_pooled = {b: band_k[b] / band_n[b] for b in band_n}
    overall = sum(band_k.values()) / sum(band_n.values())
    cells = {}
    for key, n in cell_n.items():
        raw = cell_k[key] / n
        prior = band_pooled.get(key[0], overall)
        wgt = n / (n + SHRINK_N0)
        cells[key] = wgt * raw + (1.0 - wgt) * prior

    base = BackgroundRate(cells, band_pooled, 0.0)
    best_slope, best_ll = 0.0, None
    for c in CIRCUIT_SLOPE_GRID:
        ll = 0.0
        for race in races:
            m = multiplier_for(race["circuit_id"])
            for pos, progress, swapped in race["observations"]:
                q = base.rate(pos, progress, 1.0) * math.exp(c * (m - 1.0))
                q = min(max(q, 1e-6), 1.0 - 1e-6)
                ll += math.log(q) if swapped else math.log(1.0 - q)
        if best_ll is None or ll > best_ll:
            best_slope, best_ll = c, ll

    fitted = BackgroundRate(cells, band_pooled, best_slope, meta={
        "n_pairs": sum(cell_n.values()), "n_swaps": sum(cell_k.values()),
        "overall_rate": overall, "loglik": best_ll, "n_races": len(races)})

    # The by-product 02 sec10 item 1 is owed: observed vs predicted per circuit.
    resid = {}
    for race in races:
        cid = race["circuit_id"]
        m = multiplier_for(cid)
        n = k = 0
        pred = 0.0
        for pos, progress, swapped in race["observations"]:
            n += 1
            k += swapped
            pred += fitted.rate(pos, progress, m)
        if n:
            acc = resid.setdefault(cid, {"m": m, "n": 0, "obs": 0, "pred": 0.0})
            acc["n"] += n
            acc["obs"] += k
            acc["pred"] += pred
    for cid, acc in resid.items():
        acc["observed_rate"] = acc["obs"] / acc["n"]
        acc["predicted_rate"] = acc["pred"] / acc["n"]
        acc["ratio"] = (acc["observed_rate"] / acc["predicted_rate"]
                        if acc["predicted_rate"] > 0 else float("nan"))
    fitted.per_circuit = resid
    return fitted


class PositionLadder:
    """09 sec10 baseline 2 -- P(win) from current position and progress alone.

    "The real floor and it is a strong one" (09 sec10): the leader with ten laps
    to go wins, and that is free information. Fitted race-forward on (position
    band, progress bucket) cells with a Laplace-ish shrink toward the cell's
    band-marginal, because 8 races will not support per-position cells.

    P1 is its own band here rather than sharing 09 sec2.3's P1-P3 grouping: the
    leader is the entire signal this baseline has, and pooling him with P2 and
    P3 would hand the layer a fake win by weakening its strongest competitor.
    """

    LADDER_BANDS = (("P1", 1, 1), ("P2-P3", 2, 3), ("P4-P6", 4, 6),
                    ("P7-P10", 7, 10), ("P11+", 11, 99))
    N_BUCKETS = 10

    def __init__(self, cells, band_pooled, overall):
        self.cells, self.band_pooled, self.overall = cells, band_pooled, overall

    @classmethod
    def band_of(cls, position):
        for name, lo, hi in cls.LADDER_BANDS:
            if lo <= position <= hi:
                return name
        return cls.LADDER_BANDS[-1][0]

    @classmethod
    def bucket_of(cls, progress):
        return min(max(int(progress * cls.N_BUCKETS), 0), cls.N_BUCKETS - 1)

    @classmethod
    def fit(cls, observations):
        """`observations`: [(position, progress, won)] over the training races."""
        require(observations, "PositionLadder.fit: no observations")
        cell_n, cell_k, band_n, band_k = {}, {}, {}, {}
        for pos, progress, won in observations:
            band = cls.band_of(pos)
            key = (band, cls.bucket_of(progress))
            cell_n[key] = cell_n.get(key, 0) + 1
            cell_k[key] = cell_k.get(key, 0) + won
            band_n[band] = band_n.get(band, 0) + 1
            band_k[band] = band_k.get(band, 0) + won
        overall = sum(cell_k.values()) / sum(cell_n.values())
        band_pooled = {b: band_k[b] / band_n[b] for b in band_n}
        cells = {}
        for key, n in cell_n.items():
            prior = band_pooled.get(key[0], overall)
            wgt = n / (n + 20.0)
            cells[key] = wgt * (cell_k[key] / n) + (1.0 - wgt) * prior
        return cls(cells, band_pooled, overall)

    def raw(self, position, progress):
        key = (self.band_of(position), self.bucket_of(progress))
        if key in self.cells:
            return self.cells[key]
        return self.band_pooled.get(key[0], self.overall)

    def at(self, order, progress):
        """`order`: codes in classified order, retired cars already removed.
        Normalised across the field so it is a distribution, like every other
        arm in 09 sec10's comparison."""
        require(order, "PositionLadder.at: empty order")
        raw = {code: self.raw(i + 1, progress) for i, code in enumerate(order)}
        total = sum(raw.values())
        if total <= 0:
            return {code: 1.0 / len(order) for code in order}
        return {c: v / total for c, v in raw.items()}

    def as_dict(self):
        return {"cells": {"%s|%d" % k: v for k, v in sorted(self.cells.items())},
                "band_pooled": self.band_pooled, "overall": self.overall}

    @classmethod
    def from_dict(cls, d):
        cells = {}
        for k, v in d["cells"].items():
            band, bucket = k.rsplit("|", 1)
            cells[(band, int(bucket))] = v
        return cls(cells, d["band_pooled"], d["overall"])


def dump(obj, path):
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
