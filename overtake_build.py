#!/usr/bin/env python3
"""Build the overtake training matrix. 08-overtake-model.md sec4/sec5.

Reads the historical archive through FastF1 -- no live connection, ever
(sec4, and 03 sec4.4's amended gate authorizes exactly this and nothing more).

2026 races only, and this is not a convenience cutoff (sec4): channel 45 is
measured constant zero in 2026 (03 sec7.3), so a model trained on 2024-25
archives would lean on a DRS column that is identically zero at serve time --
train/serve skew of the kind 05 sec4.2 exists to prevent. The 2026 regulations
also replaced DRS with active aero plus a battery-boost overtake mode, so
pre-2026 closing dynamics are a different process.

The output does NOT go in git. data/training/winner.csv is committed because
it is Jolpica classification data; this matrix is F1 timing data and this repo
is public, so 03 sec11.2's reasoning applies unchanged -- it lands under
data/live/, which is gitignored.

Usage:
    python3 overtake_build.py                 # every 2026 round with an archive
    python3 overtake_build.py --rounds 12,13  # specific rounds
    python3 overtake_build.py --out PATH
"""

import argparse
import csv
import os
import sys
import warnings

warnings.filterwarnings("ignore")

import fastf1
import pandas as pd

from fastf1 import api
from lib import overtakes as ov
from lib import overtake_features as of
from lib.invariants import require

SEASON = 2026
CACHE_DIR = "data/cache/fastf1"
OUT_DEFAULT = "data/live/overtakes/training.csv"

COLUMNS = ["season", "round", "race", "t", "pursuer", "ahead", "episode_start"] \
          + of.FEATURE_NAMES + ["label"]


def build_race(season, rnd, name):
    """One race -> list of feature rows. Returns (rows, summary)."""
    session = fastf1.get_session(season, rnd, "R")
    session.load(telemetry=True, laps=True, weather=False, messages=False)

    _, stream = api.timing_data(session.api_path)
    stream = stream.copy()
    stream["t"] = stream["Time"].dt.total_seconds()

    pos = ov.position_stream(stream)
    iv = ov.interval_stream(stream)
    ahead_idx = ov.AheadIndex(pos)
    windows = ov.pit_windows(session)
    lap1_t = ov.lap_one_end(session)

    passes = ov.find_passes(pos, ahead_idx, windows, lap1_t)
    episodes = ov.find_episodes(iv, ahead_idx, windows, lap1_t)
    caution = of.caution_frame(session)
    total_laps = float(session.laps["LapNumber"].max())

    rows = []
    for ep in episodes:
        rows.extend(of.build_episode_rows(session, ep, passes, caution,
                                          total_laps, iv, ahead_idx))
    for r in rows:
        r["season"] = season
        r["round"] = rnd
        r["race"] = name

    pos_rows = sum(r["label"] for r in rows)
    summary = {
        "round": rnd, "race": name, "passes": len(passes),
        "episodes": len(episodes), "rows": len(rows), "positives": pos_rows,
        "rate": (pos_rows / len(rows)) if rows else 0.0,
    }
    return rows, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default=None, help="comma-separated round numbers")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--season", type=int, default=SEASON)
    args = ap.parse_args()

    os.makedirs(CACHE_DIR, exist_ok=True)
    fastf1.Cache.enable_cache(CACHE_DIR)

    schedule = fastf1.get_event_schedule(args.season, include_testing=False)
    if args.rounds:
        wanted = {int(x) for x in args.rounds.split(",")}
    else:
        wanted = set(int(r) for r in schedule["RoundNumber"] if int(r) > 0)

    all_rows, summaries, failed = [], [], []
    for _, ev in schedule.iterrows():
        rnd = int(ev["RoundNumber"])
        if rnd not in wanted:
            continue
        name = ev["EventName"]
        try:
            rows, s = build_race(args.season, rnd, name)
        except Exception as e:                      # noqa: BLE001
            failed.append((rnd, name, "%s: %s" % (type(e).__name__, str(e)[:120])))
            print("  [skip] R%-2d %-28s %s: %s"
                  % (rnd, name, type(e).__name__, str(e)[:90]), flush=True)
            continue
        all_rows.extend(rows)
        summaries.append(s)
        print("  R%-2d %-28s passes=%3d episodes=%4d rows=%6d positives=%5d (%.2f%%)"
              % (rnd, name, s["passes"], s["episodes"], s["rows"],
                 s["positives"], 100 * s["rate"]), flush=True)

    require(all_rows, "no rows built -- every race failed to load")

    # sec10: label counts per race inside the plausibility band measured in
    # sec2.1 (34-43 on-track overtakes). A race outside it means the pit filter
    # or the persistence filter broke, not that the race was unusual.
    for s in summaries:
        require(1 <= s["passes"] <= 150,
                "R%d %s produced %d on-track overtakes, outside the sec10 plausibility "
                "band -- the pit or debounce filter is broken"
                % (s["round"], s["race"], s["passes"]))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in all_rows:
            w.writerow(r)

    npos = sum(r["label"] for r in all_rows)
    print("\nwrote %s" % args.out)
    print("  races      : %d (%d failed)" % (len(summaries), len(failed)))
    print("  rows       : %d" % len(all_rows))
    print("  positives  : %d (%.2f%%)" % (npos, 100 * npos / len(all_rows)))
    print("  overtakes  : %d" % sum(s["passes"] for s in summaries))
    if failed:
        print("  FAILED     :")
        for rnd, name, err in failed:
            print("    R%-2d %-28s %s" % (rnd, name, err))


if __name__ == "__main__":
    main()
