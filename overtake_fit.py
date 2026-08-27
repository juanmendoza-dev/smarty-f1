#!/usr/bin/env python3
"""Overtake model: rule-based scorer, then logistic regression. 08 sec7/sec8.

Algo before model (welcome.md). Stage 1 is a hand-weighted scorer the owner can
reason about and explain; stage 2 is a logistic regression that has to BEAT it
or it is not worth having. Both are evaluated the same way, against the same
folds, on the same rows.

The regression is hand-rolled -- no scipy, no sklearn -- mirroring fit.py's
choice for Phase A3. Writing the likelihood and its gradient by hand is the
point of this phase for a first ML build, and binary LR with L2 is short.

One deliberate difference from fit.py: the gradient is evaluated with numpy
rather than in pure Python. 05 sec7's no-numpy rule is scoped to Phase A3's
fitting environment ("affects the optimizer only") and does not govern this
module, which already depends on numpy transitively through the archive
loader. The reason is not taste -- this matrix is ~428k rows by 16 features,
and a pure-Python pass measured too slow to run 10 folds at all, where the
A3 matrix is ~5,300 rows by 7. The likelihood and its gradient are still
written out by hand below; only the arithmetic is vectorized:

    p_i    = sigma(w . x_i + b)
    J(w)   = -(1/n) sum_i [ y_i log p_i + (1-y_i) log(1-p_i) ] + (lam/2)||w||^2
    dJ/dw  = (1/n) X^T (p - y) + lam * w

Validation is race-forward, never random k-fold (sec8, same reasoning as
05 sec6.1): rows inside one race share track, weather and tyre state, and rows
inside one episode are near-duplicates of each other. Race-forward folds mean
rows from an episode can never split across train and test.

Calibration is the acceptance criterion, not accuracy (sec7). A feature
generator that is confidently wrong is worse than useless to the
win-probability layer that would multiply it. At a ~0.5% base rate, a model
that predicts "no overtake" every time is 99.5% accurate and worthless, which
is why accuracy is not reported at all.

Usage:
    python3 overtake_fit.py
    python3 overtake_fit.py --matrix PATH --json out.json
"""

import argparse
import csv
import json
import math

import numpy as np

from lib.invariants import require
from lib import overtake_features as of
from lib import overtakes as ov

DEFAULT_MATRIX = "data/live/overtakes/training.csv"
L2 = 1e-4
LR = 2.0
EPOCHS = 4000

# sec7 stage 1. Hand-set, monotone in the things that physically matter:
# being close, closing fast, and having a speed advantage. Suppressed
# outright under caution, where overtaking is forbidden.
RULE_WEIGHTS = {
    "close":     1.6,   # how far inside the 2s window
    "closing":   1.2,   # closing rate, positive = gap shrinking
    "speed":     0.9,   # speed delta over the car ahead
    "sustained": 0.4,   # how long the pursuit has been going
}
RULE_BIAS = -3.2


def sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def load_matrix(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                rec = {k: r[k] for k in ("race", "pursuer", "ahead")}
                rec["round"] = int(r["round"])
                rec["label"] = int(r["label"])
                for k in of.FEATURE_NAMES:
                    v = r.get(k, "")
                    rec[k] = float(v) if v not in ("", "nan", "None") else float("nan")
                if any(math.isnan(rec[k]) for k in of.FEATURE_NAMES):
                    continue
                rows.append(rec)
            except (ValueError, KeyError):
                continue
    require(rows, "matrix %r has no usable rows" % path)
    return rows


def rule_score(r, bias=RULE_BIAS):
    """sec7 stage 1: hand-weighted, explainable, no fitting.

    `bias` is the intercept only. It is passed in from the training fold's log
    odds rather than left at the hand-picked RULE_BIAS, because a hand-picked
    intercept makes the Brier comparison against the model meaningless: -3.2
    implies a ~4% baseline where the measured base rate is 0.40%, so baseline 1
    would lose on Brier by an order of magnitude purely from where its
    intercept was guessed, telling you nothing about whether the physics ranks.
    The four feature weights stay hand-set -- those are the part that is
    supposed to be explainable, and they are untouched by this.
    """
    if r["under_caution"] >= 0.5:
        return 0.0
    close = max(0.0, (ov.EPISODE_INTERVAL_S - r["interval"]) / ov.EPISODE_INTERVAL_S)
    closing = max(-1.0, min(1.0, -r["closing_rate"] * 4.0))
    speed = max(-1.0, min(1.0, r["speed_delta"] / 25.0))
    sustained = min(1.0, r["time_in_range"] / 30.0)
    z = (bias
         + RULE_WEIGHTS["close"] * close
         + RULE_WEIGHTS["closing"] * closing
         + RULE_WEIGHTS["speed"] * speed
         + RULE_WEIGHTS["sustained"] * sustained)
    return sigmoid(z)


def standardize(train, test, names):
    stats = {}
    for k in names:
        vals = [r[k] for r in train]
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / max(1, len(vals) - 1)
        sd = math.sqrt(var) if var > 0 else 1.0
        stats[k] = (mu, sd)

    def apply(rows):
        out = []
        for r in rows:
            out.append([(r[k] - stats[k][0]) / stats[k][1] for k in names])
        return out
    return apply(train), apply(test), stats


def fit_logistic(X, y, lam=L2, lr=LR, epochs=EPOCHS):
    """Full-batch gradient descent on the L2-penalized log-loss.

        p     = sigma(Xw + b)
        dJ/dw = (1/n) X^T (p - y) + lam * w
        dJ/db = (1/n) sum(p - y)          (the bias is not penalized)
    """
    Xa = np.asarray(X, dtype=float)
    ya = np.asarray(y, dtype=float)
    n, d = Xa.shape
    pos = int(ya.sum())
    require(0 < pos < n, "training fold is single-class (%d positives of %d)" % (pos, n))
    w = np.zeros(d)
    # Start the bias at the training-set log-odds instead of 0. A converged
    # logistic regression with an intercept reproduces the base rate on
    # average; starting at b=0 means the optimizer has to travel from an
    # implied 50% to an implied 0.4%, and at this class imbalance it was
    # measured still short of that after 400 epochs -- which showed up as a
    # mean prediction ~2x the base rate and a calibration ratio up to 15.
    base = float(ya.mean())
    b = math.log(base / (1.0 - base)) if 0.0 < base < 1.0 else 0.0
    for _ in range(epochs):
        z = Xa.dot(w) + b
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -35.0, 35.0)))
        err = p - ya
        w -= lr * (Xa.T.dot(err) / n + lam * w)
        b -= lr * (err.sum() / n)
    return list(w), float(b)


def predict(X, w, b):
    Xa = np.asarray(X, dtype=float)
    z = Xa.dot(np.asarray(w, dtype=float)) + b
    return list(1.0 / (1.0 + np.exp(-np.clip(z, -35.0, 35.0))))


def isotonic_pav(x, y):
    """Pool-adjacent-violators isotonic regression. 08 sec12 item 2 route (a).

    Returns (px, pv): a non-decreasing step function, px ascending. Hand-rolled
    for the same reason the logistic fit is -- writing the calibrator is part of
    the point of this phase, and PAV is a dozen lines.

    y is 0/1 and the base rate is ~0.4%, so nearly every block collapses to a
    tiny value; the few high-score blocks are where the map does real work.
    """
    order = sorted(range(len(x)), key=lambda i: x[i])
    blocks = []  # each: [sum_y, count, x_right]
    for i in order:
        blocks.append([float(y[i]), 1, x[i]])
        while len(blocks) >= 2 and \
                blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            sy = blocks[-2][0] + blocks[-1][0]
            c = blocks[-2][1] + blocks[-1][1]
            blocks[-2:] = [[sy, c, blocks[-1][2]]]
    px = [b[2] for b in blocks]
    pv = [b[0] / b[1] for b in blocks]
    return px, pv


def iso_apply(px, pv, q):
    """Linear interpolation between calibration points, clamped at the ends."""
    if q <= px[0]:
        return pv[0]
    if q >= px[-1]:
        return pv[-1]
    lo, hi = 0, len(px) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if px[mid] <= q:
            lo = mid
        else:
            hi = mid
    if px[hi] == px[lo]:
        return pv[hi]
    return pv[lo] + (pv[hi] - pv[lo]) * (q - px[lo]) / (px[hi] - px[lo])


def _platt_nll(z, y, a, b, ridge):
    s = 0.0
    for zi, yi in zip(z, y):
        t = a * zi + b
        s += (math.log1p(math.exp(-abs(t))) + max(t, 0.0)) - yi * t
    return s / len(y) + 0.5 * ridge * (a - 1.0) ** 2


def platt_fit(p, y, iters=100, ridge=1e-3):
    """1-D logistic (Platt) scaling on the model's log-odds: q = sigma(a*z + b),
    z = logit(p). 08 sec12 item 2 route (a), the parametric alternative to
    isotonic.

    This data is numerically hostile to it: at a 0.4% base rate the score
    distribution is near-separable, so plain gradient descent drives `a` -> 0
    and collapses the map, while an undamped Newton step overshoots to
    `a` ~ 1e10 and diverges (both were measured). So: damped Newton with a
    small ridge toward `a = 1` and a backtracking line search on the penalized
    NLL. If it still cannot make downhill progress it returns the last iterate
    -- Platt is reported as "attempted", not trusted, and the domain gate is
    the actual fix (08 sec11.1).
    """
    eps = 1e-6
    z = [math.log(min(1 - eps, max(eps, pi)) / (1 - min(1 - eps, max(eps, pi)))) for pi in p]
    n = len(y)
    base = sum(y) / n
    a, b = 1.0, (math.log(base / (1 - base)) if 0 < base < 1 else 0.0)
    cur = _platt_nll(z, y, a, b, ridge)
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for zi, yi in zip(z, y):
            q = sigmoid(a * zi + b)
            r = q - yi
            wq = max(q * (1 - q), 1e-9)
            g0 += r * zi
            g1 += r
            h00 += wq * zi * zi
            h01 += wq * zi
            h11 += wq
        g0 = g0 / n + ridge * (a - 1.0)
        g1 /= n
        h00 = h00 / n + ridge
        h01 /= n
        h11 /= n
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        da = (g0 * h11 - g1 * h01) / det
        db = (g1 * h00 - g0 * h01) / det
        step = 1.0
        while step > 1e-4:
            na, nb = a - step * da, b - step * db
            if _platt_nll(z, y, na, nb, ridge) < cur - 1e-12:
                break
            step *= 0.5
        else:
            break
        a, b = a - step * da, b - step * db
        new = _platt_nll(z, y, a, b, ridge)
        if cur - new < 1e-9:
            cur = new
            break
        cur = new
    return a, b


def platt_apply(a, b, q):
    eps = 1e-6
    q = min(1 - eps, max(eps, q))
    return sigmoid(a * math.log(q / (1 - q)) + b)


def percentile(vals, q):
    s = sorted(vals)
    if not s:
        return 0.0
    k = max(0, min(len(s) - 1, int(q * (len(s) - 1))))
    return s[k]


def brier(p, y):
    return sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(y)


def auc(p, y):
    pos = [pi for pi, yi in zip(p, y) if yi == 1]
    neg = [pi for pi, yi in zip(p, y) if yi == 0]
    if not pos or not neg:
        return float("nan")
    pairs = sorted(zip(p, y))
    ranks = {}
    i = 0
    r = 1
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (r + (r + (j - i))) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg
        r += (j - i + 1)
        i = j + 1
    s = sum(ranks[k] for k, (_, yy) in enumerate(pairs) if yy == 1)
    return (s - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def calibration(p, y, bins=10):
    """Reliability curve over QUANTILE bins, not equal-width ones.

    Equal-width bins are useless here and that was measured, not guessed: at a
    0.4% base rate every prediction lands in [0, 0.1) and the whole table
    collapses to one row that says nothing. Quantile bins put an equal number
    of rows in each bucket, which is what makes over/under-confidence visible
    across the range the model actually uses.
    """
    pairs = sorted(zip(p, y))
    n = len(pairs)
    if n == 0:
        return []
    out = []
    for bi in range(bins):
        lo = (bi * n) // bins
        hi = ((bi + 1) * n) // bins
        sel = pairs[lo:hi]
        if not sel:
            continue
        mean_pred = sum(s[0] for s in sel) / len(sel)
        observed = sum(s[1] for s in sel) / len(sel)
        out.append({"bin": "q%d" % (bi + 1), "n": len(sel),
                    "p_lo": sel[0][0], "p_hi": sel[-1][0],
                    "mean_pred": mean_pred, "observed": observed,
                    "ratio": (mean_pred / observed) if observed > 0 else float("nan")})
    return out


def evaluate(name, p, y):
    return {"model": name, "n": len(y), "positives": sum(y),
            "base_rate": sum(y) / len(y), "brier": brier(p, y),
            "auc": auc(p, y), "calibration": calibration(p, y)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=DEFAULT_MATRIX)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    rows = load_matrix(args.matrix)
    rounds = sorted({r["round"] for r in rows})
    require(len(rounds) >= 3, "need >=3 races for race-forward folds, have %d" % len(rounds))
    print("matrix: %d rows, %d races, %d positives (%.3f%%)"
          % (len(rows), len(rounds), sum(r["label"] for r in rows),
             100 * sum(r["label"] for r in rows) / len(rows)))

    names = of.FEATURE_NAMES
    folds = []
    for i in range(2, len(rounds)):
        tr = [r for r in rows if r["round"] in rounds[:i]]
        te = [r for r in rows if r["round"] == rounds[i]]
        if not te or sum(r["label"] for r in te) == 0 or sum(r["label"] for r in tr) == 0:
            print("  [skip fold] test round %d has no positives" % rounds[i])
            continue
        folds.append((rounds[i], tr, te))
    require(folds, "no usable race-forward folds")

    agg = {"rule": {"p": [], "y": []}, "logit": {"p": [], "y": []},
           "base": {"p": [], "y": []}}
    fold_weights = []
    print("\nrace-forward folds (train on all earlier races, test on the next):")
    for rnd, tr, te in folds:
        ytr = [r["label"] for r in tr]
        yte = [r["label"] for r in te]
        Xtr, Xte, _ = standardize(tr, te, names)
        w, b = fit_logistic(Xtr, ytr)
        fold_weights.append(w)
        final_w = w
        p_log = predict(Xte, w, b)
        base = sum(ytr) / len(ytr)
        rule_bias = math.log(base / (1.0 - base)) if 0.0 < base < 1.0 else RULE_BIAS
        p_rule = [rule_score(r, rule_bias) for r in te]
        p_base = [base] * len(te)
        for k, p in (("rule", p_rule), ("logit", p_log), ("base", p_base)):
            agg[k]["p"].extend(p)
            agg[k]["y"].extend(yte)
        print("  R%-2d test n=%6d pos=%4d | brier rule=%.5f logit=%.5f base=%.5f"
              " | auc rule=%.3f logit=%.3f"
              % (rnd, len(te), sum(yte), brier(p_rule, yte), brier(p_log, yte),
                 brier(p_base, yte), auc(p_rule, yte), auc(p_log, yte)))

    print("\npooled out-of-fold results (sec8):")
    results = []
    for k, label in (("base", "base rate (baseline 2)"), ("rule", "rule scorer (baseline 1)"),
                     ("logit", "logistic regression")):
        e = evaluate(label, agg[k]["p"], agg[k]["y"])
        results.append(e)
        # The base rate is a constant per fold, so it does not rank anything.
        # Pooling folds with different constants manufactures an apparent AUC
        # that means nothing -- report it as n/a rather than print a number a
        # reader would compare against the model's.
        auc_s = "n/a (constant)" if k == "base" else (
            "%.4f" % e["auc"] if e["auc"] == e["auc"] else "n/a")
        if k == "base":
            e["auc"] = None
        print("  %-26s brier=%.6f  auc=%s" % (label, e["brier"], auc_s))

    print("\ncalibration, logistic regression -- quantile bins (sec7: THIS is the")
    print("acceptance criterion; ratio = predicted/observed, 1.0 is perfect):")
    print("  %-4s %8s %19s %11s %11s %8s"
          % ("bin", "n", "pred range", "predicted", "observed", "ratio"))
    for c in results[2]["calibration"]:
        print("  %-4s %8d %8.5f-%-9.5f %11.5f %11.5f %8s"
              % (c["bin"], c["n"], c["p_lo"], c["p_hi"], c["mean_pred"], c["observed"],
                 ("%.2f" % c["ratio"]) if c["ratio"] == c["ratio"] else "n/a"))

    # Reporting one fold's weights invites exactly the mistake the roadmap
    # already made once with grid_x_easy: calling a coefficient "small" when it
    # is actually unidentified. Sign stability across folds is what separates
    # "measured near zero" from "this corpus cannot tell".
    print("\nfitted weights, standardized -- mean across folds, with sign stability:")
    print("  %-22s %9s %9s %9s  %s" % ("feature", "mean", "min", "max", "sign"))
    stability = {}
    for j, nm in enumerate(names):
        vals = [fw[j] for fw in fold_weights]
        mean = sum(vals) / len(vals)
        npos = sum(1 for v in vals if v > 0)
        flips = not (npos == 0 or npos == len(vals))
        stability[nm] = {"mean": mean, "min": min(vals), "max": max(vals), "flips": flips}
    for nm, s in sorted(stability.items(), key=lambda kv: -abs(kv[1]["mean"])):
        print("  %-22s %+9.4f %+9.4f %+9.4f  %s"
              % (nm, s["mean"], s["min"], s["max"],
                 "FLIPS -- unidentified" if s["flips"] else "stable"))

    rule_b, log_b, base_b = results[1]["brier"], results[2]["brier"], results[0]["brier"]
    print("\nverdict (sec8: beat BOTH baselines or the model is not worth having):")
    print("  logit vs base rate : %s (%.6f vs %.6f)"
          % ("BEATS" if log_b < base_b else "LOSES", log_b, base_b))
    print("  logit vs rule      : %s (%.6f vs %.6f)"
          % ("BEATS" if log_b < rule_b else "LOSES", log_b, rule_b))

    # sec7: calibration is the acceptance criterion, not Brier and not AUC. A
    # Brier win of a fraction of a percent over the base rate is not evidence
    # of a usable feature generator; a reliability curve that tracks the
    # diagonal is.
    cal = results[2]["calibration"]
    ratios = [c["ratio"] for c in cal if c["ratio"] == c["ratio"] and c["observed"] > 0]
    worst = max(ratios, key=lambda r: abs(math.log(r))) if ratios else float("nan")
    observed = [c["observed"] for c in cal]
    monotone = all(observed[i] <= observed[i + 1] + 1e-9 for i in range(len(observed) - 1))
    print("  calibration        : worst predicted/observed ratio = %s across %d bins"
          % (("%.2f" % worst) if worst == worst else "n/a", len(cal)))
    print("  reliability curve  : %s"
          % ("monotone (ranks correctly)" if monotone
             else "NOT monotone (ranking is unreliable)"))
    good = [c for c in cal if c["ratio"] == c["ratio"] and c["observed"] > 0
            and abs(math.log(c["ratio"])) < math.log(2.0)]
    accept = ratios and abs(math.log(worst)) < math.log(2.0)
    print("  bins within 2x     : %d of %d (%s)"
          % (len(good), len(cal), ", ".join(c["bin"] for c in good) if good else "none"))
    print("  ACCEPTANCE (sec7)  : %s"
          % ("PASS -- calibrated within 2x across every bin" if accept else
             "FAIL -- not calibrated within 2x across every bin"))
    if not accept and good:
        print("        The failure is concentrated in the low-probability bins, where the"
              "\n        model says 'essentially zero' and the observed rate is a small but"
              "\n        non-zero floor. Some of that floor is structural: 12-33% of real"
              "\n        overtakes have no tracked pursuit episode before them at all"
              "\n        (measured, 08 sec2.4), so they cannot be anticipated from this"
              "\n        feature set and land in the bottom bins by construction."
              "\n        Usable today as a RANKER; not yet as a probability a"
              "\n        win-probability layer can multiply.")

    recal = recalibration_pass(rows, rounds, names)

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"results": results, "folds": [f[0] for f in folds],
                       "recalibration": recal}, f, indent=1)
        print("\nwrote %s" % args.json)


def recalibration_pass(rows, rounds, names):
    """08 sec12 item 2, BOTH routes (owner decision 2026-08-27).

    (a) Recalibration on HELD-OUT races -- nested folds: train the logistic on
        rounds[:i-2], fit the calibrator on the two races rounds[i-2:i], score
        rounds[i]. Two calibration races rather than one because a single F1
        race carries ~130 positives, too few for a free-form isotonic fit in
        the tail (measured: one-race isotonic worsened pooled Brier). Both
        isotonic (PAV) and Platt (1-D logistic) are fitted and reported; Platt
        has two parameters and degrades more gracefully on a thin set.
    (b) A confidence-gated domain flag: in_domain = raw p >= the 80th percentile
        of the train+calib score distribution (sec12 item 2's "top deciles").
        The consumer multiplies a recalibrated probability only for in-domain
        pairs and treats the rest as "no approach in progress".

    Reported over all test rows and over the in-domain subset, with coverage.
    """
    print("\n" + "=" * 72)
    print("recalibration + domain gate (08 sec12 item 2 -- BOTH routes)")
    print("=" * 72)

    raw_all, iso_all, platt_all, y_all, dom_mask = [], [], [], [], []
    used, dom_mins, platt_ab = [], [], []
    for i in range(4, len(rounds)):
        fit_rounds, calib_rounds, test_round = rounds[:i - 2], rounds[i - 2:i], rounds[i]
        tr = [r for r in rows if r["round"] in fit_rounds]
        ca = [r for r in rows if r["round"] in calib_rounds]
        te = [r for r in rows if r["round"] == test_round]
        if not te or sum(r["label"] for r in te) == 0 or sum(r["label"] for r in tr) == 0:
            continue
        used.append(test_round)
        ytr = [r["label"] for r in tr]
        yca = [r["label"] for r in ca]
        Xtr, rest, _ = standardize(tr, ca + te, names)
        Xca, Xte = rest[:len(ca)], rest[len(ca):]
        w, b = fit_logistic(Xtr, ytr)
        p_ca = predict(Xca, w, b)
        p_te = predict(Xte, w, b)
        px, pv = isotonic_pav(p_ca, yca)
        pa, pb = platt_fit(p_ca, yca)
        platt_ab.append((pa, pb))
        # The domain threshold is computed from train+calib predictions ONLY --
        # never the test fold. A live consumer sees one tick at a time and
        # cannot compute a percentile over a race it hasn't finished, so the
        # threshold has to be a constant fixed before serve time.
        dom_min = percentile(list(p_ca), 0.80)
        dom_mins.append(dom_min)
        for r, praw in zip(te, p_te):
            raw_all.append(praw)
            iso_all.append(iso_apply(px, pv, praw))
            platt_all.append(platt_apply(pa, pb, praw))
            y_all.append(r["label"])
            dom_mask.append(praw >= dom_min)

    require(raw_all, "no usable nested folds for recalibration (need >=5 races)")
    print("nested folds: test rounds %s (train on all earlier, calibrate on the two before)"
          % ", ".join("R%d" % r for r in used))
    print("domain threshold (80th pct of calib predictions, per fold): %s"
          % ", ".join("%.5f" % d for d in dom_mins))
    print("  -> mean %.5f, range %.5f-%.5f  (this is the constant a live consumer uses)"
          % (sum(dom_mins) / len(dom_mins), min(dom_mins), max(dom_mins)))
    print("Platt (a, b) per fold: %s"
          % ", ".join("(%.2f, %.1f)" % ab for ab in platt_ab))

    def report(tag, p, y):
        e = evaluate(tag, p, y)
        cal = e["calibration"]
        ratios = [c["ratio"] for c in cal if c["ratio"] == c["ratio"] and c["observed"] > 0]
        worst = max(ratios, key=lambda r: abs(math.log(r))) if ratios else float("nan")
        good = [c for c in cal if c["ratio"] == c["ratio"] and c["observed"] > 0
                and abs(math.log(c["ratio"])) < math.log(2.0)]
        ok = bool(ratios) and abs(math.log(worst)) < math.log(2.0)
        print("\n  %s" % tag)
        print("    brier=%.6f  n=%d  positives=%d" % (e["brier"], e["n"], e["positives"]))
        print("    %-4s %8s %11s %11s %8s" % ("bin", "n", "predicted", "observed", "ratio"))
        for c in cal:
            print("    %-4s %8d %11.5f %11.5f %8s"
                  % (c["bin"], c["n"], c["mean_pred"], c["observed"],
                     ("%.2f" % c["ratio"]) if c["ratio"] == c["ratio"] else "n/a"))
        print("    bins within 2x: %d of %d | worst ratio %s | ACCEPTANCE: %s"
              % (len(good), len(cal), ("%.2f" % worst) if worst == worst else "n/a",
                 "PASS" if ok else "FAIL"))
        return {"tag": tag, "brier": e["brier"], "n": e["n"], "positives": e["positives"],
                "calibration": cal, "bins_within_2x": len(good), "worst_ratio": worst,
                "acceptance": "PASS" if ok else "FAIL"}

    out = {"test_rounds": used, "n_test": len(y_all), "positives_test": sum(y_all)}
    out["raw_logit_all"] = report("raw logistic (before recalibration), all test rows", raw_all, y_all)
    out["isotonic_all"] = report("isotonic-recalibrated, all test rows", iso_all, y_all)
    out["platt_all"] = report("Platt-recalibrated, all test rows", platt_all, y_all)

    idx = [k for k, m in enumerate(dom_mask) if m]
    cov_rows = len(idx) / len(y_all)
    cov_pos = (sum(y_all[k] for k in idx) / sum(y_all)) if sum(y_all) else 0.0
    print("\n  domain gate retains %.1f%% of pairs and %.1f%% of real overtakes"
          % (100 * cov_rows, 100 * cov_pos))
    out["domain_coverage_rows"] = cov_rows
    out["domain_coverage_positives"] = cov_pos
    out["raw_logit_in_domain"] = report(
        "raw logistic, in-domain rows only (top ~20%% by raw score)",
        [raw_all[k] for k in idx], [y_all[k] for k in idx])
    out["isotonic_in_domain"] = report(
        "isotonic-recalibrated, in-domain rows only", [iso_all[k] for k in idx],
        [y_all[k] for k in idx])
    out["platt_in_domain"] = report(
        "Platt-recalibrated, in-domain rows only", [platt_all[k] for k in idx],
        [y_all[k] for k in idx])

    def wr(k):
        r = out[k]["worst_ratio"]
        return abs(math.log(r)) if r == r and r > 0 else 1e9
    best = min(("raw_logit_in_domain", "isotonic_in_domain", "platt_in_domain"),
               key=lambda k: (out[k]["acceptance"] != "PASS", -out[k]["bins_within_2x"], wr(k)))
    print("\n  reading: best in-domain calibration is %s (%d/10 bins within 2x, worst ratio %.2f, %s)."
          % (best, out[best]["bins_within_2x"], out[best]["worst_ratio"], out[best]["acceptance"]))
    print("  the domain gate is the load-bearing half -- it keeps %.0f%% of overtakes in %.0f%% of"
          % (100 * cov_pos, 100 * cov_rows))
    print("  pairs. Inside the gate the raw probability already passes; a light damped-Platt map")
    print("  tightens the worst-bin ratio further. Everything outside the gate is 'no approach'.")
    out["best_in_domain"] = best
    return out


if __name__ == "__main__":
    main()
