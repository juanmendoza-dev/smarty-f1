"""Plackett-Luce finishing-order simulation. 04-outcome-expansion-algo.md sec6.

Reuses Phase A1's win-strength weights (w_d = exp(score_d/T)) unchanged -- this
is not a new model, it's the same softmax strengths asked "who's in the top K
of the full order" instead of just "who's first." No new weights, no network
calls, no randomness sourced from anything but the locked seed below.

Method (sec6.2): draw independent Uniform(0,1) keys per driver, transform to
key_d = -ln(U_d) / w_d (an exact draw from Exponential(rate=w_d)), sort
ascending. This is the "exponential race" equivalence -- sorting independent
non-identical exponentials is an exact draw from the Plackett-Luce
distribution over full rankings, not an approximation of one. One simulated
race gives one full order; membership in the first K positions across many
simulated races is the Monte Carlo estimate of P(top-K).
"""

import math
import random

SIM_N = 200_000
SIM_SEED = 20260823


def simulate_topk_probabilities(weights_by_code, ks, n=SIM_N, seed=SIM_SEED):
    """weights_by_code: {code: w}, w = exp((score_d - max(score)) / T), same
    strengths score.py's win softmax already computes -- pass them in, don't
    recompute here.

    ks: iterable of K values to report top-K membership probability for (e.g.
    [1, 3, 10]). Every K is measured from the *same* simulated draws, so
    p_topK is monotonically non-decreasing in K for every driver by
    construction (a driver in the top-1 set of a given simulated race is
    necessarily in that race's top-3 and top-10 sets too).

    Returns ({code: {k: probability}}, {"n": n, "seed": seed}).
    """
    codes = sorted(weights_by_code)
    ks = sorted(ks)
    rng = random.Random(seed)
    counts = {code: {k: 0 for k in ks} for code in codes}

    for _ in range(n):
        keys = {code: -math.log(rng.random()) / weights_by_code[code] for code in codes}
        order = sorted(codes, key=lambda c: keys[c])
        for k in ks:
            for code in order[:k]:
                counts[code][k] += 1

    probabilities = {code: {k: counts[code][k] / n for k in ks} for code in codes}
    return probabilities, {"n": n, "seed": seed}
