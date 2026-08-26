"""Test the 05-trained-model.md sec3.5 fitted tier interaction, on dev folds only.

sec3.5 says the honest v1 drops the per-circuit multiplier `m` entirely (D4)
and, once v1 fits cleanly, replaces it with a fitted interaction on `s_grid`
using the three tiers `02` sec5.1 already defines -- two extra parameters, not
33. This script builds that 9-feature design (the 7 sec4.2 features plus
grid_x_hard and grid_x_easy) and compares it against the 7-feature v1 model,
using fit.py's own primitives so the two share every line of likelihood,
gradient, Hessian, and evaluation code. It never reads a HOLDOUT_SEASONS
season -- same dev-fold discipline as `fit.py --mode dev` (05 sec6.1).

Standalone verification tooling, same pattern as weather_backtest.py: it does
not change fit.py or its 66 tests, and running it cannot affect the fit those
tests check. If the tier interaction is adopted, it becomes fit.py's design;
until then this is the evidence that decides sec3.5 and sec10 item 2.

Interpreting the two new coefficients (sec3.5's own criterion):
    beta_hard > 0, beta_easy < 0, ordered as 02 sec5.1 predicts (grid matters
        MORE where m=1.15, LESS where m=0.85)  -> the hand-set judgement is
        vindicated; replace it with the fitted numbers.
    flat or inverted                            -> 02 sec10.1 has its answer;
        `m` should stay dropped, not just in v1 but for good.

Usage:  python3 tier_interaction_backtest.py [--matrix path] [--json out.json]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fit
from lib.circuits import tier_for
from lib.invariants import require

TIER_FEATURES = ["grid_x_hard", "grid_x_easy"]
DEV_EVAL_SEASONS_FLOOR = fit.MIN_TRAIN_SEASONS


def add_tier_columns(races):
    """Return copies of `races` with two extra feature columns appended.

    grid_x_hard is s_grid where the circuit's tier is "hard" (m=1.15) and 0
    elsewhere; grid_x_easy mirrors that for "easy" (m=0.85). "default" --
    both the explicitly m=1.00 circuits and the 17 the backfill added that
    were never tiered by hand (lib.circuits.tier_for) -- gets zero on both,
    which is the "explicit default tier bucket" sec3.5 and sec10 item 2 call
    for: it makes no claim about those circuits' difficulty, rather than
    guessing one to fill the interaction in.
    """
    out = []
    for r in races:
        c = fit.Race(r.season, r.round, r.date, r.circuit_id)
        c.codes = list(r.codes)
        c.win_index = r.win_index
        c.n_wins = r.n_wins
        c.p_a1 = list(r.p_a1)
        tier = tier_for(r.circuit_id)
        hard = 1.0 if tier == "hard" else 0.0
        easy = 1.0 if tier == "easy" else 0.0
        c.x = [row + [row[fit.GRID_INDEX] * hard, row[fit.GRID_INDEX] * easy]
               for row in r.x]
        out.append(c)
    return out


def sweep_extended(races, eval_seasons, mus):
    results = []
    for prior in fit.PRIORS:
        for lam in fit.LAMBDA_GRID:
            folds = []
            for _y, train, test in fit.season_forward_folds(races, eval_seasons):
                beta, _ = fit.fit(train, lam, mus[prior])
                folds.append(fit.evaluate(test, lambda r, b=beta: fit.probabilities(b, r.x)))
            results.append({"prior": prior, "lam": lam, **fit.pooled(folds)})
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", default=fit.DEFAULT_MATRIX)
    ap.add_argument("--json", help="write the full result to this path")
    args = ap.parse_args()

    base_races = fit.load_matrix(args.matrix)
    require(base_races, f"{args.matrix} yielded no usable races")
    all_seasons = fit.seasons_of(base_races)

    dev_seasons = [s for s in all_seasons if s not in fit.HOLDOUT_SEASONS]
    dev_folds = dev_seasons[DEV_EVAL_SEASONS_FLOOR:]
    print("=== sec3.5 tier interaction backtest (dev folds only) ===")
    print(f"matrix: {os.path.relpath(args.matrix, fit.REPO_ROOT)}")
    print(f"holdout seasons (untouched): {list(fit.HOLDOUT_SEASONS)}")
    print(f"dev evaluation folds: {dev_folds}")

    dev_base = [r for r in base_races if r.season in set(dev_seasons)]
    dev_ext = add_tier_columns(dev_base)

    feature_names = fit.FEATURES + TIER_FEATURES
    mu_a1 = fit.a1_implied_beta() + [0.0, 0.0]  # A1 has no tier term -- extension shrinks to 0
    mu_zero = [0.0] * len(feature_names)
    mus = {"zero": mu_zero, "a1": mu_a1}

    print(f"\n--- baseline (7-feature v1, from fit.py --mode dev) ---")
    base_sweep = fit.sweep(dev_base, dev_folds)
    base_chosen = fit.select(base_sweep)
    base_mu = [0.0] * fit.K if base_chosen["prior"] == "zero" else fit.a1_implied_beta()
    base_folds = []
    for y, train, test in fit.season_forward_folds(dev_base, dev_folds):
        beta, _ = fit.fit(train, base_chosen["lam"], base_mu)
        base_folds.append(fit.evaluate(test, lambda r, b=beta: fit.probabilities(b, r.x)))
    base_pooled = fit.pooled(base_folds)
    print(f"  selected: prior={base_chosen['prior']} lambda={base_chosen['lam']}")
    print(f"  pooled brier {base_pooled['brier']:.5f}  logloss {base_pooled['logloss']:.5f}")

    print(f"\n--- extended (9-feature: + grid_x_hard, grid_x_easy) ---")
    ext_sweep = sweep_extended(dev_ext, dev_folds, mus)
    for r in ext_sweep:
        print(f"  {r['prior']:<6} {r['lam']:>7}   {r['brier']:>8.5f}  {r['logloss']:>8.5f}")
    ext_chosen = fit.select(ext_sweep)
    ext_mu = mu_zero if ext_chosen["prior"] == "zero" else mu_a1
    print(f"\n  selected: prior={ext_chosen['prior']} lambda={ext_chosen['lam']}")
    if ext_chosen["lam"] == max(fit.LAMBDA_GRID):
        print("  WARNING: selected lambda is the top of the grid -- read the sweep column "
              "above; if the tail is flat, beta has collapsed onto the prior.")

    ext_folds = []
    last_beta = None
    last_train_range = None
    for y, train, test in fit.season_forward_folds(dev_ext, dev_folds):
        beta, info = fit.fit(train, ext_chosen["lam"], ext_mu)
        ext_folds.append(fit.evaluate(test, lambda r, b=beta: fit.probabilities(b, r.x)))
        last_beta = beta
        last_train_range = (min(r.season for r in train), max(r.season for r in train))
    ext_pooled = fit.pooled(ext_folds)

    print(f"\n  pooled brier {ext_pooled['brier']:.5f}  logloss {ext_pooled['logloss']:.5f}")
    print(f"\n  fitted coefficients (last fold, trained on {last_train_range[0]}-{last_train_range[1]}):")
    for name, b, m in zip(feature_names, last_beta, ext_mu):
        print(f"    {name:14s} {b:+.4f}   (prior {m:+.4f})")

    hard_beta = last_beta[feature_names.index("grid_x_hard")]
    easy_beta = last_beta[feature_names.index("grid_x_easy")]
    print(f"\n--- sec3.5 verdict ---")
    print(f"  grid_x_hard = {hard_beta:+.4f}  (predicted > 0: grid matters MORE, m=1.15)")
    print(f"  grid_x_easy = {easy_beta:+.4f}  (predicted < 0: grid matters LESS, m=0.85)")
    ordered = hard_beta > 0 and easy_beta < 0
    print(f"  ordered as 02 sec5.1 predicted: {ordered}")
    print(f"  extended vs base pooled brier: {ext_pooled['brier']:.5f} vs {base_pooled['brier']:.5f} "
          f"({'improves' if ext_pooled['brier'] < base_pooled['brier'] else 'does not improve'})")

    if args.json:
        payload = {
            "dev_folds": dev_folds,
            "base": {"selected": base_chosen, "pooled": base_pooled},
            "extended": {"sweep": ext_sweep, "selected": ext_chosen, "pooled": ext_pooled,
                         "beta": last_beta, "features": feature_names},
            "verdict": {"grid_x_hard": hard_beta, "grid_x_easy": easy_beta,
                        "ordered_as_predicted": ordered},
        }
        with open(args.json, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nwrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
