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

from .invariants import require

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


def exact_top3_probabilities(weights_by_code):
    """Exact P(driver finishes in the top 3) under Plackett-Luce. No sampling.

    04-outcome-expansion-algo.md sec6.2 estimated this by Monte Carlo along with
    top-10. Top-3 does not need to be: summing over every ordered (1st, 2nd, 3rd)
    triple is O(n^3), which is ~10k terms on a 22-car grid and runs in ~2.5ms --
    against ~1.5s and +/-0.3pp for the simulation. Top-10 stays on Monte Carlo:
    a Plackett-Luce denominator depends on *which* drivers were already placed,
    not just how many, so there is no DP that collapses the 10-deep sum.

        P(d in top 3) = P(d 1st) + P(d 2nd) + P(d 3rd)
        P(d 1st)      = w_d / W
        P(d 2nd)      = sum_{a != d}          (w_a/W)(w_d/(W - w_a))
        P(d 3rd)      = sum_{a != d} sum_{b != a,d} (w_a/W)(w_b/(W - w_a))(w_d/(W - w_a - w_b))

    Numerical note: the third term divides by W - w_a - w_b, which cancels badly
    on a field where two drivers carry nearly all the weight. The sum-to-3 check
    at the end is not a bonus assertion, it IS the stability guard -- top-3
    marginals must sum to exactly 3 because every simulated race puts exactly
    three drivers on the podium. If it trips, the field is too lopsided for this
    recurrence in float and the Monte Carlo estimate is the honest fallback.

    Returns {code: probability}.
    """
    codes = sorted(weights_by_code)
    if len(codes) <= 3:
        return {code: 1.0 for code in codes}

    w = weights_by_code
    W = sum(w.values())
    require(W > 0, "exact_top3: total Plackett-Luce weight must be > 0")

    out = {}
    for d in codes:
        wd = w[d]
        p = wd / W
        for a in codes:
            if a == d:
                continue
            wa = w[a]
            r1 = W - wa
            require(r1 > 0, f"exact_top3: degenerate remainder after {a}")
            p += (wa / W) * (wd / r1)
            for b in codes:
                if b == a or b == d:
                    continue
                wb = w[b]
                r2 = r1 - wb
                require(r2 > 0, f"exact_top3: degenerate remainder after {a},{b}")
                p += (wa / W) * (wb / r1) * (wd / r2)
        out[d] = p

    total = sum(out.values())
    require(
        abs(total - 3.0) < 1e-6,
        f"exact_top3: marginals sum to {total!r}, not 3.0 -- the O(n^3) recurrence "
        f"lost precision on this field; fall back to the Monte Carlo estimate",
    )
    return out
