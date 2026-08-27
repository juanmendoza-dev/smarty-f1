"""Probe: 09-live-win-probability.md sec2.1, sec2.5 -- lead changes and retirements.

Probe B found 48 changes of the car in P1 across 12 races, against 08 sec2.1's
ONE on-track lead change in three races. If those are not contradictory, the
difference is pit cycles. Attribute each lead change.

Also: probe B's retirement count (79 over 12 races = 6.6/race) is implausible
against 04 sec5.1's measured 12.53% 2025 DNF rate (~2.7/race). Print the raw
Status values so the classifier can be fixed or the number marked UNMEASURED.
"""
import sys, warnings, collections
warnings.filterwarnings("ignore")
import fastf1, pandas as pd
fastf1.Cache.enable_cache("data/cache/fastf1")

status_counts = collections.Counter()
attrib = collections.Counter()
per_race = []
retire_frac = []

for rnd in range(1, 13):
    s = fastf1.get_session(2026, rnd, "R")
    s.load(telemetry=False, laps=True, weather=False, messages=False)
    laps, res = s.laps, s.results
    name = s.event["EventName"]
    total = int(laps["LapNumber"].max())
    for _, r in res.iterrows():
        status_counts[str(r.get("Status", ""))] += 1

    order = {}
    for lp in range(1, total + 1):
        sub = laps[(laps["LapNumber"] == lp) & laps["Position"].notna()]
        order[lp] = {int(x["Position"]): str(x["Driver"]) for _, x in sub.iterrows()}

    # pit laps per driver
    pit = collections.defaultdict(set)
    for _, r in laps.iterrows():
        if pd.notna(r["PitInTime"]) or pd.notna(r["PitOutTime"]):
            pit[str(r["Driver"])].add(int(r["LapNumber"]))
    # retired-on lap per driver
    ret_lap = {}
    for _, r in res.iterrows():
        drv = str(r["Abbreviation"]); st = str(r.get("Status", ""))
        if st in ("Finished", "Lapped") or st.startswith("+") or st == "Did not start":
            continue
        dl = laps[laps["Driver"] == drv]["LapNumber"]
        if len(dl):
            ret_lap[drv] = int(dl.max())

    prev, n = None, 0
    for lp in range(1, total + 1):
        ld = order[lp].get(1)
        if ld is None: continue
        if lp >= 2 and prev is not None and ld != prev:
            n += 1
            window = {lp - 2, lp - 1, lp, lp + 1, lp + 2}
            if ret_lap.get(prev) in window or ret_lap.get(prev) == lp - 1:
                attrib["retirement of the leader"] += 1
            elif window & pit[prev] or window & pit[ld]:
                attrib["pit stop (either car)"] += 1
            else:
                attrib["neither pit nor retirement"] += 1
        prev = ld
    per_race.append((rnd, name, n))
    # retirement fraction, using ONLY the fixed status classifier
    for drv, lastlap in ret_lap.items():
        if lastlap < total - 1:
            retire_frac.append(lastlap / float(total))

print("\n=== raw FastF1 Status values across 12 races ===")
for k, v in status_counts.most_common():
    print("  %-32s %d" % (k[:32], v))

print("\n=== lead-change attribution (48 total from probe B) ===")
tot = sum(attrib.values())
for k, v in attrib.most_common():
    print("  %-30s %3d  (%.0f%%)" % (k, v, 100*v/tot))
print("  TOTAL %d" % tot)

import statistics
print("\n=== retirements, fixed classifier: n=%d over 12 races (%.1f/race) ===" % (len(retire_frac), len(retire_frac)/12))
if retire_frac:
    rf = sorted(retire_frac)
    print("  median race-fraction %.3f  mean %.3f" % (statistics.median(rf), sum(rf)/len(rf)))
    for lo in (0.0, 0.25, 0.5, 0.75):
        c = sum(1 for x in rf if lo <= x < lo + 0.25)
        print("  %.2f-%.2f: %2d (%.0f%%)" % (lo, lo+0.25, c, 100*c/len(rf)))
