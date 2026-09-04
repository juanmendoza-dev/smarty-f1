#!/usr/bin/env python3
"""Fit everything the win-probability layer serves from. 09 sec5.4, sec5.5, sec10.

Produces one artifact, `data/live/winprob/fit.json`, holding per test round:

  - the **background per-lap transition model** (09 sec5.4),
  - the **two-segment retirement hazard** and its flat variant (09 sec2.5/5.5),
  - the **position-only ladder** baseline (09 sec10 baseline 2),
  - a persisted **`08` model** (weights, standardisation, Platt map),
  - the **reconciled strengths** `w'` from 09 sec5.5's IPF, and its residual.

Everything is fitted RACE-FORWARD: the objects used to score race *n* are fitted
on races strictly before it. Nothing is fitted on the corpus it is scored on
(`05` sec6.1, `08` sec8, 09 sec9.2). The `08` folds reproduce `08` sec11.1's
nested structure exactly -- logistic on rounds `[:i-2]`, Platt on the two races
`[i-2:i]`, serve on round `i` -- so the model this layer consumes for race *n*
is the same model `08` reported out-of-fold numbers for.

The output is gitignored: it is derived from F1 timing data and this repo is
public (`03` sec11.2).

Usage:
    .venv312/bin/python winprob_fit.py
    .venv312/bin/python winprob_fit.py --rounds 5,6 --quick
"""

import argparse
import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")

import numpy as np

import overtake_fit as of_fit
from lib import overtake_features as of
from lib import winprob_background as bgmod
from lib import winprob_priors as wpp
from lib import winprob_replay as wpr
from lib import winprob_sim as wsim
from lib.circuits import multiplier_for
from lib.invariants import require
from lib.overtake_serve import OvertakeModel

SEASON = 2026
OUT_DEFAULT = "data/live/winprob/fit.json"
MATRIX = "data/live/overtakes/training.csv"

# 09 sec9.2: 08's out-of-fold predictions exist only for R5-R12 (its nested
# folds need two races to fit the calibrator and two more before them to fit
# the logistic), so rounds 1-4 are training material and are never scored.
FIRST_SCOREABLE_ROUND = 5

# 09 sec5.5 requires the reconciliation to run at N >= 200,000 so the ratio
# update is not chasing Monte Carlo noise into w'. Run flat out for 30+
# iterations at that N and one race costs seven minutes, so the schedule is
# staged: converge cheaply, then finish where the spec wants it. Only the final
# sweeps decide w', and they are at the spec'd budget.
IPF_COARSE_N = 40_000
IPF_COARSE_ITERS = 25
IPF_FINE_N = wpp.RECONCILE_N
IPF_FINE_ITERS = 6


def load_archives(rounds, cache):
    out = {}
    for rnd in rounds:
        if rnd in cache:
            out[rnd] = cache[rnd]
            continue
        try:
            a = wpr.RaceArchive(SEASON, rnd, telemetry=False)
        except Exception as e:                      # noqa: BLE001
            sys.stderr.write("R%d: skip (%s)\n" % (rnd, type(e).__name__))
            continue
        cache[rnd] = a
        out[rnd] = a
    return out


def race_observations(archive, exclude_pit_swaps=False):
    """Background-rate and ladder observations for one race.

    `exclude_pit_swaps` is 12 sec4's mandatory side effect: with
    `lib/pit_strategy.py` projecting a cycle explicitly, a `q` that still
    contains pit-cycle swaps counts the cycle twice. The window is
    `lib/winprob_background`'s and is pre-registered there.
    """
    order_by_lap = archive.order_by_lap()
    retired_lap = archive.retired_lap_by_code()
    swaps = bgmod.swap_observations(
        order_by_lap, retired_lap, archive.total_laps,
        pit_laps_by_code=archive.pit_laps_by_code() if exclude_pit_swaps else None)
    winner = archive.winner()
    ladder = []
    for lap in range(1, archive.total_laps + 1):
        order = order_by_lap.get(lap) or {}
        progress = lap / float(archive.total_laps)
        for pos, code in order.items():
            ladder.append((pos, progress, 1 if code == winner else 0))
    retire_fracs = [rl / float(archive.total_laps) for rl in retired_lap.values()]
    return {"circuit_id": archive.circuit_id, "observations": swaps,
            "ladder": ladder, "retire_fracs": retire_fracs,
            "pit_swaps_removed": bool(exclude_pit_swaps)}


def fit_overtake_models(rows, rounds):
    """08 sec11.1's nested race-forward folds, persisted rather than scored.

    `overtake_fit`'s own primitives are reused unchanged -- `standardize`,
    `fit_logistic`, `platt_fit`. Re-typing a standardisation or an optimizer
    here would be exactly the reimplementation `05` sec4.2 forbids, and this
    layer's whole claim on 08's calibration rests on the model being the same
    object 08 measured.
    """
    models = {}
    for i in range(4, len(rounds)):
        fit_rounds, calib_rounds, test_round = rounds[:i - 2], rounds[i - 2:i], rounds[i]
        tr = [r for r in rows if r["round"] in fit_rounds]
        ca = [r for r in rows if r["round"] in calib_rounds]
        if not tr or not ca or sum(r["label"] for r in tr) == 0:
            continue
        ytr = [r["label"] for r in tr]
        yca = [r["label"] for r in ca]
        Xtr, Xca, stats = of_fit.standardize(tr, ca, of.FEATURE_NAMES)
        w, b = of_fit.fit_logistic(Xtr, ytr)
        p_ca = of_fit.predict(Xca, w, b)
        pa, pb = of_fit.platt_fit(p_ca, yca)
        models[test_round] = OvertakeModel(
            w, b, stats, of.FEATURE_NAMES, platt=(pa, pb),
            meta={"train_rounds": fit_rounds, "calib_rounds": calib_rounds,
                  "n_train": len(tr), "positives_train": int(sum(ytr))})
        print("  08 model for R%-2d  train=%s calib=%s rows=%d pos=%d platt=(%.2f,%.1f)"
              % (test_round, fit_rounds, calib_rounds, len(tr), sum(ytr), pa, pb),
              flush=True)
    return models


def reconcile_race(archive, prior_rows, background, hazard, quick=False):
    """09 sec5.5 step 3, for one race."""
    p_algo, grid, prior_id = wpp.prior_from_rows(prior_rows)
    order = wpp.grid_order(grid)
    f_dnf, field_rate = wpp.dnf_rates_before(SEASON, archive.round)
    # A code in the archive with no prior row still has to be in the
    # permutation or p_win cannot sum to 1 over the field the tick carries
    # (09 sec11 assertion 1). It enters at the weakest prior strength and is
    # excluded from the reconcile band -- declared once, in winprob_priors.
    for code in archive.codes:
        if code not in p_algo:
            order.append(code)
            f_dnf.setdefault(code, field_rate)
    total_laps = archive.total_laps
    m = multiplier_for(archive.circuit_id)
    hz = wpp.lap_hazards({c: f_dnf.get(c, field_rate) for c in order}, hazard, 0, total_laps)
    floor = min(p_algo.values()) if p_algo else 1e-6

    def sim(w, n):
        strengths = {c: w.get(c, floor) for c in order}
        p, _, _ = wsim.forward_simulate(
            "%s:reconcile" % archive.round, order, strengths, hz, background,
            1, total_laps, track_frac=0.0, m=m, pursuits=(), n_paths=n,
            use_overtake_model=False)
        return p

    w = wpp.strengths_from_prior(p_algo)
    coarse_n = 8_000 if quick else IPF_COARSE_N
    fine_n = 20_000 if quick else IPF_FINE_N
    w, d1 = wpp.reconcile(p_algo, lambda ws_: sim(ws_, coarse_n), w_start=w,
                          iters=IPF_COARSE_ITERS)
    w, d2 = wpp.reconcile(p_algo, lambda ws_: sim(ws_, fine_n), w_start=w,
                          iters=IPF_FINE_ITERS)
    strengths = {c: w.get(c, floor) for c in order}
    return {
        "prior_id": prior_id, "p_algo": p_algo, "grid_order": order,
        "strengths": strengths, "f_dnf": f_dnf, "field_dnf_rate": field_rate,
        "circuit_id": archive.circuit_id, "m": m, "total_laps": total_laps,
        # Both residuals, always. 09 sec5.5's dated correction explains why
        # they differ and why neither is quoted without the other.
        "residual": d2["worst_abs_residual"],
        "residual_cond": d2["worst_cond_residual"],
        "residual_coarse": d1["worst_cond_residual"],
        "tail_mass": d2["tail_mass"], "band_mass": d2["band_mass"],
        "residual_history": d1["residual_history"] + d2["residual_history"],
        "reconcile_n": fine_n, "band": d2["band"],
        "p_hat": d2["p_hat"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default=None, help="test rounds to fit for")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--matrix", default=MATRIX)
    ap.add_argument("--quick", action="store_true",
                    help="lower IPF budgets -- for wiring checks, never for a reported run")
    ap.add_argument("--skip-overtake", action="store_true")
    ap.add_argument("--pit-refit", action="store_true",
                    help="refit 09 sec5.4's q with pit-cycle swaps removed "
                         "(12 sec4) -- REQUIRED by 12 sec7 assertion 4 for any "
                         "fit the pit-strategy model will be scored against")
    args = ap.parse_args()

    t_start = time.time()
    all_rounds = list(range(1, 13))
    cache = {}
    archives = load_archives(all_rounds, cache)
    require(archives, "no archives loaded")
    print("archives: %s" % ", ".join("R%d" % r for r in sorted(archives)))

    obs = {r: race_observations(a, exclude_pit_swaps=args.pit_refit)
           for r, a in sorted(archives.items())}
    if args.pit_refit:
        kept = sum(len(o["observations"]) for o in obs.values())
        print("12 sec4: q refit with pit-cycle swaps removed -- %d adjacent-pair "
              "observations survive the window" % kept)

    test_rounds = ([int(x) for x in args.rounds.split(",")] if args.rounds
                   else [r for r in sorted(archives) if r >= FIRST_SCOREABLE_ROUND])

    ot_models = {}
    if not args.skip_overtake:
        print("\nfitting 08 fold models (08 sec11.1's nested structure)")
        rows = of_fit.load_matrix(args.matrix)
        rounds_present = sorted(set(r["round"] for r in rows))
        ot_models = fit_overtake_models(rows, rounds_present)

    out = {"season": SEASON, "test_rounds": [], "races": {},
           "overtake_models": {str(k): v.as_dict() for k, v in ot_models.items()},
           "meta": {"quick": args.quick, "ipf_fine_n": IPF_FINE_N,
                    "ipf_coarse_n": IPF_COARSE_N,
                    "pit_swaps_removed": bool(args.pit_refit)}}

    print("\nfitting race-forward, per test round")
    for rnd in test_rounds:
        if rnd not in archives:
            continue
        train = [obs[r] for r in sorted(obs) if r < rnd]
        require(train, "R%d has no earlier races to fit on" % rnd)
        background = bgmod.fit_background(train)
        hazard = wpp.TwoSegmentHazard.fit(
            [f for race in train for f in race["retire_fracs"]])
        flat = wpp.TwoSegmentHazard.flat()
        ladder = bgmod.PositionLadder.fit(
            [o for race in train for o in race["ladder"]])

        prior_rows = wpp.load_prior_rows(SEASON, rnd)
        t0 = time.time()
        rec = reconcile_race(archives[rnd], prior_rows, background, hazard,
                             quick=args.quick)
        rec_flat = reconcile_race(archives[rnd], prior_rows, background, flat,
                                  quick=args.quick)

        out["test_rounds"].append(rnd)
        out["races"][str(rnd)] = {
            "round": rnd, "name": str(archives[rnd].session.event["EventName"]),
            "background": background.as_dict(), "ladder": ladder.as_dict(),
            "hazard": hazard.as_dict(), "hazard_flat": flat.as_dict(),
            "reconciled": rec, "reconciled_flat": rec_flat,
            "winner": archives[rnd].winner(),
            "classified_order": archives[rnd].classified_order(),
        }
        print("  R%-2d %-26s q(P1-P3,late)=%.4f slope=%.1f hazard a=%.2f b=%.2f "
              "| IPF cond %.4f abs %.4f (tail %.3f; flat cond %.4f) %.0fs"
              % (rnd, str(archives[rnd].session.event["EventName"])[:26],
                 background.rate(1, 0.9, rec["m"]), background.slope,
                 hazard.a, hazard.b, rec["residual_cond"], rec["residual"],
                 rec["tail_mass"], rec_flat["residual_cond"],
                 time.time() - t0), flush=True)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print("\nwrote %s  (%.0fs)" % (args.out, time.time() - t_start))

    # The by-product 02 sec10 item 1 is owed: per-circuit residual against m.
    last = out["races"].get(str(test_rounds[-1]))
    if last:
        pc = last["background"]["per_circuit"]
        print("\nper-circuit background residual against 02 sec5.1's m "
              "(fitted on R1..R%d) -- 02 sec10 item 1" % (test_rounds[-1] - 1))
        print("  %-18s %5s %8s %10s %10s %7s" % ("circuit", "m", "pairs", "observed",
                                                 "predicted", "obs/pred"))
        for cid, acc in sorted(pc.items(), key=lambda kv: -kv[1]["observed_rate"]):
            print("  %-18s %5.2f %8d %10.4f %10.4f %7.2f"
                  % (cid, acc["m"], acc["n"], acc["observed_rate"],
                     acc["predicted_rate"], acc["ratio"]))


if __name__ == "__main__":
    main()
