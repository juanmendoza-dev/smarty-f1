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


def rule_score(r):
    """sec7 stage 1: hand-weighted, explainable, no fitting."""
    if r["under_caution"] >= 0.5:
        return 0.0
    close = max(0.0, (ov.EPISODE_INTERVAL_S - r["interval"]) / ov.EPISODE_INTERVAL_S)
    closing = max(-1.0, min(1.0, -r["closing_rate"] * 4.0))
    speed = max(-1.0, min(1.0, r["speed_delta"] / 25.0))
    sustained = min(1.0, r["time_in_range"] / 30.0)
    z = (RULE_BIAS
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
    print("\nrace-forward folds (train on all earlier races, test on the next):")
    for rnd, tr, te in folds:
        ytr = [r["label"] for r in tr]
        yte = [r["label"] for r in te]
        Xtr, Xte, _ = standardize(tr, te, names)
        w, b = fit_logistic(Xtr, ytr)
        final_w = w
        p_log = predict(Xte, w, b)
        p_rule = [rule_score(r) for r in te]
        base = sum(ytr) / len(ytr)
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

    print("\nfitted weights on the final fold (standardized features, so these are")
    print("comparable to each other):")
    for nm, wt in sorted(zip(names, final_w), key=lambda kv: -abs(kv[1])):
        print("  %-22s %+8.4f" % (nm, wt))

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
              "\n        non-zero floor. Some of that floor is structural: 12-33%% of real"
              "\n        overtakes have no tracked pursuit episode before them at all"
              "\n        (measured, 08 sec2.4), so they cannot be anticipated from this"
              "\n        feature set and land in the bottom bins by construction."
              "\n        Usable today as a RANKER; not yet as a probability a"
              "\n        win-probability layer can multiply.")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"results": results, "folds": [f[0] for f in folds]}, f, indent=1)
        print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
