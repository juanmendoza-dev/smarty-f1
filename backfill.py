#!/usr/bin/env python3
"""Build the Phase A3 training matrix. See 05-trained-model.md sec4/sec5.

One row per driver-race, seven feature columns, one label. Writes CSV to
data/training/ rather than one snapshot JSON per race (sec5.1): a snapshot is
defined as carrying a markets block, this deliberately has none, and the
append-only rule on data/snapshots/ protects predictions that were actually
made -- a training set is a derived artifact that gets rebuilt whenever a
feature changes.

The central rule is sec4.2: every feature here is computed by calling
snapshot.build_* and score.score_all *unchanged*. This file contains no copy
of the feature logic. Train/serve skew in this project would be invisible --
a re-typed pos_score with a different K produces perfectly plausible numbers
that simply aren't what the scorer computes at inference -- so sharing the
code path makes that class of bug impossible rather than merely unlikely.

Two deliberate differences from what score_all returns, both from sec3:

  F7 weather is dropped entirely (sec3.3). Dormant scores every driver
  NEUTRAL, a within-race constant cancels exactly out of a conditional logit's
  likelihood, so beta_weather is unidentified -- not merely imprecise. A
  constant 0.5 column would train an artifact of the regularizer. score_all
  still computes it (we hand it a dormant stub, as test_phase_a4.py does);
  we just don't write it.

  F3 sprint is forced to 0.0 for the whole field on a non-sprint race
  (sec3.4). score.compute_sprint returns NEUTRAL there, which is right for A1
  -- A1 drops the feature and renormalizes -- but a pooled model can't have a
  design matrix whose column count varies by race. By the same cancellation
  rule, 0.0 across the field is exactly equivalent to dropping it, and it
  makes "this race didn't inform F3" visible in the matrix instead of
  confusable with a real neutral score.

Usage:
    python3 backfill.py --seasons 2015              # one season, for validation
    python3 backfill.py --seasons 2014-2026         # the full corpus
    python3 backfill.py --seasons 2014-2026 --limit 5
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import postrace
import score
import snapshot
from lib import circuits, jolpica
from lib.invariants import require

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(REPO_ROOT, "data", "training", "winner.csv")

# sec4.2. The seven features, in the order they appear in the CSV. Keyed by
# score.score_all's own sub_scores names so the mapping can't drift.
FEATURES = ["grid", "team", "sprint", "driver_form", "track", "champ", "teammate"]

IDENTITY = ["season", "round", "race_date", "circuit_id", "driver_code", "constructor_id"]
CONTEXT = ["is_sprint_weekend", "quali_position", "track_n"]
OUTCOME = ["finish_position", "status", "classified", "label"]
BASELINE = ["p_a1"]

COLUMNS = IDENTITY + CONTEXT + FEATURES + OUTCOME + BASELINE


def already_done(out_path):
    """sec5.3: resumable. A rate-limit stall should cost the remaining races,
    not the finished ones."""
    if not os.path.exists(out_path):
        return set()
    done = set()
    with open(out_path, newline="") as f:
        for row in csv.DictReader(f):
            done.add((int(row["season"]), int(row["round"])))
    return done


def build_race_rows(season, round_, cache_dir):
    """Every driver-race row for one race. Raises on anything it can't do
    honestly -- the caller decides whether to skip or abort."""
    race, _ = jolpica.race_info(season, round_, cache_dir)
    race_date = race["date"]
    circuit_id = race["Circuit"]["circuitId"]
    lat = float(race["Circuit"]["Location"]["lat"])
    lon = float(race["Circuit"]["Location"]["long"])

    grid, is_sprint_weekend, _ = build_grid_checked(season, round_, cache_dir)

    # race_has_run=True routes F6 to {season}/{round-1}/driverstandings.json.
    # The live "latest" path would hand back a finished season's FINAL table
    # (01-data-pipeline.md sec4.6) -- a real bug once, and one that looks
    # entirely plausible in the output.
    form, _ = snapshot.build_form(season, round_, grid, cache_dir, race_has_run=True)

    track_history, th_prov = snapshot.build_track_history(
        circuit_id, lat, lon, grid, race_date, cache_dir
    )

    # sec4.4: leakage check at build time rather than in a reviewer's head.
    # build_track_history already asserts exactly this on its own output, so
    # this is a deliberate second line of defence on the assembled structure
    # rather than a missing guard: F5 is the feature where a leak is both
    # easiest to introduce (the target race's own date is exactly race_date, so
    # a non-strict compare trains on the label) and hardest to notice.
    for code, rows in track_history["by_driver"].items():
        for row in rows:
            require(
                row["date"] < race_date,
                f"leakage: {code} track-history row dated {row['date']} is not strictly "
                f"before {season}/{round_} on {race_date}",
            )
    require(
        form["standings_after_round"] in (None, round_ - 1),
        f"leakage: F6 standings for {season}/{round_} stamped round "
        f"{form['standings_after_round']}, expected {round_ - 1}",
    )

    algo_snapshot = {
        "meta": {
            "season": season,
            "round": round_,
            "circuit_id": circuit_id,
            "track_overtaking_multiplier": circuits.multiplier_for(circuit_id),
            "is_sprint_weekend": is_sprint_weekend,
        },
        "grid": grid,
        "form": form,
        "track_history": track_history,
        # sec3.3: F7 is dormant for every backfilled row -- the archive endpoint
        # serves observed mm, never a precipitation probability (01 sec5.6).
        # score_all still wants the key; the column it produces is discarded.
        "weather": {"p_max": 0},
    }
    # dict keys like results_by_round's round numbers become strings on the way
    # through json.dump in a real snapshot, and compute_driver_form indexes
    # per_round[str(rnd)]. Round-trip so this path sees what score.py normally
    # sees, exactly as test_phase_a4.py does.
    algo_snapshot = json.loads(json.dumps(algo_snapshot, default=str))

    scored = score.score_all(algo_snapshot)

    # sec4.1: the label. Fetched only here, and only to label -- never fed to a
    # feature. find_full_result asserts exactly one classified P1 using the
    # real is_classified() rule, which has already been wrong once (04 sec10.5).
    result_rows, _ = postrace.find_full_result(season, round_, cache_dir)
    result_by_code = {r["code"]: r for r in result_rows}

    rows = []
    for entry in grid:
        code = entry["code"]
        res = result_by_code.get(code)
        sub = {f: scored["sub_scores"][f][code] for f in FEATURES}
        if not is_sprint_weekend:
            sub["sprint"] = 0.0  # sec3.4
        row = {
            "season": season,
            "round": round_,
            "race_date": race_date,
            "circuit_id": circuit_id,
            "driver_code": code,
            "constructor_id": entry["constructor_id"],
            "is_sprint_weekend": int(is_sprint_weekend),
            "quali_position": entry["quali_position"],
            "track_n": scored["track_n"].get(code, 0),
            "finish_position": res["position"] if res else "",
            "status": res["status"] if res else "",
            "classified": int(res["classified"]) if res else "",
            "label": 1 if (res and res["classified"] and res["position"] == 1) else 0,
            "p_a1": round(scored["p_algo"][code], 8),
        }
        row.update({f: round(sub[f], 8) for f in FEATURES})
        rows.append(row)

    # sec9.1
    n_winners = sum(r["label"] for r in rows)
    require(
        n_winners == 1,
        f"{season}/{round_}: expected exactly one label-1 row, got {n_winners} "
        f"(winner may not have started -- qualifying and result disagree)",
    )
    # sec9.2
    for r in rows:
        for f in FEATURES:
            require(
                0.0 - 1e-9 <= r[f] <= 1.0 + 1e-9,
                f"{season}/{round_} {r['driver_code']}: feature {f}={r[f]} outside [0,1]",
            )
    # sec5.4: a cached whole-history response fetched before the target race
    # can't be proven complete. The leakage filter drops anything on or after
    # race_date, so a stale entry yields silently FEWER prior editions rather
    # than an error -- an F5 shrunk toward NEUTRAL for no stated reason.
    stale = [m["url"] for m in th_prov
             if m.get("timestamp", "")[:10] < race_date and "circuits" in m.get("url", "")]

    return rows, stale


def build_grid_checked(season, round_, cache_dir):
    """build_grid raises SystemExit when qualifying is missing, which would
    abort the whole run rather than skip one race. Convert it."""
    try:
        return snapshot.build_grid(season, round_, cache_dir)
    except SystemExit as e:
        raise RuntimeError(str(e))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", required=True,
                    help="a season (2015) or an inclusive range (2014-2026)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--cache-dir", default=snapshot.DEFAULT_CACHE_DIR)
    ap.add_argument("--limit", type=int, help="stop after this many races (for a trial run)")
    args = ap.parse_args()

    if "-" in args.seasons:
        lo, hi = (int(x) for x in args.seasons.split("-", 1))
    else:
        lo = hi = int(args.seasons)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    done = already_done(args.out)
    if done:
        print(f"resuming: {len(done)} race(s) already in {os.path.relpath(args.out, REPO_ROOT)}")

    today = datetime.now(timezone.utc).date().isoformat()
    written, skipped, stale_all = 0, [], []
    exists = os.path.exists(args.out)

    with open(args.out, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if not exists:
            writer.writeheader()

        for season in range(lo, hi + 1):
            schedule, _ = jolpica.schedule(season, args.cache_dir)
            for race in schedule:
                round_ = int(race["round"])
                if race["date"] >= today:
                    continue  # not run yet; no label to be had
                if (season, round_) in done:
                    continue
                if args.limit is not None and written >= args.limit:
                    print(f"\nstopping at --limit {args.limit}")
                    _summarize(written, skipped, stale_all, args.out)
                    return 1 if skipped else 0

                label = f"{season} R{round_:<2} {race['raceName']}"
                try:
                    rows, stale = build_race_rows(season, round_, args.cache_dir)
                except Exception as e:
                    skipped.append((season, round_, race["raceName"], repr(e)))
                    print(f"  SKIP  {label}: {e}")
                    continue

                for row in rows:
                    writer.writerow(row)
                f.flush()  # resumable means resumable after a kill, not just a clean exit
                written += 1
                stale_all.extend(stale)
                winner = next(r["driver_code"] for r in rows if r["label"] == 1)
                print(f"  ok    {label:<44} {len(rows):>2} rows, won by {winner}")

    _summarize(written, skipped, stale_all, args.out)
    return 1 if skipped else 0


def _summarize(written, skipped, stale, out_path):
    print(f"\n{written} race(s) written to {os.path.relpath(out_path, REPO_ROOT)}")
    if stale:
        print(f"\nWARNING: {len(stale)} track-history response(s) were cached before their "
              f"target race date (05 sec5.4).")
        print("  Completeness can't be proven for those; re-run with a cleared cache to be sure.")
        for url in sorted(set(stale))[:5]:
            print(f"    {url}")
    if skipped:
        print(f"\n{len(skipped)} race(s) SKIPPED -- these are not in the training set:")
        for season, round_, name, err in skipped:
            print(f"    {season} R{round_} {name}: {err}")
    else:
        print("no races skipped")


if __name__ == "__main__":
    sys.exit(main())
