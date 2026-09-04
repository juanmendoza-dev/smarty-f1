#!/usr/bin/env python3
"""Fit `delta` -- the time a pit stop costs -- per circuit and season. 12 sec5.1.

**A served constant, never a live computation.** `08` sec11.1 fixes `theta`
offline for the same reason this fixes `delta`: a live consumer sees one tick at
a time and cannot take a median over a race that has not finished. This script
is the offline half; `lib/pit_loss.py` is the serve half, and the table it
carries is what this script prints.

The measurement is `probes/12b_pit_projection.py`'s, re-implemented here rather
than imported because the probe is a one-shot report and this is a fitter: same
filter, same thresholds, same arithmetic, keyed on the circuit id the layer
itself uses (`lib/winprob_replay._circuit_id`) rather than on the event name.
The pooled figure it reproduces is 12 sec2.1's **22.8 s over 286 stops**.

**Keyed on (season, circuit)** -- the owner's call, 2026-09-04, on 12 sec9 item
2. Today the archive holds one scoreable season, so the season key changes no
number in the table; what it changes is what happens when the archive grows,
which is that 2027 gets its own row instead of being averaged into 2026's under
a regulation change. The fallback chain is season-and-circuit -> pooled, and
a circuit with fewer than MIN_STOPS measured stops takes the pooled value and
is flagged (12 sec5.1: the threshold is a stated judgement, not a measurement).

Usage:
    .venv312/bin/python pit_fit.py
    .venv312/bin/python pit_fit.py --season 2026 --out data/live/winprob/pit_loss.json
"""

import argparse
import json
import os
import statistics
import warnings

warnings.filterwarnings("ignore")

import fastf1
import pandas as pd

from lib.invariants import require
from lib.pit_loss import MIN_STOPS
from lib.winprob_replay import CACHE_DIR, _circuit_id

OUT_DEFAULT = "data/live/winprob/pit_loss.json"

# 12 sec2.1's tightened green filter, and the two numbers that define it. A
# green lap is within GREEN_BAND of the driver's own median; an in- or out-lap
# outside PIT_LAP_CAP of that median is behind a safety car rather than serving
# a green stop, and 12 sec5.3 refuses to project under caution anyway, so it is
# not a pit loss this model should learn.
GREEN_BAND = 1.15
PIT_LAP_CAP = 1.45
MIN_GREEN_LAPS = 8
# A delta outside this is not a stop: below it the lap pair is noise, above it
# the car served a penalty or stopped twice.
DELTA_MIN_S, DELTA_MAX_S = 5.0, 40.0


def _sec(td):
    return td.total_seconds() if pd.notna(td) else None


def mad(vals):
    """Median absolute deviation, scaled to compare with a standard deviation
    on normal data. Robust to the one caution lap an IQR still lets through."""
    if len(vals) < 2:
        return float("nan")
    med = statistics.median(vals)
    return 1.4826 * statistics.median([abs(v - med) for v in vals])


def deltas_for_session(session):
    """Every measurable green pit loss in one race, in seconds."""
    laps = session.laps
    if laps is None or laps.empty:
        return []
    out = []
    for _, dl in laps.groupby("Driver"):
        dl = dl.sort_values("LapNumber")
        green = [_sec(t) for t, pi, po
                 in zip(dl["LapTime"], dl["PitInTime"], dl["PitOutTime"])
                 if pd.isna(pi) and pd.isna(po) and _sec(t) is not None]
        if len(green) < MIN_GREEN_LAPS:
            continue
        base = statistics.median(green)
        green = [g for g in green if g < base * GREEN_BAND]
        if len(green) < MIN_GREEN_LAPS:
            continue
        base = statistics.median(green)
        for _, row in dl.iterrows():
            if pd.isna(row["PitInTime"]):
                continue
            lap = int(row["LapNumber"])
            inlap = _sec(row["LapTime"])
            nxt = dl[dl["LapNumber"] == lap + 1]
            outlap = _sec(nxt["LapTime"].iloc[0]) if len(nxt) else None
            if inlap is None or outlap is None:
                continue
            if inlap > base * PIT_LAP_CAP or outlap > base * PIT_LAP_CAP:
                continue
            d = (inlap - base) + (outlap - base)
            if DELTA_MIN_S < d < DELTA_MAX_S:
                out.append(d)
    return out


def fit(season, rounds=None, verbose=True):
    fastf1.Cache.enable_cache(CACHE_DIR)
    sched = fastf1.get_event_schedule(season, include_testing=False)
    now = pd.Timestamp.utcnow().tz_localize(None)
    by_circuit = {}
    names = {}
    for _, ev in sched.iterrows():
        rnd = int(ev["RoundNumber"])
        if rounds and rnd not in rounds:
            continue
        # A round that has not happened yet has nothing to fit and asking for it
        # is a network call for data that does not exist. The schedule already
        # says which those are, so the fitter reads it rather than discovering
        # the answer from a failed load.
        when = ev.get("EventDate")
        if pd.notna(when) and pd.Timestamp(when) > now:
            continue
        try:
            s = fastf1.get_session(season, rnd, "R")
            s.load(telemetry=False, laps=True, weather=False, messages=False)
            ds = deltas_for_session(s)
        except Exception as e:                      # noqa: BLE001
            if verbose:
                print("  R%-2d skip (%s)" % (rnd, type(e).__name__))
            continue
        if not ds:
            continue
        cid = _circuit_id(s)
        by_circuit.setdefault(cid, []).extend(ds)
        names[cid] = str(ev["EventName"])
        if verbose:
            print("  R%-2d %-26s %-16s n=%3d median=%5.1f"
                  % (rnd, str(ev["EventName"])[:26], cid, len(ds),
                     statistics.median(ds)))

    pooled = [d for v in by_circuit.values() for d in v]
    require(pooled, "pit_fit: no measurable stops in season %d" % season)
    table = {}
    for cid, v in sorted(by_circuit.items()):
        # Stored unrounded. Rounding here and again at display is a double
        # round, and it is not cosmetic: Canada's median is 28.154, which goes
        # to 28.2 in one step and to 28.1 in two -- a table that no longer
        # matches 12 sec2.1's published one for no reason a reader could see.
        table[cid] = {"n": len(v), "delta": float(statistics.median(v)),
                      "mad": float(mad(v)), "name": names.get(cid, cid),
                      "thin": len(v) < MIN_STOPS}
    return {"season": season, "circuits": table,
            "pooled": {"n": len(pooled), "delta": float(statistics.median(pooled)),
                       "mad": float(mad(pooled))},
            "filter": {"green_band": GREEN_BAND, "pit_lap_cap": PIT_LAP_CAP,
                       "min_green_laps": MIN_GREEN_LAPS,
                       "delta_range_s": [DELTA_MIN_S, DELTA_MAX_S],
                       "min_stops": MIN_STOPS}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--rounds", default=None)
    ap.add_argument("--out", default=OUT_DEFAULT)
    args = ap.parse_args()
    rounds = [int(x) for x in args.rounds.split(",")] if args.rounds else None

    print("fitting delta for %d (12 sec2.1's tightened green filter)" % args.season)
    blob = fit(args.season, rounds)

    print("\n%-18s %-26s %5s %8s %8s %6s" % ("circuit", "race", "n", "delta",
                                             "MAD", "thin"))
    for cid, row in sorted(blob["circuits"].items(), key=lambda kv: kv[1]["delta"]):
        print("%-18s %-26s %5d %8.1f %8.1f %6s"
              % (cid, row["name"][:26], row["n"], row["delta"], row["mad"],
                 "YES" if row["thin"] else ""))
    p = blob["pooled"]
    print("%-18s %-26s %5d %8.1f %8.1f" % ("-- POOLED --", "", p["n"],
                                           p["delta"], p["mad"]))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(blob, fh, indent=1, sort_keys=True)
    print("\nwrote %s" % args.out)
    print("\nlib/pit_loss.py's served table, for pasting if it has drifted:")
    print("DELTA_TABLE = {")
    for cid, row in sorted(blob["circuits"].items()):
        print("    (%d, %-18s): (%5.1f, %5.1f, %3d),"
              % (blob["season"], '"%s"' % cid, row["delta"], row["mad"], row["n"]))
    print("}")


if __name__ == "__main__":
    main()
