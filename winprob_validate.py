#!/usr/bin/env python3
"""Replay validation for the win-probability layer. 09 sec9, sec10.

**Success is pre-registered in 09 sec10 and is not moved here.** The layer
succeeds only if it beats BOTH the static Lane A number and the position-only
ladder on pooled log-loss, AND wins the per-race breakdown in at least 6 of the
8 scoreable races. `08` earns its place only if the ablation is measurably worse
than the full layer by more than the block-bootstrap width. 09 sec1.3 and sec10
pre-register "the layer adds nothing" as a legitimate result, so it is reported
either way.

Four baselines, all on the same folds, checkpoints and metrics (09 sec10):

  1. **Lane A's static pre-race number**, held constant for the whole race.
  2. **The position-only ladder** -- P(win) from position and progress alone.
     09 sec10 calls this "the real floor and it is a strong one".
  3. **The ablation** -- the same simulator with `08` switched off. 09 sec10:
     "the most important number this validation produces". Run under common
     random numbers with the full arm (09 sec7.4), so the paired difference is
     not swamped by either arm's own ~0.5-point standard error.
  4. **The market**, on the one race this project holds live snapshotted prices
     for (2026 Dutch GP). Colour on a single race, never a baseline -- `05`
     sec6.3's rule against backfilling historical market prices.

Three things about the power problem, all pre-registered in 09 sec9.3 and all
enforced here rather than remembered:

  - Eight scoreable races means **eight winner events**, not ~500. Per-race
    won/lost is reported alongside every pooled number.
  - No interval is computed as if checkpoints were independent. The bootstrap
    blocks over whole races. It will be wide; that is the finding.
  - The Plackett-Luce log-likelihood of the full realised finishing order is a
    secondary DIAGNOSTIC and does not substitute for the winner metric.

Usage:
    .venv312/bin/python winprob_validate.py
    .venv312/bin/python winprob_validate.py --rounds 12 --mode lap
"""

import argparse
import json
import math
import os
import random
import sys
import time
import warnings

warnings.filterwarnings("ignore")

from lib import winprob as wp
from lib import winprob_background as bgmod
from lib import winprob_replay as wpr
from lib import winprob_sim as wsim
from lib.invariants import require
from lib.overtake_serve import OvertakeModel
from lib.winprob_priors import TwoSegmentHazard

SEASON = 2026
FIT_DEFAULT = "data/live/winprob/fit.json"
OUT_DEFAULT = "data/live/winprob/validation.json"
SNAPSHOT_DUTCH = "data/snapshots/2026-12-race-20260823T031058Z.json"

# One epsilon floor, declared once and applied identically to the layer and to
# every baseline. Log-loss of a zero is infinite, and at finite N the simulator
# can legitimately assign the eventual winner no paths at all, as can an empty
# ladder cell. Applied unevenly this constant silently decides the comparison,
# and the error direction favours whichever arm was floored more generously --
# so it is a module constant, not a per-arm choice.
EPS = 1e-4
BOOTSTRAP_DRAWS = 5000
BOOTSTRAP_SEED = 20260903

ARMS = ("layer", "ablation", "ladder", "static")


def clamp(p):
    return min(max(p, EPS), 1.0)


def log_loss(p_by_code, winner):
    return -math.log(clamp(p_by_code.get(winner, 0.0)))


def brier(p_by_code, winner):
    return sum((p - (1.0 if c == winner else 0.0)) ** 2 for c, p in p_by_code.items())


def top1(p_by_code, winner):
    if not p_by_code:
        return 0
    return 1 if max(p_by_code, key=lambda c: p_by_code[c]) == winner else 0


def pl_loglik(strengths, realised_order):
    """Plackett-Luce log-likelihood of the full realised finishing order.

    09 sec9.3's higher-information diagnostic: a finishing order carries ~20
    ordered observations per race rather than one, so it has real power to say
    whether the state estimate is improving. It is labelled a diagnostic and it
    does NOT substitute for the winner metric -- `04` sec6.1 already established
    Plackett-Luce as this project's ranking model, so this costs nothing and is
    not a different model sneaking in through the validation section.

    The strengths used are the estimate's own `p_win`, which under Plackett-Luce
    IS the implied first-place strength vector at that state (P(first) = w/sum w
    and `p_win` sums to 1). That keeps the diagnostic state-dependent -- with the
    frozen reconciled strengths it would barely move between checkpoints.
    """
    w = {c: max(strengths.get(c, 0.0), EPS) for c in realised_order}
    total = sum(w.values())
    ll = 0.0
    for code in realised_order:
        if total <= 0:
            break
        ll += math.log(max(w[code] / total, 1e-300))
        total -= w[code]
    return ll


def load_fit(path):
    with open(path) as fh:
        blob = json.load(fh)
    require(blob.get("races"), "%s has no fitted races" % path)
    return blob


def prior_from_fit(race_blob, flat=False):
    rec = race_blob["reconciled_flat" if flat else "reconciled"]
    hz = race_blob["hazard_flat" if flat else "hazard"]
    return wp.RacePrior(
        prior_id=rec["prior_id"], p_algo=rec["p_algo"], strengths=rec["strengths"],
        f_dnf=rec["f_dnf"],
        hazard=TwoSegmentHazard(hz["a"], hz["b"], hz.get("n_events"), hz.get("split", 0.25)),
        reconciled=True, residual=rec.get("residual_cond"),
        meta={"tail_mass": rec.get("tail_mass"), "m": rec["m"]})


def market_prices():
    """The one race with live snapshotted prices (09 sec10 baseline 4)."""
    if not os.path.exists(SNAPSHOT_DUTCH):
        return None
    with open(SNAPSHOT_DUTCH) as fh:
        snap = json.load(fh)
    venues = snap.get("markets") or {}
    out = {}
    for venue in ("polymarket", "kalshi"):
        by_code = (venues.get(venue) or {}).get("by_code") or {}
        for code, blob in by_code.items():
            n = blob.get("normalized")
            if n is not None:
                out.setdefault(code, []).append(float(n))
    if not out:
        return None
    mean = {c: sum(v) / len(v) for c, v in out.items()}
    total = sum(mean.values())
    return {c: v / total for c, v in mean.items()} if total > 0 else None


def score_race(rnd, race_blob, mode, n_paths, use_platt, inject, verbose=True):
    """Replay one race and score every arm at every lap boundary."""
    telemetry = (mode == "full")
    archive = wpr.RaceArchive(SEASON, rnd, telemetry=telemetry)
    winner = archive.winner()
    require(winner, "R%d has no classified winner" % rnd)
    realised = archive.classified_order()

    prior = prior_from_fit(race_blob)
    background = bgmod.BackgroundRate.from_dict(race_blob["background"])
    ladder = bgmod.PositionLadder.from_dict(race_blob["ladder"])
    m = race_blob["reconciled"]["m"]

    om = None
    if race_blob.get("overtake_model"):
        om = OvertakeModel.from_dict(race_blob["overtake_model"])
    layer = wp.WinProbLayer(prior, background, overtake_model=om, m=m,
                            n_paths=n_paths, use_platt=use_platt,
                            model_id="08:R%d" % rnd)

    static = dict(prior.p_algo)
    rows = []
    n_ticks = 0
    last_lap = None
    ticks = (wpr.full_ticks(archive, inject=inject) if telemetry
             else wpr.lap_ticks(archive, inject=inject))
    t0 = time.time()
    for tick in ticks:
        layer.fold(tick)
        n_ticks += 1
        if tick.lap_current == last_lap:
            continue
        last_lap = tick.lap_current
        running = layer.running_order()
        if not running:
            continue
        est = layer.estimate(n_paths=n_paths, use_overtake_model=True)
        abl = layer.estimate(n_paths=n_paths, use_overtake_model=False)
        p_ladder = ladder.at(running, layer.progress)
        rows.append({
            "round": rnd, "lap": tick.lap_current, "progress": layer.progress,
            "reliable": est.reliable, "reasons": list(est.reasons),
            "pit_offset": est.pit_offset, "n_in_domain": len(est.in_domain),
            "max_se": max(est.se_mc.values()) if est.se_mc else 0.0,
            "ll": {
                "layer": log_loss(est.p_win, winner),
                "ablation": log_loss(abl.p_win, winner),
                "ladder": log_loss(p_ladder, winner),
                "static": log_loss(static, winner),
            },
            "brier": {
                "layer": brier(est.p_win, winner), "ablation": brier(abl.p_win, winner),
                "ladder": brier(p_ladder, winner), "static": brier(static, winner),
            },
            "top1": {
                "layer": top1(est.p_win, winner), "ablation": top1(abl.p_win, winner),
                "ladder": top1(p_ladder, winner), "static": top1(static, winner),
            },
            "p_leader": est.p_win.get(running[0], 0.0),
            "leader": running[0],
            "pl_ll": pl_loglik(dict(est.p_win), [c for c in realised if c in est.p_win]),
        })
    if verbose:
        print("    R%-2d %-24s %s: %d ticks, %d checkpoints, %.0fs"
              % (rnd, str(archive.session.event["EventName"])[:24], mode,
                 n_ticks, len(rows), time.time() - t0), flush=True)
    return {"round": rnd, "name": str(archive.session.event["EventName"]),
            "winner": winner, "total_laps": archive.total_laps,
            "checkpoints": rows}


def pooled(races, metric, arm):
    vals = [r[metric][arm] for race in races for r in race["checkpoints"]]
    return sum(vals) / len(vals) if vals else float("nan")


def per_race(races, metric, arm):
    out = {}
    for race in races:
        vals = [r[metric][arm] for r in race["checkpoints"]]
        out[race["round"]] = sum(vals) / len(vals) if vals else float("nan")
    return out


def block_bootstrap(races, metric, arm_a, arm_b, draws=BOOTSTRAP_DRAWS):
    """09 sec9.3 requirement 2: blocks over whole races, never over checkpoints.

    Within one race the checkpoints are massively autocorrelated -- a race the
    favourite led wire-to-wire contributes ~60 near-identical wins -- so an
    interval computed as if they were independent is a fiction. Eight blocks.
    It will be wide. That is the finding, not a presentational problem.
    """
    means = []
    for race in races:
        a = [r[metric][arm_a] for r in race["checkpoints"]]
        b = [r[metric][arm_b] for r in race["checkpoints"]]
        if a:
            means.append(sum(a) / len(a) - sum(b) / len(b))
    if not means:
        return {"point": float("nan"), "lo": float("nan"), "hi": float("nan"), "n_blocks": 0}
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(means)
    samples = []
    for _ in range(draws):
        pick = [means[rng.randrange(n)] for _ in range(n)]
        samples.append(sum(pick) / n)
    samples.sort()
    return {"point": sum(means) / n,
            "lo": samples[int(0.025 * draws)], "hi": samples[int(0.975 * draws) - 1],
            "n_blocks": n, "per_race": means}


def progress_curve(races, metric, buckets=10):
    out = []
    for b in range(buckets):
        lo, hi = b / buckets, (b + 1) / buckets
        row = {"bucket": "%.1f-%.1f" % (lo, hi), "n": 0}
        vals = {a: [] for a in ARMS}
        for race in races:
            for r in race["checkpoints"]:
                if lo <= r["progress"] < hi or (b == buckets - 1 and r["progress"] >= hi):
                    row["n"] += 1
                    for a in ARMS:
                        vals[a].append(r[metric][a])
        for a in ARMS:
            row[a] = sum(vals[a]) / len(vals[a]) if vals[a] else float("nan")
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit", default=FIT_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--rounds", default=None)
    ap.add_argument("--mode", default="full", choices=("full", "lap"))
    ap.add_argument("--n", type=int, default=wsim.VALIDATE_N)
    ap.add_argument("--platt", action="store_true",
                    help="consume 08's damped-Platt map instead of p_raw (09 sec5.3)")
    ap.add_argument("--degrade", type=int, default=0,
                    help="inject a 03 sec8 degraded window every N seconds (09 sec9.1)")
    args = ap.parse_args()

    fit = load_fit(args.fit)
    rounds = ([int(x) for x in args.rounds.split(",")] if args.rounds
              else sorted(int(r) for r in fit["races"]))
    inject = wpr.degrade_every(args.degrade) if args.degrade else None

    print("validating %d races, mode=%s, N=%d, calibrator=%s"
          % (len(rounds), args.mode, args.n, "platt" if args.platt else "raw"))
    races = []
    for rnd in rounds:
        blob = dict(fit["races"][str(rnd)])
        om = fit.get("overtake_models", {}).get(str(rnd))
        blob["overtake_model"] = om
        if om is None:
            print("    R%-2d no 08 fold model -- the ablation arm is meaningless here" % rnd)
        races.append(score_race(rnd, blob, args.mode, args.n, args.platt, inject))

    report = {"mode": args.mode, "n_paths": args.n, "eps": EPS,
              "platt": args.platt, "degrade_every": args.degrade,
              "rounds": rounds, "races": races}

    print("\n" + "=" * 78)
    print("09 sec10 -- pooled over %d races, %d checkpoints"
          % (len(races), sum(len(r["checkpoints"]) for r in races)))
    print("=" * 78)
    print("%-10s %12s %12s %10s" % ("arm", "log-loss", "brier", "top-1"))
    for arm in ARMS:
        print("%-10s %12.5f %12.5f %10.3f"
              % (arm, pooled(races, "ll", arm), pooled(races, "brier", arm),
                 pooled(races, "top1", arm)))
    report["pooled"] = {a: {m: pooled(races, m, a) for m in ("ll", "brier", "top1")}
                        for a in ARMS}

    print("\nper-race mean log-loss (09 sec9.3 requirement 1 -- "
          "a pooled improvement carried by one race is not a result)")
    pr = {a: per_race(races, "ll", a) for a in ARMS}
    print("  %-4s %-24s %9s %9s %9s %9s  %s" %
          ("rnd", "race", "layer", "ablation", "ladder", "static", "layer beats"))
    wins = {"ladder": 0, "static": 0, "ablation": 0}
    for race in races:
        r = race["round"]
        beats = [k for k in ("static", "ladder", "ablation") if pr["layer"][r] < pr[k][r]]
        for k in beats:
            wins[k] += 1
        print("  R%-3d %-24s %9.4f %9.4f %9.4f %9.4f  %s"
              % (r, race["name"][:24], pr["layer"][r], pr["ablation"][r],
                 pr["ladder"][r], pr["static"][r], ",".join(beats) or "-"))
    report["per_race_ll"] = pr
    report["per_race_wins"] = wins

    print("\n09 sec9.3 requirement 2 -- block bootstrap over whole races, %d blocks"
          % len(races))
    boots = {}
    for other in ("static", "ladder", "ablation"):
        b = block_bootstrap(races, "ll", "layer", other)
        boots[other] = b
        print("  layer - %-9s log-loss  %+.4f   95%% CI [%+.4f, %+.4f]  (negative = layer better)"
              % (other, b["point"], b["lo"], b["hi"]))
    report["bootstrap"] = boots

    print("\n09 sec9.2 -- log-loss against race progress "
          "(where in the race does the layer become better?)")
    curve = progress_curve(races, "ll")
    print("  %-10s %6s %9s %9s %9s %9s" % ("progress", "n", "layer", "ablation",
                                           "ladder", "static"))
    for row in curve:
        print("  %-10s %6d %9.4f %9.4f %9.4f %9.4f"
              % (row["bucket"], row["n"], row["layer"], row["ablation"],
                 row["ladder"], row["static"]))
    report["progress_curve"] = curve

    all_cp = [r for race in races for r in race["checkpoints"]]
    unreliable = [r for r in all_cp if not r["reliable"]]
    reasons = {}
    for r in unreliable:
        for reason in r["reasons"]:
            reasons[reason] = reasons.get(reason, 0) + 1
    pit_suppressed = sum(1 for r in all_cp if wp.REASON_PIT_OFFSET in r["reasons"])
    print("\n09 sec5.7 / sec10 -- reported regardless of outcome")
    print("  checkpoints                     : %d" % len(all_cp))
    print("  reliable = False                : %d (%.1f%%)"
          % (len(unreliable), 100.0 * len(unreliable) / max(len(all_cp), 1)))
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print("    %-28s: %d (%.1f%%)" % (reason, n, 100.0 * n / max(len(all_cp), 1)))
    print("  pit-cycle suppression fraction  : %.1f%%   (09 sec2.6 measured 34.5%% of "
          "race-laps carrying a stop)" % (100.0 * pit_suppressed / max(len(all_cp), 1)))
    print("  checkpoints with an in-domain 08 pair: %d (%.1f%%)"
          % (sum(1 for r in all_cp if r["n_in_domain"]),
             100.0 * sum(1 for r in all_cp if r["n_in_domain"]) / max(len(all_cp), 1)))
    print("  mean max se_mc                  : %.5f (half a market tick = %.5f)"
          % (sum(r["max_se"] for r in all_cp) / max(len(all_cp), 1), wp.MAX_SE_MC))
    report["reliability"] = {
        "n_checkpoints": len(all_cp), "n_unreliable": len(unreliable),
        "reasons": reasons, "pit_suppressed": pit_suppressed,
        "n_with_in_domain": sum(1 for r in all_cp if r["n_in_domain"]),
        "mean_max_se": sum(r["max_se"] for r in all_cp) / max(len(all_cp), 1)}

    print("\n09 sec9.3 requirement 3 -- Plackett-Luce log-likelihood of the realised "
          "finishing order (DIAGNOSTIC, not the winner metric)")
    pl = [r["pl_ll"] for r in all_cp]
    print("  mean per checkpoint: %.3f" % (sum(pl) / max(len(pl), 1)))
    report["pl_loglik_mean"] = sum(pl) / max(len(pl), 1)

    # 09 sec2.2's ladder is the cheapest sanity assertion in the spec.
    late = [r for r in all_cp if r["progress"] >= 0.9]
    if late:
        print("\n09 sec2.2 -- leader's p_win in the closing tenth of the race "
              "(measured 120/120 conversions inside 10 laps to go)")
        print("  mean leader p_win at progress >= 0.90: %.3f over %d checkpoints"
              % (sum(r["p_leader"] for r in late) / len(late), len(late)))
        report["late_leader_p_win"] = sum(r["p_leader"] for r in late) / len(late)

    mkt = market_prices()
    dutch = next((r for r in races if r["round"] == 12), None)
    if mkt and dutch:
        w = dutch["winner"]
        ml = log_loss(mkt, w)
        print("\n09 sec10 baseline 4 -- the market, on the 2026 Dutch GP only.")
        print("  COLOUR ON ONE RACE, NEVER A BASELINE (05 sec6.3): these are pre-race")
        print("  prices held constant, so this compares a static market number against a")
        print("  live layer, and one race settles nothing (05 sec6.4).")
        print("  market  log-loss %.4f | layer %.4f | static Lane A %.4f"
              % (ml, pr["layer"][12], pr["static"][12]))
        report["market_dutch"] = {"market_ll": ml, "layer_ll": pr["layer"][12],
                                  "static_ll": pr["static"][12]}

    print("\n" + "=" * 78)
    print("09 sec10 -- PRE-REGISTERED VERDICT")
    print("=" * 78)
    beat_static = report["pooled"]["layer"]["ll"] < report["pooled"]["static"]["ll"]
    beat_ladder = report["pooled"]["layer"]["ll"] < report["pooled"]["ladder"]["ll"]
    n_races = len(races)
    per_race_ok = wins["ladder"] >= 6 and wins["static"] >= 6
    success = beat_static and beat_ladder and per_race_ok
    print("  beats baseline 1 (static Lane A) on pooled log-loss : %s" % beat_static)
    print("  beats baseline 2 (position ladder) on pooled log-loss: %s" % beat_ladder)
    print("  wins per-race vs static / ladder                     : %d / %d of %d (needs >= 6)"
          % (wins["static"], wins["ladder"], n_races))
    print("  => LAYER %s" % ("SUCCEEDS" if success else "DOES NOT SUCCEED"))
    ab = boots["ablation"]
    earns = ab["hi"] < 0.0
    print("  08 earns its place (ablation worse than full layer beyond the")
    print("  bootstrap width)                                     : %s" % earns)
    print("     layer - ablation = %+.5f, 95%% CI [%+.5f, %+.5f]"
          % (ab["point"], ab["lo"], ab["hi"]))
    report["verdict"] = {"beats_static": beat_static, "beats_ladder": beat_ladder,
                         "per_race_wins": wins, "success": success,
                         "overtake_model_earns_place": earns}

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1, sort_keys=True)
    print("\nwrote %s" % args.out)


if __name__ == "__main__":
    main()
