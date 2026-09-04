"""Probe: does taking pit cycles out of `q` move 12 sec2.3's 0.61 toward 1.0?

This is `docs/12` sec6 **outcome 2**, and it is the sharpest test in that
document because it is a prediction about a quantity that was already measured
before the model existed. It has no path through `winprob_fit.py` -- the 0.61
is a probe-level ratio over raw pair counts, not anything the fitted
`BackgroundRate` emits -- so it gets its own script.

**The measurement, unchanged from `12b_pit_projection.py` sec1b.** For each
adjacent pair at lap `L`: the one-lap swap rate `q` over `L -> L+1`, and the net
displacement over `L -> L+5` (is the car that was behind actually ahead?). A
per-lap rate compounded as `1 - (1-q)^5` answers "did this pair swap at least
once", which is not the same question, and the ratio between them is the
over-dispersion. Pooled over 12 races it measured **0.61**.

**Pre-registered before the run** (this docstring is committed ahead of the
number it produces):

  - The one-lap half uses `lib/winprob_background`'s exclusion window -- drop
    the pair if either car has a pit-in on `L-1`, `L` or `L+1` -- because that
    is the window the refit `q` is actually fitted on, and outcome 2 is a claim
    about that `q`.
  - The five-lap half applies the same rule **across the whole span**: drop the
    pair if either car has a pit-in anywhere in `L-1 .. L+5`. A displacement
    that a stop inside the span produced is a pit-cycle displacement, which is
    precisely what was removed from `q`; scoring a pit-free rate against a
    pit-contaminated displacement would compare two different processes and
    would bias the ratio in the direction this probe is hoping for.
  - Both arms are reported. The all-pairs arm has to reproduce 0.61 or the
    comparison means nothing.

**What a pass and a failure each look like, said in advance.** Toward 1.0 means
pit cycles were a principal source of the transient swaps and `q` is a cleaner
input for having lost them. No movement, or movement away, means the reverting
swaps are mostly on-track and the double-count fix buys correctness of
bookkeeping and nothing else -- which is a real result and is reported as one
(12 sec6's null, `05` sec6.4.1's precedent).

Reads only the warm FastF1 archive cache; same 12 rounds as `12b`.
Usage: .venv312/bin/python probes/12c_q_refit.py
"""
import collections
import sys
import warnings

warnings.filterwarnings("ignore")

import fastf1
import pandas as pd

fastf1.Cache.enable_cache("data/cache/fastf1")
SEASON = 2026
SPAN = 5
BANDS = (("P1-P3", 1, 3), ("P4-P6", 4, 6), ("P7-P10", 7, 10),
         ("P11-P15", 11, 15), ("P16+", 16, 99))

# arm -> (band, quarter) -> [pairs, events]
lap1 = {"all": collections.defaultdict(lambda: [0, 0]),
        "no_pit": collections.defaultdict(lambda: [0, 0])}
net5 = {"all": collections.defaultdict(lambda: [0, 0]),
        "no_pit": collections.defaultdict(lambda: [0, 0])}


def band_of(pos):
    for nm, lo, hi in BANDS:
        if lo <= pos <= hi:
            return nm
    return BANDS[-1][0]


sched = fastf1.get_event_schedule(SEASON, include_testing=False)
now = pd.Timestamp.utcnow().tz_localize(None)
for _, ev in sched.iterrows():
    rnd = int(ev["RoundNumber"])
    when = ev.get("EventDate")
    if pd.notna(when) and pd.Timestamp(when) > now:
        continue
    try:
        s = fastf1.get_session(SEASON, rnd, "R")
        s.load(telemetry=False, laps=True, weather=False, messages=False)
        laps = s.laps
    except Exception as e:                          # noqa: BLE001
        sys.stderr.write("R%d: skip (%s)\n" % (rnd, type(e).__name__))
        continue
    if laps is None or laps.empty:
        continue
    total = int(laps["LapNumber"].max())

    order, rorder = {}, {}
    for lp in range(1, total + 1):
        sub = laps[(laps["LapNumber"] == lp) & laps["Position"].notna()]
        order[lp] = {int(r["Position"]): str(r["Driver"]) for _, r in sub.iterrows()}
        rorder[lp] = {v: k for k, v in order[lp].items()}

    stops = collections.defaultdict(set)
    for _, r in laps.iterrows():
        if pd.notna(r["PitInTime"]) and pd.notna(r["LapNumber"]):
            stops[str(r["Driver"])].add(int(r["LapNumber"]))

    def clean(codes, lo, hi):
        """No car in `codes` has a pit-in anywhere in [lo, hi]."""
        return not any(any(lo <= x <= hi for x in stops.get(c, ()))
                       for c in codes)

    for L in range(1, total):
        a_ord = order.get(L)
        if not a_ord:
            continue
        q = min(int((L / float(total)) * 4), 3)
        nxt, far = rorder.get(L + 1), rorder.get(L + SPAN)
        for pos in sorted(a_ord):
            if pos + 1 not in a_ord:
                continue
            ahead_c, behind_c = a_ord[pos], a_ord[pos + 1]
            pair = (ahead_c, behind_c)
            key = (band_of(pos), q)
            if nxt and ahead_c in nxt and behind_c in nxt:
                swapped = nxt[behind_c] < nxt[ahead_c]
                for arm in ("all",) + (("no_pit",) if clean(pair, L - 1, L + 1) else ()):
                    lap1[arm][key][0] += 1
                    lap1[arm][key][1] += 1 if swapped else 0
            if far and ahead_c in far and behind_c in far:
                ahead_now = far[behind_c] < far[ahead_c]
                for arm in ("all",) + (("no_pit",) if clean(pair, L - 1, L + SPAN) else ()):
                    net5[arm][key][0] += 1
                    net5[arm][key][1] += 1 if ahead_now else 0

print("=== 12 sec6 outcome 2 -- net@5 / compounded, with and without pit cycles ===")
print("%-8s %-10s %-10s %8s %9s %11s %11s %8s"
      % ("arm", "band", "quarter", "pairs", "q/lap", "1-(1-q)^5", "net@5", "net/cmp"))
summary = {}
for arm in ("all", "no_pit"):
    for nm, _, _ in BANDS:
        for qq in range(4):
            n1, k1 = lap1[arm][(nm, qq)]
            n5, k5 = net5[arm][(nm, qq)]
            if n1 < 50 or n5 < 50:
                continue
            q1 = k1 / n1
            cmp5 = 1 - (1 - q1) ** 5
            net = k5 / n5
            print("%-8s %-10s %-10s %8d %9.4f %11.4f %11.4f %8.2f"
                  % (arm, nm, "%.2f-%.2f" % (qq / 4, (qq + 1) / 4), n1, q1,
                     cmp5, net, net / cmp5 if cmp5 > 0 else float("nan")))
    tn1 = sum(v[0] for v in lap1[arm].values())
    tk1 = sum(v[1] for v in lap1[arm].values())
    tn5 = sum(v[0] for v in net5[arm].values())
    tk5 = sum(v[1] for v in net5[arm].values())
    q1 = tk1 / tn1
    cmp5 = 1 - (1 - q1) ** 5
    net = tk5 / tn5
    summary[arm] = (tn1, q1, cmp5, net, net / cmp5)
    print("%-8s %-10s %-10s %8d %9.4f %11.4f %11.4f %8.2f  <- POOLED"
          % (arm, "POOLED", "all", tn1, q1, cmp5, net, net / cmp5))

print("\n12 sec6 outcome 2, pre-registered: 0.61 should move TOWARD 1.0.")
print("  all pairs      : %.2f  (12 sec2.3 measured 0.61 -- must reproduce)"
      % summary["all"][4])
print("  pit cycles out : %.2f" % summary["no_pit"][4])
print("  movement       : %+.2f" % (summary["no_pit"][4] - summary["all"][4]))
