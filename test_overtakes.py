#!/usr/bin/env python3
"""Tests for the overtake model. 08-overtake-model.md sec5/sec6/sec10.

Fixtures are hand-written and synthetic, never a truncated real capture. That
is not fussiness: 03 sec11.2 forbids committing any F1 live-timing data to this
repo, including a sample or a test fixture, and this repo is public.

Run: python3 test_overtakes.py
"""

import math
import sys

import pandas as pd

from lib import overtakes as ov
from lib import overtake_features as of
from lib.invariants import InvariantError

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def pos_frame(triples):
    return pd.DataFrame(triples, columns=["t", "Driver", "Position"]).sort_values("t")


# ---------------------------------------------------------------- AheadIndex
def test_ahead_index():
    print("AheadIndex")
    pos = pos_frame([(0.0, "A", 1), (0.0, "B", 2), (0.0, "C", 3),
                     (50.0, "B", 1), (50.0, "A", 2)])
    idx = ov.AheadIndex(pos)
    check("leader has nobody ahead", idx.ahead_of(10.0, "A") is None)
    check("B is behind A at t=10", idx.ahead_of(10.0, "B") == "A")
    check("after the swap A is behind B", idx.ahead_of(60.0, "A") == "B")
    check("position lookup tracks the swap", idx.position_of(60.0, "B") == 1)
    check("before any data -> None", idx.order_at(-5.0) is None)
    # the no-lookahead property: a lookup at t must not see an update after t
    check("lookup is backward-only", idx.position_of(49.9, "B") == 2)


# ------------------------------------------------------------- pass labelling
def test_find_passes():
    print("find_passes (sec5.1's four filters)")
    # B passes A at t=100 and it sticks
    pos = pos_frame([(0.0, "A", 1), (0.0, "B", 2),
                     (100.0, "B", 1), (100.0, "A", 2),
                     (200.0, "B", 1), (200.0, "A", 2)])
    idx = ov.AheadIndex(pos)
    ev = ov.find_passes(pos, idx, {}, lap1_t=10.0)
    check("a clean pass is found", len(ev) == 1 and ev[0].overtaker == "B", str(ev))

    # same pass, but it reverts after 2s -> debounce must reject it
    pos2 = pos_frame([(0.0, "A", 1), (0.0, "B", 2),
                      (100.0, "B", 1), (100.0, "A", 2),
                      (102.0, "A", 1), (102.0, "B", 2),
                      (200.0, "A", 1), (200.0, "B", 2)])
    ev2 = ov.find_passes(pos2, ov.AheadIndex(pos2), {}, lap1_t=10.0)
    check("a swap that reverts is rejected by the debounce", len(ev2) == 0, str(ev2))

    # lap-1 churn is excluded
    ev3 = ov.find_passes(pos, idx, {}, lap1_t=150.0)
    check("a pass before lap 1 ends is excluded", len(ev3) == 0, str(ev3))

    # a pass while the passed car is in the pits is not an overtake
    ev4 = ov.find_passes(pos, idx, {"A": [(95.0, 130.0)]}, lap1_t=10.0)
    check("a pass over a pitting car is excluded", len(ev4) == 0, str(ev4))


# ------------------------------------------------------------------- episodes
def test_episodes():
    print("find_episodes (sec5.3)")
    pos = pos_frame([(0.0, "A", 1), (0.0, "B", 2), (0.0, "C", 3)])
    idx = ov.AheadIndex(pos)
    iv = pd.DataFrame({"t": [float(t) for t in range(20, 60)],
                       "Driver": ["B"] * 40,
                       "interval": [1.0] * 40})
    eps = ov.find_episodes(iv, idx, {}, lap1_t=10.0)
    check("a sustained close pursuit is one episode", len(eps) == 1, str(eps))
    check("episode records the right pair",
          eps and eps[0].pursuer == "B" and eps[0].ahead == "A")

    # a car that is never within the threshold produces no episode
    iv_far = pd.DataFrame({"t": [float(t) for t in range(20, 60)],
                           "Driver": ["B"] * 40, "interval": [5.0] * 40})
    check("a distant car is not an episode",
          len(ov.find_episodes(iv_far, idx, {}, lap1_t=10.0)) == 0)

    # identity change must break the episode, sec5.3
    pos_sw = pos_frame([(0.0, "A", 1), (0.0, "B", 3), (0.0, "C", 2),
                        (40.0, "A", 2), (40.0, "C", 1)])
    eps_sw = ov.find_episodes(iv, ov.AheadIndex(pos_sw), {}, lap1_t=10.0)
    check("a change of car-ahead breaks the episode into two",
          len(eps_sw) == 2, str(eps_sw))


# ------------------------------------------------------------------ labelling
def test_lookahead_labels():
    print("label_rows (sec5.3 as amended -- symmetric sampling)")
    ep = ov.Episode("B", "A", 0.0, 100.0)
    passes = [ov.PassEvent(60.0, "B", "A", 1)]
    times = [float(t) for t in range(0, 101, 10)]
    labels = of.label_rows(times, ep, passes, horizon=10.0)
    got = {t: l for t, l in zip(times, labels)}
    check("t=50 is positive (pass at t=60 is inside the 10s horizon)", got[50.0] == 1)
    check("t=60 is NOT positive (the pass is not strictly after t)", got[60.0] == 0)
    check("t=20 is negative even though this episode ends in a pass", got[20.0] == 0)
    check("t=0 is negative", got[0.0] == 0)

    # a pass by a DIFFERENT pair must not label this episode
    other = [ov.PassEvent(60.0, "C", "A", 1)]
    check("another pair's pass does not leak into this episode",
          sum(of.label_rows(times, ep, other, 10.0)) == 0)


# ------------------------------------------------------------- no-lookahead
def test_no_lookahead_guard():
    print("assert_no_lookahead (sec10)")
    times = [10.0, 20.0, 30.0]
    of.assert_no_lookahead({"ok": [9.0, 19.0, 29.0]}, times)
    check("a backward-only source passes", True)
    try:
        of.assert_no_lookahead({"bad": [9.0, 25.0, 29.0]}, times)
        check("a future-reading source is rejected", False, "no InvariantError raised")
    except InvariantError:
        check("a future-reading source is rejected", True)


# ----------------------------------------------------------------- rule model
def test_rule_scorer():
    print("rule_score (sec7 stage 1)")
    import overtake_fit as fit

    base = {"interval": 1.0, "closing_rate": 0.0, "speed_delta": 0.0,
            "time_in_range": 5.0, "under_caution": 0.0}

    def s(**kw):
        r = dict(base)
        r.update(kw)
        return fit.rule_score(r)

    check("probability is in [0,1]", 0.0 <= s() <= 1.0)
    check("closing faster scores higher", s(closing_rate=-0.3) > s(closing_rate=0.0))
    check("a bigger gap scores lower", s(interval=1.8) < s(interval=0.3))
    check("a speed advantage scores higher", s(speed_delta=15.0) > s(speed_delta=0.0))
    check("caution suppresses the score to zero", s(under_caution=1.0) == 0.0)
    check("monotone in interval",
          s(interval=0.2) > s(interval=0.8) > s(interval=1.4))


# --------------------------------------------------------------- fit mechanics
def test_logistic():
    print("fit_logistic (sec7 stage 2)")
    import overtake_fit as fit

    # a linearly separable problem the fitter must solve
    X = [[-2.0], [-1.5], [-1.0], [1.0], [1.5], [2.0]]
    y = [0, 0, 0, 1, 1, 1]
    w, b = fit.fit_logistic(X, y, lam=0.0, lr=1.0, epochs=2000)
    p = fit.predict(X, w, b)
    check("separable data is learned", all(p[i] < 0.5 for i in range(3))
          and all(p[i] > 0.5 for i in range(3, 6)), str(p))
    check("weight has the right sign", w[0] > 0)

    check("brier of a perfect prediction is 0", fit.brier([1.0, 0.0], [1, 0]) == 0.0)
    check("auc of a perfect ranker is 1", abs(fit.auc([0.9, 0.1], [1, 0]) - 1.0) < 1e-9)
    check("auc of an inverted ranker is 0", abs(fit.auc([0.1, 0.9], [1, 0])) < 1e-9)

    try:
        fit.fit_logistic([[1.0], [2.0]], [0, 0])
        check("a single-class fold is rejected", False, "no InvariantError raised")
    except InvariantError:
        check("a single-class fold is rejected", True)


# ------------------------------------------------------------ throttle rule
def test_throttle_not_bounded():
    print("throttle is not bounded at 100 (sec10, 03 sec7.3)")
    src = open("lib/overtake_features.py").read() + open("overtake_fit.py").read()
    bad = ("throttle <= 100" in src or "throttle<=100" in src
           or "throttle > 100" in src.replace("exceeding 100", ""))
    check("no assertion bounds throttle at 100", not bad)


def main():
    for fn in (test_ahead_index, test_find_passes, test_episodes,
               test_lookahead_labels, test_no_lookahead_guard, test_rule_scorer,
               test_logistic, test_throttle_not_bounded):
        fn()
    print()
    if FAILURES:
        print("FAILED: %d" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        sys.exit(1)
    print("all tests passed")


if __name__ == "__main__":
    main()
