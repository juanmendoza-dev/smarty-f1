#!/usr/bin/env python3
"""Fit the Phase A3 conditional logit. 05-trained-model.md sec6/sec7.

Reads data/training/winner.csv and nothing else -- no network calls, no market
data, ever (sec1, sec4.5, sec9 assertion 7). The one column here that looks
market-ish is `p_a1`, and it is not: it is A1's *own* market-blind probability,
written by backfill.py through score.py's unchanged code path, and it is
baseline 1 of sec6.3. Do not "fix" the market-blindness assertion by deleting
it.

The model (sec3.1). A1 is already a conditional logit with hand-set
coefficients -- dividing the weighted sum by T inside the softmax distributes,
so beta_f = w_f_eff / T. A3 fits what A1 guessed:

    v_d   = sum_f beta_f * s_f,d              (no intercept, sec3.6)
    p_d   = exp(v_d) / sum_e exp(v_e)         (over the drivers in ONE race)

No intercept because a per-race or global intercept is a within-race constant
and cancels exactly out of the likelihood (sec3.2). Seven features, not eight:
F7 weather is dormant on every backfilled row, which makes it a within-race
constant in every race, which makes beta_weather *unidentified* rather than
merely imprecise (sec3.3).

The fit is hand-rolled in pure Python 3.9 (sec7, decided 2026-08-24) -- no
numpy, no scipy. Newton-Raphson with a backtracking line search on the
penalized objective. The NLL is convex and there are seven parameters over
~5,300 rows, so this converges in a handful of iterations and runs in seconds.
Writing the likelihood and its gradient by hand is the point of this phase.

Objective, per sec3.6 -- L2 on beta, not L1, because the seven features are
correlated (02 sec2.1: F1/F3/F8 partly measure the same thing) and the goal is
shrinkage across them, not selection:

    J(beta) = NLL(beta)/n_races + (lambda/2) * ||beta - mu||^2

Dividing the NLL by the race count is what makes `lambda` mean the same thing
on a 3-season training fold and on a 10-season one, so a strength chosen on the
sec6.1 dev folds transfers to the final fits. `mu` is the open decision in
sec10.1 -- shrink toward zero, or toward A1's implied beta? Both are
implemented and the choice is made by a validation-set comparison, per that
item's own instruction, not by argument. Note the two arms are identical at
lambda=0, so the comparison only has content at lambda > 0.

Validation is season-forward, never random k-fold (sec6.1). Random folds put
later races in the training set and leak the future through every season-long
feature. Two modes enforce sec6.1's "the final held-out period is touched
exactly once" structurally rather than by convention:

    --mode dev    (default) season-forward folds over the pre-holdout seasons
                  only. Never reads a holdout season. Hyperparameters are
                  chosen here.
    --mode final  the one run that evaluates HOLDOUT_SEASONS, using the
                  hyperparameters dev mode selected. Refuses to run on an
                  incomplete corpus.

Usage:
    python3 fit.py                                  # dev folds, preliminary
    python3 fit.py --json out.json                  # ... and record the fit
    python3 fit.py --mode final                     # the once-only holdout run
"""

import argparse
import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# score is imported for BASE_WEIGHTS and T only, to build the A1 prior without
# re-typing the locked constants. It pulls in lib.features/invariants/simulate,
# none of which touch the network -- no Jolpica, Open-Meteo, Polymarket or
# Kalshi client is reachable from this file.
import score
from lib.invariants import require

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MATRIX = os.path.join(REPO_ROOT, "data", "training", "winner.csv")

# sec4.2. Must stay in lockstep with backfill.FEATURES; test_fit.py asserts it.
FEATURES = ["grid", "team", "sprint", "driver_form", "track", "champ", "teammate"]
K = len(FEATURES)

# sec6.1 / sec9 assertion 8. The held-out period is a fixed set of seasons, not
# "the last N seasons of whatever has been backfilled so far" -- with rows still
# arriving, a count-based rule would silently name a different experiment every
# time it ran. 2024-2026 is the last third of the 2014-2026 corpus.
HOLDOUT_SEASONS = (2024, 2025, 2026)

# The first fold trains on this many seasons before anything is evaluated.
MIN_TRAIN_SEASONS = 3

# sec3.6. lambda=0 is included so "the data wanted no shrinkage" stays a
# reportable answer rather than an option the grid forbids; if it wins, say so.
# The top end runs well past where beta has visibly collapsed onto mu, so that
# a selection landing at the boundary means "the curve has plateaued" and not
# "the grid stopped too early" -- the report distinguishes the two.
LAMBDA_GRID = (0.0, 0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0)

PRIORS = ("zero", "a1")

# sec6.2/sec6.4: Brier is the primary metric and the success criterion, so it
# is also what hyperparameters are selected on. Top-1 accuracy is reported and
# deliberately never selected on -- it ignores calibration entirely.
SELECTION_METRIC = "brier"

# sec4.3: 264 rounds with a result across 2014-2026, verified live 2026-08-23.
EXPECTED_RACES = 264

NEWTON_MAX_ITER = 200
NEWTON_GRAD_TOL = 1e-9
# Keeps the Newton system solvable when lambda=0 and two features are nearly
# collinear within races. Small enough not to move a converged answer.
SOLVE_RIDGE = 1e-10
# log-loss clamp. p_winner is positive by construction; this only stops a
# denormal from turning a metric into -inf.
LOGLOSS_FLOOR = 1e-12

# sec9 assertion 7. Substring match on the lowercased header, so a future
# column called `polymarket_p` or `kalshi_yes_price` trips it too.
MARKET_TOKENS = ("market", "polymarket", "kalshi", "odds", "vig", "implied",
                 "price", "book", "volume", "liquidity", "midpoint")


def a1_implied_beta():
    """sec3.1: beta_f = w_f_eff / T. A1's coefficients, as A3 would write them.

    Uses the *base* weights, not sec3.1's printed table. That table is the
    Zandvoort instance -- m=1.15, sprint weekend -- and sec3.1 says plainly
    that A1 is "not one model but six", one per (m, sprint-regime) pair, while
    A3 fits a single pooled beta. D4 drops m and D3 drops the renormalization,
    so the pooled counterpart is the m=1.00 base weights.

    The weather entry is deleted rather than redistributed: the remaining seven
    are NOT renormalized to sum to 1.0. Rescaling them by 1/0.95 would change
    every coefficient to compensate for dropping a term that, by sec3.2,
    contributes exactly nothing to the likelihood in the first place.
    """
    return [score.BASE_WEIGHTS[f] / score.T for f in FEATURES]


class Race:
    """One choice set: every driver on the grid for one (season, round)."""

    __slots__ = ("season", "round", "date", "circuit_id", "codes", "x",
                 "win_index", "p_a1")

    def __init__(self, season, round_, date, circuit_id):
        self.season = season
        self.round = round_
        self.date = date
        self.circuit_id = circuit_id
        self.codes = []
        self.x = []
        self.win_index = None
        self.p_a1 = []

    @property
    def key(self):
        return (self.season, self.round)

    def __len__(self):
        return len(self.codes)


# ---------- loading ----------


def load_matrix(path, quiet=False):
    """Read winner.csv into a list of Race, in file order.

    Tolerates a torn or half-written trailing race. backfill.py appends rows
    live and flushes per race, so a read taken mid-run can legitimately catch a
    truncated final line or a race whose rows are only partly on disk. That is
    a property of the producer, not a corrupt file, and failing loudly on it
    would make this script unrunnable while a backfill is going. Anything
    malformed that is NOT the last race in the file is a real problem and
    raises.
    """
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)

    require(header, f"{path} has no header row")
    for col in FEATURES + ["season", "round", "label", "p_a1"]:
        require(col in header, f"{path} is missing required column {col!r}")

    # sec9 assertion 7: no market field anywhere in the fitting path. Checked
    # against the header rather than the design matrix so a market column can't
    # even reach the loader unnoticed.
    for col in header:
        for token in MARKET_TOKENS:
            require(token not in col.lower(),
                    f"market-derived column {col!r} present in {path}; the A3 fit is "
                    f"market-blind by construction (05 sec1, sec4.5, sec9 assertion 7)")

    races = []
    by_key = {}
    dropped = []
    for i, row in enumerate(rows):
        is_last = i == len(rows) - 1
        try:
            parsed = _parse_row(row)
        except (TypeError, ValueError, KeyError) as e:
            if is_last:
                dropped.append(("torn final line", repr(e)))
                break
            raise

        key = (parsed["season"], parsed["round"])
        race = by_key.get(key)
        if race is None:
            race = Race(parsed["season"], parsed["round"], row["race_date"],
                        row["circuit_id"])
            by_key[key] = race
            races.append(race)
        race.codes.append(row["driver_code"])
        race.x.append(parsed["x"])
        race.p_a1.append(parsed["p_a1"])
        if parsed["label"] == 1:
            race.win_index = len(race.codes) - 1

    # sec9 assertion 1, and the sum-to-1 half of assertion 9 for the A1 column.
    ok = []
    for i, race in enumerate(races):
        is_last = i == len(races) - 1
        problem = _race_problem(race)
        if problem is None:
            ok.append(race)
        elif is_last:
            dropped.append((f"{race.season} R{race.round}", problem))
        else:
            raise AssertionError(f"{race.season} R{race.round}: {problem}")

    if dropped and not quiet:
        for what, why in dropped:
            print(f"note: dropped {what} -- {why}")
        print("      (backfill.py appends live; a half-written trailing race is expected)")

    return ok


def _parse_row(row):
    x = []
    for f in FEATURES:
        raw = row.get(f)
        require(raw not in (None, ""), f"missing cell for feature {f!r}")
        v = float(raw)
        # sec9 assertion 2. NaN fails the self-comparison, which is the only
        # way to catch it -- every bounds check below would pass silently.
        require(v == v, f"feature {f!r} is NaN")
        require(-1e-9 <= v <= 1.0 + 1e-9, f"feature {f!r}={v} outside [0,1]")
        x.append(v)
    label = int(row["label"])
    require(label in (0, 1), f"label={label} is not 0 or 1")
    p = float(row["p_a1"])
    require(p == p and 0.0 <= p <= 1.0, f"p_a1={p} outside [0,1]")
    return {
        "season": int(row["season"]),
        "round": int(row["round"]),
        "label": label,
        "p_a1": p,
        "x": x,
    }


def _race_problem(race):
    if len(race) < 2:
        return f"choice set has {len(race)} driver(s)"
    if race.win_index is None:
        return "no label-1 row"
    total = sum(race.p_a1)
    if abs(total - 1.0) > 1e-6:
        return f"p_a1 sums to {total:.9f}, not 1.0 (05 sec9 assertion 9)"
    return None


def project(races, feature_indices):
    """A copy of `races` keeping only some feature columns.

    sec6.3's grid-only baseline is this model with one column, so it reuses the
    same fitter rather than a second implementation of the same likelihood.
    """
    out = []
    for r in races:
        c = Race(r.season, r.round, r.date, r.circuit_id)
        c.codes = list(r.codes)
        c.x = [[row[i] for i in feature_indices] for row in r.x]
        c.win_index = r.win_index
        c.p_a1 = list(r.p_a1)
        out.append(c)
    return out


# ---------- the model ----------


def probabilities(beta, x_rows):
    """Conditional-logit probabilities over one race's drivers.

    Subtracts max(v) before exponentiating -- standard softmax stability, same
    as score.py does at 02 sec5.4. The result is identical and cannot overflow.
    """
    v = [sum(b * xi for b, xi in zip(beta, x)) for x in x_rows]
    top = max(v)
    e = [math.exp(vi - top) for vi in v]
    s = sum(e)
    return [ei / s for ei in e]


def nll_and_gradient(beta, races):
    """Mean per-race negative log-likelihood and its analytic gradient.

    For race r with winner w, log p_w = v_w - logsumexp(v), and

        d(-log p_w) / d beta_f = E_p[x_f] - x_w,f

    i.e. the gap between what the model expects the winner's feature to look
    like and what it actually was. Summed over races, divided by the race
    count. Computed in the stable logsumexp form rather than by taking a log of
    a divided probability.
    """
    k = len(beta)
    nll = 0.0
    grad = [0.0] * k
    for r in races:
        v = [sum(b * xi for b, xi in zip(beta, x)) for x in r.x]
        top = max(v)
        e = [math.exp(vi - top) for vi in v]
        s = sum(e)
        nll -= v[r.win_index] - top - math.log(s)
        p = [ei / s for ei in e]
        xw = r.x[r.win_index]
        for f in range(k):
            exp_f = 0.0
            for j, pj in enumerate(p):
                exp_f += pj * r.x[j][f]
            grad[f] += exp_f - xw[f]
    n = len(races)
    return nll / n, [g / n for g in grad]


def nll_gradient_hessian(beta, races):
    """The above, plus the Hessian.

    d2(-log p_w) / d beta_f d beta_g = E_p[x_f x_g] - E_p[x_f] E_p[x_g]

    -- the within-race covariance of the features under the model's own
    probabilities. It does not depend on which driver won, which is why the
    Hessian is positive semi-definite and the problem is convex: a covariance
    matrix has no negative eigenvalues, and a sum of them has none either. The
    L2 term the caller adds makes it strictly positive definite.
    """
    k = len(beta)
    nll = 0.0
    grad = [0.0] * k
    hess = [[0.0] * k for _ in range(k)]
    for r in races:
        v = [sum(b * xi for b, xi in zip(beta, x)) for x in r.x]
        top = max(v)
        e = [math.exp(vi - top) for vi in v]
        s = sum(e)
        nll -= v[r.win_index] - top - math.log(s)
        p = [ei / s for ei in e]
        xw = r.x[r.win_index]
        ex = [0.0] * k
        for f in range(k):
            acc = 0.0
            for j, pj in enumerate(p):
                acc += pj * r.x[j][f]
            ex[f] = acc
            grad[f] += acc - xw[f]
        for f in range(k):
            for g in range(f, k):
                acc = 0.0
                for j, pj in enumerate(p):
                    acc += pj * r.x[j][f] * r.x[j][g]
                hess[f][g] += acc - ex[f] * ex[g]
    n = len(races)
    for f in range(k):
        for g in range(f, k):
            hess[f][g] /= n
            hess[g][f] = hess[f][g]
    return nll / n, [g / n for g in grad], hess


def penalized_objective(beta, races, lam, mu):
    nll, _ = nll_and_gradient(beta, races)
    return nll + 0.5 * lam * sum((b - m) ** 2 for b, m in zip(beta, mu))


def solve(a, b):
    """Gaussian elimination with partial pivoting. a is k x k, b is length k."""
    k = len(b)
    m = [list(row) + [b[i]] for i, row in enumerate(a)]
    for col in range(k):
        pivot = max(range(col, k), key=lambda r: abs(m[r][col]))
        require(abs(m[pivot][col]) > 1e-14,
                f"Newton system is singular at column {col}; features may be "
                f"collinear within races")
        m[col], m[pivot] = m[pivot], m[col]
        pv = m[col][col]
        for r in range(col + 1, k):
            factor = m[r][col] / pv
            if factor:
                for c in range(col, k + 1):
                    m[r][c] -= factor * m[col][c]
    out = [0.0] * k
    for r in range(k - 1, -1, -1):
        acc = m[r][k] - sum(m[r][c] * out[c] for c in range(r + 1, k))
        out[r] = acc / m[r][r]
    return out


def fit(races, lam, mu, k=None):
    """Newton-Raphson on the penalized objective. Returns (beta, info).

    Damped with a backtracking line search on J itself, not on the NLL -- with
    a penalty term in play those are different functions and halving against
    the wrong one can walk uphill. If a Newton step is not a descent direction
    (only reachable through numerical noise on a convex problem) it falls back
    to plain gradient descent for that iteration.
    """
    k = k if k is not None else len(mu)
    require(len(mu) == k, "prior mean has the wrong length")
    require(races, "cannot fit on an empty set of races")
    beta = list(mu) if lam > 0 else [0.0] * k

    obj = None
    for iteration in range(NEWTON_MAX_ITER):
        nll, grad, hess = nll_gradient_hessian(beta, races)
        obj = nll + 0.5 * lam * sum((b - m) ** 2 for b, m in zip(beta, mu))
        g = [grad[f] + lam * (beta[f] - mu[f]) for f in range(k)]
        if max(abs(gi) for gi in g) < NEWTON_GRAD_TOL:
            return beta, {"iterations": iteration, "objective": obj,
                          "nll": nll, "converged": True}

        h = [row[:] for row in hess]
        for f in range(k):
            h[f][f] += lam + SOLVE_RIDGE
        step = solve(h, [-gi for gi in g])
        if sum(s * gi for s, gi in zip(step, g)) >= 0:
            step = [-gi for gi in g]

        t = 1.0
        moved = False
        while t > 1e-14:
            cand = [beta[f] + t * step[f] for f in range(k)]
            if penalized_objective(cand, races, lam, mu) < obj:
                beta = cand
                moved = True
                break
            t *= 0.5
        if not moved:
            # Line search exhausted: we are at the optimum to machine precision.
            return beta, {"iterations": iteration, "objective": obj,
                          "nll": nll, "converged": True}

    return beta, {"iterations": NEWTON_MAX_ITER, "objective": obj,
                  "nll": nll, "converged": False}


# ---------- metrics (sec6.2) ----------


def evaluate(races, prob_fn):
    """Multi-class Brier, log-loss, top-1 accuracy, and calibration pairs.

    Brier uses 02 sec7's definition unchanged -- sum over drivers of
    (p_d - outcome_d)^2, summed not averaged within a race -- so these numbers
    are directly comparable to score.py's, to postrace.py's, and to the market
    Brier already persisted for the 2026 Dutch GP.
    """
    brier_total = 0.0
    logloss_total = 0.0
    top1 = 0
    pairs = []
    for r in races:
        p = prob_fn(r)
        total = sum(p)
        # sec9 assertion 9, on every predictor, every race.
        require(abs(total - 1.0) < 1e-6,
                f"{r.season} R{r.round}: probabilities sum to {total:.9f}, not 1.0")
        w = r.win_index
        brier_total += sum((p[j] - (1.0 if j == w else 0.0)) ** 2 for j in range(len(p)))
        logloss_total += -math.log(max(p[w], LOGLOSS_FLOOR))
        best = max(range(len(p)), key=lambda j: p[j])
        if best == w:
            top1 += 1
        for j, pj in enumerate(p):
            pairs.append((pj, 1 if j == w else 0))
    n = len(races)
    return {
        "n_races": n,
        "brier": brier_total / n,
        "logloss": logloss_total / n,
        "top1": top1 / n,
        "pairs": pairs,
    }


CALIBRATION_BINS = (0.0, 0.01, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 1.000001)


def calibration_curve(pairs):
    """sec6.2: bucket predicted probabilities, compare to realized win rate.

    Bins are hand-chosen rather than equal-width because winner probabilities
    are heavily massed near zero -- 19 of ~20 drivers lose every race, so
    equal-width bins would put ~95% of the rows in the first one and say
    nothing about the end of the range that matters.
    """
    out = []
    for i in range(len(CALIBRATION_BINS) - 1):
        lo, hi = CALIBRATION_BINS[i], CALIBRATION_BINS[i + 1]
        sel = [(p, y) for p, y in pairs if lo <= p < hi]
        if not sel:
            out.append({"lo": lo, "hi": min(hi, 1.0), "n": 0})
            continue
        out.append({
            "lo": lo,
            "hi": min(hi, 1.0),
            "n": len(sel),
            "mean_predicted": sum(p for p, _ in sel) / len(sel),
            "realized": sum(y for _, y in sel) / len(sel),
        })
    return out


def separation_check(races):
    """sec3.6: a feature that perfectly separates winners diverges.

    The failure mode is not an error -- it is a coefficient that grows until
    the optimizer stops -- so it has to be looked for rather than assumed away.
    Counts races where the winner is the strict argmax of each feature.
    """
    out = {}
    for f, name in enumerate(FEATURES):
        perfect = 0
        for r in races:
            vals = [row[f] for row in r.x]
            best = max(vals)
            if vals[r.win_index] == best and vals.count(best) == 1:
                perfect += 1
        out[name] = {"races_won_by_argmax": perfect, "n_races": len(races),
                     "separates": perfect == len(races)}
    return out


# ---------- splits (sec6.1) ----------


def seasons_of(races):
    return sorted({r.season for r in races})


def season_forward_folds(races, eval_seasons):
    """(train, test) per evaluated season: train on everything strictly earlier.

    An expanding window, split on whole seasons rather than a race count so no
    season lands half in and half out -- F2/F4/F6/F8 are within-season features
    and a mid-season cut puts near-identical rows on both sides (sec6.1).
    """
    folds = []
    for y in eval_seasons:
        train = [r for r in races if r.season < y]
        test = [r for r in races if r.season == y]
        if not test:
            continue
        if len({r.season for r in train}) < MIN_TRAIN_SEASONS:
            continue
        require_disjoint(train, test)
        folds.append((y, train, test))
    return folds


def require_disjoint(train, test):
    """sec9 assertion 8: train and test groups share no (season, round) key."""
    train_keys = {r.key for r in train}
    overlap = train_keys & {r.key for r in test}
    require(not overlap,
            f"train/test leak: {len(overlap)} shared (season, round) key(s), "
            f"e.g. {sorted(overlap)[:3]}")


def pooled(fold_results):
    """Pool per-fold metrics by race count, so a short season doesn't get the
    same weight as a full one. sec6.4 judges on the pooled number; the
    per-season breakdown exists to show whether an advantage is stable."""
    n = sum(f["n_races"] for f in fold_results)
    if not n:
        return {"n_races": 0, "brier": float("nan"), "logloss": float("nan"), "top1": float("nan")}
    return {
        "n_races": n,
        "brier": sum(f["brier"] * f["n_races"] for f in fold_results) / n,
        "logloss": sum(f["logloss"] * f["n_races"] for f in fold_results) / n,
        "top1": sum(f["top1"] * f["n_races"] for f in fold_results) / n,
    }


# ---------- the three predictors (sec6.3) ----------


GRID_INDEX = FEATURES.index("grid")
# sec6.3: the grid-only floor gets its single coefficient fitted, unregularized.
# "A baseline handicapped by an unfitted scale is not a floor, it is a strawman"
# -- and shrinking one coefficient toward an arbitrary prior is the same kind of
# handicap by another name.
GRID_ONLY_LAMBDA = 0.0


def run_fold(train, test, lam, mu):
    """Fit A3 and grid-only on `train`, score all three predictors on `test`."""
    beta, info = fit(train, lam, mu)
    grid_train = project(train, [GRID_INDEX])
    grid_beta, _ = fit(grid_train, GRID_ONLY_LAMBDA, [0.0], k=1)

    a3 = evaluate(test, lambda r: probabilities(beta, r.x))
    a1 = evaluate(test, lambda r: list(r.p_a1))
    grid_only = evaluate(project(test, [GRID_INDEX]),
                         lambda r: probabilities(grid_beta, r.x))
    return {
        "beta": beta,
        "beta_grid_only": grid_beta,
        "fit_info": info,
        "a3": a3,
        "a1": a1,
        "grid_only": grid_only,
    }


def sweep(races, eval_seasons):
    """Every (prior, lambda) pair over the season-forward folds.

    Both arms are reported, not just the winner: sec10.1 is an open roadmap
    item this run exists to close, so the evidence has to be legible. The arms
    are identical at lambda=0 by construction -- (lambda/2)*||beta-mu||^2 is
    zero whatever mu is -- so only the lambda>0 rows carry the comparison.
    """
    mus = {"zero": [0.0] * K, "a1": a1_implied_beta()}
    results = []
    for prior in PRIORS:
        for lam in LAMBDA_GRID:
            folds = []
            for _y, train, test in season_forward_folds(races, eval_seasons):
                beta, _ = fit(train, lam, mus[prior])
                folds.append(evaluate(test, lambda r, b=beta: probabilities(b, r.x)))
            results.append({"prior": prior, "lam": lam, **pooled(folds)})
    return results


def select(sweep_results):
    """Lowest pooled dev Brier wins (SELECTION_METRIC), log-loss breaks ties.

    At lambda=0 the two priors give identical numbers, so prefer the "zero" arm
    there -- reporting "the A1 prior won" off a row where the prior had no
    effect would be a false finding.
    """
    def rank(r):
        return (round(r[SELECTION_METRIC], 12), round(r["logloss"], 12),
                0 if r["prior"] == "zero" else 1, r["lam"])
    return min(sweep_results, key=rank)


# ---------- corpus checks ----------


def corpus_report(races):
    """Per-season race counts and any gaps in the round numbering.

    A missing round is the signature of the sec5 backfill's IndexError bug
    (00-roadmap.md's Phase A3 entry: ~11 races across 2021-2024, and not
    randomly distributed -- it removed three specific circuits). Those get
    refilled by a second backfill pass, so a hole here means the corpus is not
    finished, whatever `ps aux` says about the process.
    """
    per_season = {}
    for r in races:
        per_season.setdefault(r.season, []).append(r.round)
    out = []
    for season in sorted(per_season):
        rounds = sorted(per_season[season])
        missing = [n for n in range(1, max(rounds) + 1) if n not in set(rounds)]
        out.append({"season": season, "n_races": len(rounds),
                    "max_round": max(rounds), "missing_rounds": missing})
    return out


def require_complete_corpus(races):
    """sec6.4 verdicts need the whole corpus. Refuse `--mode final` without it."""
    report = corpus_report(races)
    holes = [(c["season"], c["missing_rounds"]) for c in report if c["missing_rounds"]]
    total = sum(c["n_races"] for c in report)
    detail = "; ".join(f"{s} missing R{r}" for s, r in holes) or "no round gaps"
    require(
        total == EXPECTED_RACES and not holes,
        f"corpus is incomplete: {total} races, expected {EXPECTED_RACES} (05 sec4.3); "
        f"{detail}. Finish the backfill (including the second pass that refills the "
        f"races lost to the track-history IndexError) before running --mode final -- "
        f"a sec6.4 verdict on a holed corpus is not a result.",
    )


# ---------- reporting ----------


def fmt(v, nd=4):
    return "n/a" if v != v else f"{v:.{nd}f}"


def print_beta(label, beta, names=FEATURES, against=None):
    """Print a coefficient vector, optionally beside A1's implied one.

    The delta column is what sec3.6 says A3 is actually being asked -- "how far
    did the data move the weights?" -- so it is printed rather than left for
    the reader to subtract by hand.
    """
    print(f"  {label}")
    if against is None:
        for name, b in zip(names, beta):
            print(f"    {name:<12} {b:+8.4f}")
        return
    print(f"    {'feature':<12} {'fitted':>8} {'A1':>8} {'delta':>8}")
    for name, b, a in zip(names, beta, against):
        print(f"    {name:<12} {b:>+8.4f} {a:>+8.4f} {b - a:>+8.4f}")


def print_metrics_row(label, m):
    print(f"  {label:<12} brier {fmt(m['brier'])}   logloss {fmt(m['logloss'])}   "
          f"top-1 {fmt(m['top1'], 3)}   ({m['n_races']} races)")


def print_calibration(pairs):
    print("  bucket            n     mean p    realized")
    for b in calibration_curve(pairs):
        if not b["n"]:
            continue
        print(f"  [{b['lo']:.2f},{b['hi']:.2f})  {b['n']:>6}    "
              f"{b['mean_predicted']:.4f}     {b['realized']:.4f}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", default=DEFAULT_MATRIX)
    ap.add_argument("--mode", choices=("dev", "final"), default="dev",
                    help="dev: season-forward folds before the holdout, hyperparameters "
                         "chosen here. final: the once-only run that touches the holdout.")
    ap.add_argument("--holdout", default=",".join(str(s) for s in HOLDOUT_SEASONS),
                    help="comma-separated seasons held out of every dev fold")
    ap.add_argument("--json", help="write the full result to this path")
    args = ap.parse_args()

    holdout = tuple(int(s) for s in args.holdout.split(",") if s.strip())

    races = load_matrix(args.matrix)
    require(races, f"{args.matrix} yielded no usable races")
    all_seasons = seasons_of(races)

    print(f"\n=== Phase A3 fit ({args.mode} mode) === 05-trained-model.md sec6/sec7")
    print(f"matrix: {os.path.relpath(args.matrix, REPO_ROOT)}")
    print(f"{len(races)} races, {sum(len(r) for r in races)} driver-race rows, "
          f"seasons {all_seasons[0]}-{all_seasons[-1]}")

    print("\n--- corpus ---")
    coverage = corpus_report(races)
    for c in coverage:
        gaps = (f"  MISSING R{c['missing_rounds']}" if c["missing_rounds"] else "")
        print(f"  {c['season']}  {c['n_races']:>2} races (max round {c['max_round']:>2}){gaps}")
    incomplete = (sum(c["n_races"] for c in coverage) != EXPECTED_RACES
                  or any(c["missing_rounds"] for c in coverage))
    if incomplete:
        print(f"  corpus is INCOMPLETE ({sum(c['n_races'] for c in coverage)} of "
              f"{EXPECTED_RACES} races). Every number below is PRELIMINARY.")

    dev_seasons = [s for s in all_seasons if s not in holdout]
    require(not (set(dev_seasons) & set(holdout)),
            "dev seasons overlap the holdout -- sec6.1 requires the held-out period be "
            "touched exactly once")
    dev_folds = [y for y in dev_seasons][MIN_TRAIN_SEASONS:]
    print(f"\nholdout seasons (untouched in dev mode): {list(holdout)}")
    print(f"dev seasons: {dev_seasons}")
    print(f"dev evaluation folds: {dev_folds}")

    dev_races = [r for r in races if r.season in set(dev_seasons)]
    require(dev_races, "no races outside the holdout to develop on")
    require(not any(r.season in holdout for r in dev_races),
            "a holdout season leaked into the dev set")

    # --- sec10.1: the regularization prior, decided on validation, not argument
    print("\n--- sec10.1: regularization prior and strength, on the dev folds ---")
    print("  (the two priors are identical at lambda=0; only lambda>0 rows compare them)")
    sweep_results = sweep(dev_races, dev_folds)
    print(f"  {'prior':<6} {'lambda':>7}   {'brier':>8}  {'logloss':>8}  {'top-1':>6}")
    for r in sweep_results:
        print(f"  {r['prior']:<6} {r['lam']:>7}   {r['brier']:>8.5f}  "
              f"{r['logloss']:>8.5f}  {r['top1']:>6.3f}")
    chosen = select(sweep_results)
    print(f"\n  selected on pooled dev {SELECTION_METRIC}: prior={chosen['prior']} "
          f"lambda={chosen['lam']}")
    if chosen["lam"] == 0.0:
        print("  note: lambda=0 won, so the prior choice carries no content -- the dev "
              "folds asked for no shrinkage at all.")
    if chosen["lam"] == max(LAMBDA_GRID):
        print(f"  WARNING: the selected lambda is the top of the grid. Read the sweep "
              f"column above before quoting this: if the last few rows are flat, beta has "
              f"already collapsed onto the prior and a wider grid changes nothing; if it "
              f"is still falling, the grid stopped too early and needs extending.")

    mu = [0.0] * K if chosen["prior"] == "zero" else a1_implied_beta()

    print("\n  A1's implied beta (sec3.1, base weights / T -- the informative prior):")
    print_beta("", a1_implied_beta())

    # --- separation check (sec3.6)
    print("\n--- sec3.6 separation check (on the dev set) ---")
    sep = separation_check(dev_races)
    flagged = [n for n, s in sep.items() if s["separates"]]
    for name in FEATURES:
        s = sep[name]
        print(f"  {name:<12} winner is strict argmax in {s['races_won_by_argmax']:>4} "
              f"of {s['n_races']} races")
    if flagged:
        print(f"  WARNING: {flagged} separates perfectly; its coefficient will diverge.")
    else:
        print("  no feature separates perfectly -- no coefficient is driven to infinity.")

    eval_seasons = dev_folds if args.mode == "dev" else [s for s in holdout if s in all_seasons]
    if args.mode == "final":
        require_complete_corpus(races)
        require(eval_seasons, f"none of the holdout seasons {list(holdout)} are in the matrix")
        source = races
    else:
        source = dev_races

    label = "DEV FOLDS (preliminary -- the holdout is untouched)" if args.mode == "dev" \
        else "HELD-OUT SEASONS (sec6.1: touched exactly once)"
    print(f"\n--- {label} ---")

    folds = season_forward_folds(source, eval_seasons)
    require(folds, "no evaluable folds")

    per_season = []
    last = None
    for y, train, test in folds:
        require_disjoint(train, test)
        require(max(r.season for r in train) < y, "a fold trained on a season at or after "
                                                  "the one it evaluates")
        res = run_fold(train, test, chosen["lam"], mu)
        per_season.append((y, res))
        last = res
        print(f"\n  season {y}  (trained on {min(r.season for r in train)}-"
              f"{max(r.season for r in train)}, {len(train)} races)")
        print_metrics_row("A3", res["a3"])
        print_metrics_row("A1", res["a1"])
        print_metrics_row("grid-only", res["grid_only"])

    pooled_a3 = pooled([res["a3"] for _, res in per_season])
    pooled_a1 = pooled([res["a1"] for _, res in per_season])
    pooled_gr = pooled([res["grid_only"] for _, res in per_season])

    print(f"\n  POOLED over {len(per_season)} season(s)")
    print_metrics_row("A3", pooled_a3)
    print_metrics_row("A1", pooled_a1)
    print_metrics_row("grid-only", pooled_gr)

    print("\n--- fitted coefficients (last fold, the largest training set) ---")
    print_beta(f"A3 (prior={chosen['prior']}, lambda={chosen['lam']})", last["beta"],
               against=a1_implied_beta())
    print_beta("grid-only", last["beta_grid_only"], names=["grid"])
    print(f"  newton: {last['fit_info']['iterations']} iterations, "
          f"converged={last['fit_info']['converged']}")

    # The honesty note sec10.1 asks for. If validation selects the A1 prior at a
    # strength that pins beta to it, A3 has stopped being a fitted model and the
    # sec6.4 comparison no longer measures fitting at all -- it measures the two
    # structural differences that remain (D4 drops m, D3 drops the sprint
    # renormalization). Say so rather than quoting the Brier gap as a win for
    # estimation.
    collapsed = chosen["prior"] == "a1" and max(
        abs(b - a) for b, a in zip(last["beta"], a1_implied_beta())) < 0.01
    if collapsed:
        print("\n  NOTE: beta has collapsed onto A1's implied coefficients. The dev folds "
              "preferred maximal shrinkage toward the hand-set weights over anything the "
              "data fitted, so any A3-vs-A1 gap below is attributable to the two remaining "
              "structural differences -- no per-circuit m (D4) and no sprint-weekend "
              "renormalization (D3) -- and not to estimation.")

    print("\n--- calibration (sec6.2), A3 over the evaluated seasons ---")
    print_calibration([p for _, res in per_season for p in res["a3"]["pairs"]])

    # sec6.4: stated before the numbers existed, so it cannot move afterwards.
    beats_brier = pooled_a3["brier"] < pooled_a1["brier"]
    loses_logloss = pooled_a3["logloss"] > pooled_a1["logloss"]
    print("\n--- sec6.4 success criterion ---")
    print(f"  beats A1 on pooled Brier:      {beats_brier} "
          f"({fmt(pooled_a3['brier'], 5)} vs {fmt(pooled_a1['brier'], 5)})")
    print(f"  does not lose on log-loss:     {not loses_logloss} "
          f"({fmt(pooled_a3['logloss'], 5)} vs {fmt(pooled_a1['logloss'], 5)})")
    print(f"  clears the grid-only floor:    {pooled_a3['brier'] < pooled_gr['brier']} "
          f"({fmt(pooled_a3['brier'], 5)} vs {fmt(pooled_gr['brier'], 5)})")
    if args.mode == "final":
        verdict = "A3 SUCCEEDS" if (beats_brier and not loses_logloss) else \
            "A3 does not succeed -- A1 stays production, A3's coefficients are the finding"
        print(f"\n  VERDICT: {verdict}")
    else:
        print("\n  NOT A VERDICT. sec6.4 judges on the held-out seasons, which dev mode "
              "never reads. Re-run with --mode final on a complete corpus.")

    if args.json:
        payload = {
            "mode": args.mode,
            "matrix": os.path.relpath(args.matrix, REPO_ROOT),
            "corpus": coverage,
            "corpus_complete": not incomplete,
            "holdout_seasons": list(holdout),
            "dev_seasons": dev_seasons,
            "evaluated_seasons": [y for y, _ in per_season],
            "features": FEATURES,
            "a1_implied_beta": a1_implied_beta(),
            "sweep": sweep_results,
            "selected": {"prior": chosen["prior"], "lambda": chosen["lam"],
                         "metric": SELECTION_METRIC},
            "separation": sep,
            "per_season": [
                {"season": y,
                 "a3": {k: v for k, v in res["a3"].items() if k != "pairs"},
                 "a1": {k: v for k, v in res["a1"].items() if k != "pairs"},
                 "grid_only": {k: v for k, v in res["grid_only"].items() if k != "pairs"},
                 "beta": res["beta"], "beta_grid_only": res["beta_grid_only"]}
                for y, res in per_season
            ],
            "pooled": {"a3": pooled_a3, "a1": pooled_a1, "grid_only": pooled_gr},
            "calibration": calibration_curve(
                [p for _, res in per_season for p in res["a3"]["pairs"]]),
            "criterion": {"beats_a1_on_brier": beats_brier,
                          "loses_to_a1_on_logloss": loses_logloss,
                          "beats_grid_only_on_brier": pooled_a3["brier"] < pooled_gr["brier"]},
        }
        with open(args.json, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nwrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
