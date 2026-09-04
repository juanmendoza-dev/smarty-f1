"""Probe: pit-cycle dynamics, ahead of a pit-strategy model spec (docs/12).

09 sec5.7 names an undercut/pit-loss model as "the most valuable thing this
layer could gain" and 09 sec13 item 2 makes it the owner's call. This project
does not spec a feature from mechanism alone (docs/11, 09 sec2's opening), so
the numbers come first. Four quantities a pit-strategy spec cannot be written
without:

  1. delta per circuit -- the time a stop costs vs. staying out, from FastF1
     lap times (in-lap + out-lap vs the driver's own green-lap baseline).
     Reported with spread, not just a mean -- it feeds a track-position
     projection and the spread is the projection's error bar.
  2. how far the pit phase moves the front -- net positions gained/lost
     between a top-6 finisher's last lap before their first stop and the
     final classification.
  3. of 09 sec2.1's 34 pit-attributable P1 changes, how many stuck to the flag
     -- i.e. how many are real signal a live layer would want vs. transient
     in-lap/out-lap ordering.
  4. undercut success rate -- when two adjacent cars pit on different laps,
     how often the earlier-stopping car comes out ahead. Bounds how much
     stop *timing* (as opposed to a stop already in progress) matters.

Reads only the warm FastF1 archive cache. laps=True, telemetry=False.
Same 12 archived 2026 rounds as probes/09_*.py.

Usage: .venv312/bin/python probes/12_pit_loss.py
"""
import sys, warnings, collections, statistics
warnings.filterwarnings("ignore")
import fastf1
import pandas as pd

fastf1.Cache.enable_cache("data/cache/fastf1")
SEASON = 2026

deltas = collections.defaultdict(list)      # circuit -> [delta_seconds]
front_moves = []                            # net position change of eventual top-6 through the pit phase
stuck = [0, 0]                              # [pit-attributable P1 changes, of those that led at the flag]
undercut = [0, 0]                           # [attempts, successes]
races = []


def sec(td):
    return td.total_seconds() if pd.notna(td) else None


sched = fastf1.get_event_schedule(SEASON, include_testing=False)
for _, ev in sched.iterrows():
    rnd = int(ev["RoundNumber"])
    name = str(ev["EventName"])
    try:
        s = fastf1.get_session(SEASON, rnd, "R")
        s.load(telemetry=False, laps=True, weather=False, messages=False)
        laps = s.laps
    except Exception as e:
        sys.stderr.write("R%d %s: skip (%s)\n" % (rnd, name, type(e).__name__))
        continue
    if laps is None or laps.empty:
        sys.stderr.write("R%d %s: no laps\n" % (rnd, name)); continue
    res = s.results
    total = int(laps["LapNumber"].max())

    # per-lap order {lap: {pos: drv}} and {lap: {drv: pos}}
    order, rorder = {}, {}
    for lp in range(1, total + 1):
        sub = laps[(laps["LapNumber"] == lp) & laps["Position"].notna()]
        order[lp] = {int(r["Position"]): str(r["Driver"]) for _, r in sub.iterrows()}
        rorder[lp] = {v: k for k, v in order[lp].items()}

    # --- 1. delta per circuit ---
    # a stop shows as PitInTime on lap L (the in-lap) and PitOutTime on lap L+1 (the out-lap).
    for drv, dl in laps.groupby("Driver"):
        dl = dl.sort_values("LapNumber")
        # green baseline: median of this driver's laps with no pit in/out and a plausible time
        green = [sec(t) for t, pi, po in zip(dl["LapTime"], dl["PitInTime"], dl["PitOutTime"])
                 if pd.isna(pi) and pd.isna(po) and sec(t) is not None]
        if len(green) < 5:
            continue
        base = statistics.median(green)
        # discard obviously-SC laps: anything > base * 1.30 is not a green lap
        green = [g for g in green if g < base * 1.30]
        if len(green) < 5:
            continue
        base = statistics.median(green)
        for _, row in dl.iterrows():
            if pd.isna(row["PitInTime"]):
                continue
            L = int(row["LapNumber"])
            inlap = sec(row["LapTime"])
            out_row = dl[dl["LapNumber"] == L + 1]
            outlap = sec(out_row["LapTime"].iloc[0]) if len(out_row) else None
            if inlap is None or outlap is None:
                continue
            # both in- and out-lap must look like a real racing lap under green
            if inlap > base * 1.6 or outlap > base * 1.6:
                continue
            d = (inlap - base) + (outlap - base)
            if 5.0 < d < 45.0:          # sane pit-loss band; wider rejects flag/SC contamination
                deltas[name].append(d)

    # --- 2. how far the pit phase moves the front ---
    if res is not None and not res.empty:
        top6 = [str(r["Abbreviation"]) for _, r in
                res.sort_values("Position").head(6).iterrows()]
        for drv in top6:
            dl = laps[laps["Driver"] == drv].sort_values("LapNumber")
            first_stop = dl[dl["PitInTime"].notna()]["LapNumber"]
            if first_stop.empty:
                continue
            L0 = int(first_stop.min()) - 1
            if L0 < 1 or L0 not in rorder or drv not in rorder[L0]:
                continue
            pos_before = rorder[L0][drv]
            fin = res[res["Abbreviation"] == drv]["Position"]
            if fin.empty or pd.isna(fin.iloc[0]):
                continue
            front_moves.append(pos_before - int(fin.iloc[0]))   # + = gained through the phase

    # --- 3. did pit-attributable P1 changes stick? ---
    winner = None
    if res is not None and not res.empty:
        winner = str(res.sort_values("Position").iloc[0]["Abbreviation"])
    prev = None
    for lp in range(1, total + 1):
        ld = order[lp].get(1)
        if ld is None:
            continue
        if lp >= 2 and prev is not None and ld != prev:
            # pit-attributable: either car pitted within +-2 laps
            window = range(max(1, lp - 2), min(total, lp + 2) + 1)
            pitted = laps[(laps["LapNumber"].isin(window)) &
                          (laps["Driver"].isin([ld, prev])) &
                          (laps["PitInTime"].notna())]
            if not pitted.empty:
                stuck[0] += 1
                if winner is not None and ld == winner:
                    stuck[1] += 1
        prev = ld

    # --- 4. undercut success ---
    # adjacent pair (b ahead, a behind) at lap La-1; a pits on La, b pits later on Lb (La<Lb).
    # the undercut resolves once b has rejoined: compare a vs b one lap after b's out-lap.
    # "clean" = they were adjacent going in and no third car pitted between them in that span.
    stops = {}   # drv -> sorted list of in-lap numbers
    for drv, dl in laps.groupby("Driver"):
        stops[drv] = sorted(int(x) for x in dl[dl["PitInTime"].notna()]["LapNumber"].dropna())
    for La in range(2, total - 1):
        if La - 1 not in order:
            continue
        for pos in sorted(order[La - 1]):
            if pos + 1 not in order[La - 1]:
                continue
            b, a = order[La - 1][pos], order[La - 1][pos + 1]   # b ahead, a behind
            if La not in stops.get(a, []):
                continue
            later = [x for x in stops.get(b, []) if La < x <= La + 6]   # b stays out up to 6 laps
            if not later:
                continue
            Lb = later[0]
            check = Lb + 1
            if check not in rorder or a not in rorder[check] or b not in rorder[check]:
                continue
            undercut[0] += 1
            if rorder[check][a] < rorder[check][b]:
                undercut[1] += 1

    races.append(name)
    nd = sum(len(v) for k, v in deltas.items() if k == name)
    print("R%-2d %-28s stops_measured=%d" % (rnd, name[:28], nd))

print("\n=== 1. pit delta per circuit (seconds lost vs staying out) ===")
print("%-30s %5s %8s %8s %8s %8s" % ("circuit", "n", "median", "p25", "p75", "mean"))
alld = []
for name in races:
    v = sorted(deltas.get(name, []))
    alld += v
    if len(v) >= 4:
        q = statistics.quantiles(v, n=4)
        print("%-30s %5d %8.1f %8.1f %8.1f %8.1f"
              % (name[:30], len(v), statistics.median(v), q[0], q[2], statistics.mean(v)))
if alld:
    alld.sort()
    q = statistics.quantiles(alld, n=4)
    print("%-30s %5d %8.1f %8.1f %8.1f %8.1f"
          % ("-- POOLED --", len(alld), statistics.median(alld), q[0], q[2], statistics.mean(alld)))

print("\n=== 2. eventual top-6: net positions moved through the pit phase ===")
if front_moves:
    fm = sorted(front_moves)
    print("  n=%d  mean %+.2f  median %+.0f  |move|>=2 in %d of %d (%.0f%%)"
          % (len(fm), statistics.mean(fm), statistics.median(fm),
             sum(1 for x in fm if abs(x) >= 2), len(fm),
             100 * sum(1 for x in fm if abs(x) >= 2) / len(fm)))

print("\n=== 3. pit-attributable P1 changes that stuck to the flag ===")
print("  %d of %d pit-attributable lead changes had the beneficiary win (%.0f%%)"
      % (stuck[1], stuck[0], 100 * stuck[1] / stuck[0] if stuck[0] else 0))

print("\n=== 4. undercut success rate ===")
print("  %d of %d clean undercut attempts succeeded (%.0f%%)"
      % (undercut[1], undercut[0], 100 * undercut[1] / undercut[0] if undercut[0] else 0))
print("  (baseline: an adjacent pair swaps ~6%%/lap with no strategy; sec2.3)")
