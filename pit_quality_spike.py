"""Spike: does a driver's/team's recent pit-stop execution speed predict this race's result?

Same discipline as the weather spikes -- test before designing. Hypothesis: a
crew that executes fast, consistent stops should show up as the driver
gaining places (finish better than quali) more often than a crew with slow or
erratic stops. This is a genuinely different mechanism from anything already
tested (wind/temp/humidity/disagreement, all null) -- pit execution is a
mechanical, measured time loss, not an ambient condition.

Data: Jolpica's pitstops endpoint (confirmed live, stop durations back to
2011 -- no gap against the 2014+ training corpus) plus race_results for the
driverId -> FIA code mapping (pitstops doesn't carry the code directly).

Feature built exactly like F4 (02 sec4, driver recent form): rolling mean
stop duration over the driver's last 5 prior races with recorded pit data,
never including the race being predicted -- no leakage.

Outcome: grid-to-finish delta (quali_position - finish_position; positive =
gained places) and the algo's own label/brier, both already in winner.csv.

Usage: python pit_quality_spike.py [--refresh]
"""

import argparse
import csv
import os
import statistics
import sys
from collections import defaultdict
from datetime import date

from lib import httpcache
from lib.jolpica import race_results

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cache")
WINNER_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "training", "winner.csv")
PITSTOPS_BASE = "https://api.jolpi.ca/ergast/f1"


def load_corpus():
    rows = []
    with open(WINNER_CSV) as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def pitstops_for(season, rnd, refresh):
    url = f"{PITSTOPS_BASE}/{season}/{rnd}/pitstops.json?limit=100"
    body, _ = httpcache.cached_get_json(url, CACHE_DIR, timeout=30, force_refresh=refresh)
    races = body["MRData"]["RaceTable"]["Races"]
    if not races:
        return {}
    out = defaultdict(list)
    for s in races[0]["PitStops"]:
        try:
            out[s["driverId"]].append(float(s["duration"]))
        except (KeyError, ValueError):
            continue  # a handful of stops have no numeric duration (red-flag-adjacent, etc.)
    return out


def driverid_to_code(season, rnd):
    results, _ = race_results(season, rnd, CACHE_DIR)
    return {r["Driver"]["driverId"]: r["Driver"]["code"] for r in results}


def build(refresh):
    corpus = load_corpus()
    by_race = defaultdict(list)
    for row in corpus:
        by_race[(row["season"], row["round"])].append(row)

    races_sorted = sorted(by_race.keys(), key=lambda k: (k[0], int(k[1])))

    # driver_code -> list of (race_date, mean_stop_duration) seen so far, oldest first
    history = defaultdict(list)
    out_rows = []
    skipped_no_pit_data = 0

    for season, rnd in races_sorted:
        rows = by_race[(season, rnd)]
        race_date = rows[0]["race_date"]

        stops = pitstops_for(season, rnd, refresh)
        if not stops:
            skipped_no_pit_data += 1
        else:
            id_to_code = driverid_to_code(season, rnd)

            for row in rows:
                code = row["driver_code"]
                # rolling feature: mean of this driver's last 5 prior races with pit data
                prior = history[code][-5:]
                if len(prior) >= 2:  # need at least a couple of data points to mean anything
                    rolling_mean_stop = statistics.mean(d for _, d in prior)
                    grid_finish_delta = int(row["quali_position"]) - int(row["finish_position"]) \
                        if row["classified"] == "1" else None
                    out_rows.append({
                        "season": season, "round": rnd, "driver_code": code,
                        "race_date": race_date,
                        "rolling_pit_speed": rolling_mean_stop,
                        "delta": grid_finish_delta,
                        "brier": (float(row["p_a1"]) - float(row["label"])) ** 2,
                    })

            # update history AFTER scoring this race, so it's never used on itself
            for driver_id, durations in stops.items():
                code = id_to_code.get(driver_id)
                if code and durations:
                    history[code].append((race_date, statistics.mean(durations)))

    return out_rows, skipped_no_pit_data


def spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0] * n
        for rank, i in enumerate(order):
            r[i] = rank
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    sx = (sum((v - mx) ** 2 for v in rx)) ** 0.5
    sy = (sum((v - my) ** 2 for v in ry)) ** 0.5
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    rows, skipped = build(args.refresh)
    print(f"n = {len(rows)} driver-race rows with a rolling pit-speed feature "
          f"({skipped} races had no recorded pit-stop data, e.g. zero-stop races)\n")

    delta_rows = [r for r in rows if r["delta"] is not None]
    print(f"--- rolling pit speed (lower=faster) vs outcome (n={len(delta_rows)} classified) ---")
    xs = [r["rolling_pit_speed"] for r in delta_rows]
    ys = [r["delta"] for r in delta_rows]
    print(f"  rolling_pit_speed vs grid-to-finish delta (+ = gained places): {spearman(xs, ys):+.3f}")
    print(f"  rolling_pit_speed vs brier: {spearman([r['rolling_pit_speed'] for r in rows], [r['brier'] for r in rows]):+.3f}")
    print()

    sorted_rows = sorted(delta_rows, key=lambda r: r["rolling_pit_speed"])
    q = len(sorted_rows) // 4
    fast, slow = sorted_rows[:q], sorted_rows[-q:]
    print(f"--- fastest/slowest quartile by rolling pit speed (n={q} each) ---")
    print(f"  fast crews ({fast[0]['rolling_pit_speed']:.2f}-{fast[-1]['rolling_pit_speed']:.2f}s): "
          f"mean delta {statistics.mean(r['delta'] for r in fast):+.2f} places")
    print(f"  slow crews ({slow[0]['rolling_pit_speed']:.2f}-{slow[-1]['rolling_pit_speed']:.2f}s): "
          f"mean delta {statistics.mean(r['delta'] for r in slow):+.2f} places")


if __name__ == "__main__":
    sys.exit(main())
