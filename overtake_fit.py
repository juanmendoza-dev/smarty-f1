#!/usr/bin/env python3
"""Overtake model: rule-based scorer, then logistic regression. 08 sec7/sec8.

Algo before model (welcome.md). Stage 1 is a hand-weighted scorer the owner can
reason about and explain; stage 2 is a logistic regression that has to BEAT it
or it is not worth having. Both are evaluated the same way, against the same
folds, on the same rows.

The regression is hand-rolled -- no scipy, no sklearn -- mirroring fit.py's
choice for Phase A3. Writing the likelihood and its gradient by hand is the
point of this phase for a first ML build, and binary LR with L2 is short:

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

from lib.invariants import require
from lib import overtake_features as of
from lib import overtakes as ov

DEFAULT_MATRIX = "data/live/overtakes/training.csv"
L2 = 1e-4
LR = 0.5
EPOCHS = 400

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
    n, d = len(X), len(X[0])
    w = [0.0] * d
    b = 0.0
    pos = sum(y)
    require(0 < pos < n, "training fold is single-class (%d positives of %d)" % (pos, n))
    for _ in range(epochs):
        gw = [0.0] * d
        gb = 0.0
        for xi, yi in zip(X, y):
            z = b
            for j in range(d):
                z += w[j] * xi[j]
            e = sigmoid(z) - yi
            gb += e
            for j in range(d):
                gw[j] += e * xi[j]
        b -= lr * (gb / n)
        for j in range(d):
            w[j] -= lr * (gw[j] / n + lam * w[j])
    return w, b


def predict(X, w, b):
    out = []
    for xi in X:
        z = b
        for j in range(len(w)):
            z += w[j] * xi[j]
        out.append(sigmoid(z))
    return out


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
    """Reliability curve. sec8: this is the acceptance criterion."""
    out = []
    for bi in range(bins):
        lo, hi = bi / bins, (bi + 1) / bins
        sel = [(pi, yi) for pi, yi in zip(p, y) if (lo <= pi < hi or (bi == bins - 1 and pi == 1.0))]
        if not sel:
            continue
        out.append({"bin": "%.2f-%.2f" % (lo, hi), "n": len(sel),
                    "mean_pred": sum(s[0] for s in sel) / len(sel),
                    "observed": sum(s[1] for s in sel) / len(sel)})
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
        print("  %-26s brier=%.6f  auc=%s"
              % (label, e["brier"], "%.4f" % e["auc"] if e["auc"] == e["auc"] else "n/a"))

    print("\ncalibration, logistic regression (sec7: this is the acceptance criterion):")
    print("  %-12s %8s %10s %10s" % ("bin", "n", "predicted", "observed"))
    for c in results[2]["calibration"]:
        print("  %-12s %8d %10.4f %10.4f" % (c["bin"], c["n"], c["mean_pred"], c["observed"]))

    rule_b, log_b, base_b = results[1]["brier"], results[2]["brier"], results[0]["brier"]
    print("\nverdict (sec8: beat BOTH baselines or the model is not worth having):")
    print("  logit vs base rate : %s (%.6f vs %.6f)"
          % ("BEATS" if log_b < base_b else "LOSES", log_b, base_b))
    print("  logit vs rule      : %s (%.6f vs %.6f)"
          % ("BEATS" if log_b < rule_b else "LOSES", log_b, rule_b))

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"results": results, "folds": [f[0] for f in folds]}, f, indent=1)
        print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
