"""Probe: 09-live-win-probability.md sec2.2, sec2.3, sec2.6 -- race dynamics.

Five quantities the live win-probability spec cannot be written without:
  1. leader-conversion ladder  -- P(leader at lap L is the eventual winner)
  2. per-lap adjacent-pair swap rate, by position band  (the background process)
  3. lead changes after lap 1, all 12 archived 2026 races
  4. retirement lap distribution (is the DNF hazard front-loaded?)
     -- 09_leadchange_attribution.py is the authoritative source for this one;
        it is recomputed here only as a cross-check that the two agree.
  5. pit-cycle time fraction (how much of the race is a pit cycle in progress?)

Reads only the warm FastF1 archive cache. laps=True, telemetry=False -- this is
lap-level, so it does not need the ~GB car/pos channels.
"""
import sys, warnings, collections
warnings.filterwarnings("ignore")
import fastf1
import pandas as pd

fastf1.Cache.enable_cache("data/cache/fastf1")

SEASON = 2026
out = {"races": [], "ladder": collections.defaultdict(lambda: [0, 0]),
       "swaps": collections.defaultdict(lambda: [0, 0]),
       "retire_frac": [], "pit_frac": [], "lead_changes": {}}

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
    winner = None
    if res is not None and not res.empty:
        r0 = res.sort_values("Position").iloc[0]
        winner = str(r0["Abbreviation"])
    total = int(laps["LapNumber"].max())

    # per-lap order: {lap: {pos: drv}}
    order = {}
    for lp in range(1, total + 1):
        sub = laps[(laps["LapNumber"] == lp) & laps["Position"].notna()]
        order[lp] = {int(r["Position"]): str(r["Driver"]) for _, r in sub.iterrows()}

    # 1. leader ladder + 3. lead changes
    lc, prev_leader = 0, None
    for lp in range(1, total + 1):
        ld = order[lp].get(1)
        if ld is None:
            continue
        if lp >= 2 and prev_leader is not None and ld != prev_leader:
            lc += 1
        prev_leader = ld
        rem = total - lp
        bucket = (min(rem, 60) // 5) * 5
        b = out["ladder"][bucket]
        b[0] += 1
        if winner is not None and ld == winner:
            b[1] += 1
    out["lead_changes"][name] = lc

    # 2. adjacent-pair swap rate per lap, by band
    BANDS = [("P1-P3", 1, 3), ("P4-P6", 4, 6), ("P7-P10", 7, 10),
             ("P11-P15", 11, 15), ("P16+", 16, 30)]
    def band(p):
        for nm, lo, hi in BANDS:
            if lo <= p <= hi: return nm
        return "P16+"
    for lp in range(1, total):
        a, b2 = order[lp], order[lp + 1]
        if not a or not b2: continue
        for pos in sorted(a):
            if pos + 1 not in a: continue
            d1, d2 = a[pos], a[pos + 1]
            # did the adjacent pair invert by the next lap?
            p1 = next((p for p, d in b2.items() if d == d1), None)
            p2 = next((p for p, d in b2.items() if d == d2), None)
            if p1 is None or p2 is None: continue
            st = out["swaps"][band(pos)]
            st[0] += 1
            if p2 < p1:
                st[1] += 1

    # 4. retirement lap fraction
    n_ret = 0
    if res is not None and not res.empty:
        for _, r in res.iterrows():
            st = str(r.get("Status", ""))
            cl = str(r.get("ClassifiedPosition", ""))
            drv = str(r["Abbreviation"])
            dl = laps[laps["Driver"] == drv]["LapNumber"]
            last = int(dl.max()) if len(dl) else 0
            # "Lapped" IS a finish. Classifying it as a retirement inflates the
            # count from 4.2/race to 6.6/race and flips the distribution from
            # front-loaded to back-loaded -- the correction recorded in 09 sec15,
            # caught by checking against 04 sec5.1's measured 12.53% 2025 DNF rate.
            finished = st in ("Finished", "Lapped") or st.startswith("+") or st == "Did not start"
            if not finished and last > 0 and last < total - 1:
                out["retire_frac"].append(last / float(total))
                n_ret += 1

    # 5. pit-cycle fraction: laps on which >=1 car pitted
    pit_laps = set(int(x) for x in laps[laps["PitInTime"].notna()]["LapNumber"].dropna())
    out["pit_frac"].append((len(pit_laps), total))

    out["races"].append({"round": rnd, "name": name, "total_laps": total,
                         "winner": winner, "lead_changes": lc,
                         "retirements": n_ret, "pit_laps": len(pit_laps)})
    print("R%-2d %-28s laps=%-3d winner=%-4s leadchg=%d ret=%d pitlaps=%d"
          % (rnd, name[:28], total, winner, lc, n_ret, len(pit_laps)))

print("\n=== 1. leader-conversion ladder (all 12 races pooled) ===")
print("%-14s %8s %8s %8s" % ("laps_remaining", "obs", "leader_won", "rate"))
for b in sorted(out["ladder"]):
    n, w = out["ladder"][b]
    print("%-14s %8d %8d %8.3f" % ("%d-%d" % (b, b+4), n, w, w/n if n else 0))

print("\n=== 2. per-lap adjacent-pair swap rate ===")
print("%-8s %10s %8s %10s" % ("band", "pairs", "swaps", "rate/lap"))
for nm in ["P1-P3", "P4-P6", "P7-P10", "P11-P15", "P16+"]:
    n, k = out["swaps"][nm]
    if n: print("%-8s %10d %8d %10.5f" % (nm, n, k, k/n))

print("\n=== 3. lead changes after lap 1 ===")
tot = 0
for k, v in out["lead_changes"].items():
    print("  %-30s %d" % (k[:30], v)); tot += v
print("  TOTAL %d across %d races" % (tot, len(out["lead_changes"])))

print("\n=== 4. retirement lap fraction (n=%d) ===" % len(out["retire_frac"]))
rf = sorted(out["retire_frac"])
if rf:
    import statistics
    print("  median %.3f  mean %.3f" % (statistics.median(rf), sum(rf)/len(rf)))
    for lo in (0.0, 0.25, 0.5, 0.75):
        c = sum(1 for x in rf if lo <= x < lo+0.25)
        print("  race fraction %.2f-%.2f: %d retirements (%.1f%%)" % (lo, lo+0.25, c, 100*c/len(rf)))

print("\n=== 5. pit-cycle laps ===")
tp, tt = sum(a for a, _ in out["pit_frac"]), sum(b for _, b in out["pit_frac"])
print("  laps with >=1 pit stop: %d of %d race-laps = %.1f%%" % (tp, tt, 100*tp/tt))
for r in out["races"]:
    print("   R%-2d %-26s %3d/%3d = %.0f%%" % (r["round"], r["name"][:26], r["pit_laps"],
          r["total_laps"], 100*r["pit_laps"]/r["total_laps"]))

