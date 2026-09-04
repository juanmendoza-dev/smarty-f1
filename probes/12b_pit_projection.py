"""Probe: what a pit-strategy model would actually have to do (docs/12).

`probes/12_pit_loss.py` measured the four quantities the first pass needed:
delta per circuit, how far the pit phase moves the front, whether
pit-attributable P1 changes stick, and the raw undercut success rate. Those
numbers are quoted in `probes/README.md` and are NOT recomputed here -- this
probe is additive, and it exists because building B4 raised four further
questions the spec cannot be written without.

  1. **The lead pair is not the front band.** 09 sec5.4 conditions the
     background swap rate on 09 sec2.3's position bands, and the front band
     pools P1/P2 with P2/P3. B4's replay showed the layer under-converging on
     the leader late in the race, so: what is the P1/P2 pair's OWN lap-to-lap
     swap rate, by race quarter, against the pooled P1-P3 rate the layer feeds
     the simulator?

  2. **How much of that is transient?** A simulator treats every swap as
     permanent. A pit cycle's swap is not -- the leader who stops rejoins
     behind and takes the place back. Of all lap-to-lap P1 changes, how many
     revert within a few laps, and how does that split by pit-attribution?
     This is the over-dispersion 09 sec5.7 predicts, measured.

  3. **delta per circuit, with the green filter tightened.** The first pass
     allowed anything under 1.6x the driver's baseline through, which lets a
     lap under yellow into the sample. Tightened here, with a robust spread
     (median absolute deviation) rather than an IQR that a single SC lap can
     widen.

  4. **Is stop timing predictable live at all?** This is the question that
     decides docs/12's v1 scope, and it is much sharper than "would a pit model
     help". Projecting a stop ALREADY IN PROGRESS (the car is in the pit lane;
     `CarState.in_pit` says so) needs only delta and track position. Predicting
     WHEN a car will stop is a different and far harder model. Measured here as:
     how well does stint age predict that this lap is the stop lap?

Reads only the warm FastF1 archive cache. laps=True, telemetry=False.
Same 12 archived 2026 rounds as probes/09_*.py and probes/12_pit_loss.py.

Usage: .venv312/bin/python probes/12b_pit_projection.py
"""
import sys, warnings, collections, statistics
warnings.filterwarnings("ignore")
import fastf1
import pandas as pd

fastf1.Cache.enable_cache("data/cache/fastf1")
SEASON = 2026
REVERT_WINDOW = 5          # laps within which a lead change counts as transient

lead_pair = collections.defaultdict(lambda: [0, 0])    # quarter -> [pairs, swaps]
band_pair = collections.defaultdict(lambda: [0, 0])    # quarter -> [pairs, swaps] over P1-P3
lead_changes = {"total": 0, "reverted": 0, "pit": 0, "pit_reverted": 0,
                "nonpit": 0, "nonpit_reverted": 0}
deltas = collections.defaultdict(list)
stint_hazard = collections.defaultdict(lambda: [0, 0])  # stint age -> [laps at risk, stops]
undercut_span = []          # (span_laps, success)
# The right background for an undercut is NOT "did this pair swap at least once
# over n laps" -- swaps revert, so compounding the per-lap rate overstates it.
# It is "was the car that was behind at lap L ahead at lap L+n", measured over
# adjacent pairs where NEITHER car stopped in the window, so strategy is out of
# it by construction.
background_ahead = collections.defaultdict(lambda: [0, 0])   # span -> [pairs, behind-car-ahead]
races = []


def sec(td):
    return td.total_seconds() if pd.notna(td) else None


def mad(vals):
    """Median absolute deviation, scaled to be comparable to a standard
    deviation on normal data. Robust to the one SC lap an IQR still lets in."""
    if len(vals) < 2:
        return float("nan")
    med = statistics.median(vals)
    return 1.4826 * statistics.median([abs(v - med) for v in vals])


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
    total = int(laps["LapNumber"].max())

    order, rorder = {}, {}
    for lp in range(1, total + 1):
        sub = laps[(laps["LapNumber"] == lp) & laps["Position"].notna()]
        order[lp] = {int(r["Position"]): str(r["Driver"]) for _, r in sub.iterrows()}
        rorder[lp] = {v: k for k, v in order[lp].items()}

    stops = {}
    for drv, dl in laps.groupby("Driver"):
        stops[str(drv)] = sorted(int(x) for x in
                                 dl[dl["PitInTime"].notna()]["LapNumber"].dropna())

    def pitted_near(drivers, lap, pad=2):
        for d in drivers:
            if any(abs(x - lap) <= pad for x in stops.get(d, [])):
                return True
        return False

    # --- 1. lead-pair vs front-band swap rate, by race quarter ---
    for lp in range(1, total):
        a, b = order.get(lp), order.get(lp + 1)
        if not a or not b:
            continue
        q = min(int((lp / float(total)) * 4), 3)
        where = {c: p for p, c in b.items()}
        for pos in (1, 2, 3):
            if pos not in a or pos + 1 not in a:
                continue
            d1, d2 = a[pos], a[pos + 1]
            p1, p2 = where.get(d1), where.get(d2)
            if p1 is None or p2 is None:
                continue
            band_pair[q][0] += 1
            if p2 < p1:
                band_pair[q][1] += 1
            if pos == 1:
                lead_pair[q][0] += 1
                if p2 < p1:
                    lead_pair[q][1] += 1

    # --- 2. transience of lead changes ---
    prev = None
    for lp in range(1, total + 1):
        ld = order[lp].get(1)
        if ld is None:
            continue
        if lp >= 2 and prev is not None and ld != prev:
            reverted = any(order.get(lp + k, {}).get(1) == prev
                           for k in range(1, REVERT_WINDOW + 1))
            is_pit = pitted_near([ld, prev], lp)
            lead_changes["total"] += 1
            lead_changes["reverted"] += 1 if reverted else 0
            key = "pit" if is_pit else "nonpit"
            lead_changes[key] += 1
            lead_changes[key + "_reverted"] += 1 if reverted else 0
        prev = ld

    # --- 3. delta per circuit, tighter green filter ---
    for drv, dl in laps.groupby("Driver"):
        dl = dl.sort_values("LapNumber")
        green = [sec(t) for t, pi, po in zip(dl["LapTime"], dl["PitInTime"], dl["PitOutTime"])
                 if pd.isna(pi) and pd.isna(po) and sec(t) is not None]
        if len(green) < 8:
            continue
        base = statistics.median(green)
        # Tighter than 12_pit_loss.py's 1.30: a lap 15% off a driver's own
        # median is already traffic or a slow zone, not a green racing lap.
        green = [g for g in green if g < base * 1.15]
        if len(green) < 8:
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
            # Both laps must be inside a band a green in/out lap can plausibly
            # occupy. 1.45 rather than 1.6 -- an out-lap 60% off baseline is
            # behind a safety car, and 09 sec5.6 says overtaking is forbidden
            # there anyway, so it is not a pit loss this model should learn.
            if inlap > base * 1.45 or outlap > base * 1.45:
                continue
            d = (inlap - base) + (outlap - base)
            if 5.0 < d < 40.0:
                deltas[name].append(d)

    # --- 4. is the stop lap predictable from stint age? ---
    for drv, dl in laps.groupby("Driver"):
        drv = str(drv)
        my_stops = stops.get(drv, [])
        last = 0
        for lp in range(1, total + 1):
            if lp not in rorder or drv not in rorder[lp]:
                continue
            age = lp - last
            bucket = min(age // 5, 9)
            stint_hazard[bucket][0] += 1
            if lp in my_stops:
                stint_hazard[bucket][1] += 1
                last = lp

    # --- 5. undercut, with the span it took to resolve ---
    for La in range(2, total - 1):
        if La - 1 not in order:
            continue
        for pos in sorted(order[La - 1]):
            if pos + 1 not in order[La - 1]:
                continue
            b, a = order[La - 1][pos], order[La - 1][pos + 1]
            if La not in stops.get(a, []):
                continue
            later = [x for x in stops.get(b, []) if La < x <= La + 6]
            if not later:
                continue
            Lb = later[0]
            check = Lb + 1
            if check not in rorder or a not in rorder[check] or b not in rorder[check]:
                continue
            undercut_span.append((check - (La - 1), 1 if rorder[check][a] < rorder[check][b] else 0))

    # --- 5b. the matched background: same spans, no stops by either car ---
    for span in range(2, 9):
        for L in range(1, total - span):
            a_ord = order.get(L)
            b_ord = rorder.get(L + span)
            if not a_ord or not b_ord:
                continue
            for pos in sorted(a_ord):
                if pos + 1 not in a_ord:
                    continue
                ahead_c, behind_c = a_ord[pos], a_ord[pos + 1]
                if any(L <= x <= L + span for x in stops.get(ahead_c, [])) or \
                        any(L <= x <= L + span for x in stops.get(behind_c, [])):
                    continue
                if ahead_c not in b_ord or behind_c not in b_ord:
                    continue
                background_ahead[span][0] += 1
                if b_ord[behind_c] < b_ord[ahead_c]:
                    background_ahead[span][1] += 1

    races.append(name)
    print("R%-2d %-28s laps=%d" % (rnd, name[:28], total), flush=True)

print("\n=== 1. the lead pair is not the front band ===")
print("09 sec5.4 feeds the simulator the P1-P3 band rate for the P1/P2 pair.")
print("%-10s %10s %8s %10s | %10s %8s %10s" %
      ("quarter", "P1/P2 obs", "swaps", "rate", "P1-P3 obs", "swaps", "rate"))
for q in range(4):
    ln, lk = lead_pair[q]
    bn, bk = band_pair[q]
    if ln and bn:
        print("%-10s %10d %8d %10.4f | %10d %8d %10.4f"
              % ("%.2f-%.2f" % (q / 4, (q + 1) / 4), ln, lk, lk / ln, bn, bk, bk / bn))
tl = sum(lead_pair[q][0] for q in range(4)); tk = sum(lead_pair[q][1] for q in range(4))
bl = sum(band_pair[q][0] for q in range(4)); bk2 = sum(band_pair[q][1] for q in range(4))
if tl and bl:
    print("%-10s %10d %8d %10.4f | %10d %8d %10.4f"
          % ("POOLED", tl, tk, tk / tl, bl, bk2, bk2 / bl))
    print("  ratio (band rate / lead-pair rate): %.2fx" % ((bk2 / bl) / (tk / tl)))

print("\n=== 2. how much of a lead change is transient? ===")
lc = lead_changes
if lc["total"]:
    print("  lap-to-lap P1 changes            : %d" % lc["total"])
    print("  reverted within %d laps           : %d (%.0f%%)"
          % (REVERT_WINDOW, lc["reverted"], 100 * lc["reverted"] / lc["total"]))
    for key in ("pit", "nonpit"):
        n, r = lc[key], lc[key + "_reverted"]
        if n:
            print("    %-8s %4d changes, %3d reverted (%.0f%%)"
                  % (key, n, r, 100 * r / n))

print("\n=== 3. pit delta per circuit, tightened green filter ===")
print("%-30s %5s %8s %8s %8s" % ("circuit", "n", "median", "MAD", "mean"))
alld = []
for name in races:
    v = sorted(deltas.get(name, []))
    alld += v
    if len(v) >= 4:
        print("%-30s %5d %8.1f %8.1f %8.1f"
              % (name[:30], len(v), statistics.median(v), mad(v), statistics.mean(v)))
if alld:
    print("%-30s %5d %8.1f %8.1f %8.1f"
          % ("-- POOLED --", len(alld), statistics.median(alld), mad(alld),
             statistics.mean(alld)))

print("\n=== 4. is the stop lap predictable from stint age? ===")
print("%-12s %10s %8s %10s" % ("stint age", "laps", "stops", "hazard"))
for b in sorted(stint_hazard):
    n, k = stint_hazard[b]
    if n:
        print("%-12s %10d %8d %10.4f" % ("%d-%d" % (b * 5, b * 5 + 4), n, k, k / n))
tot_n = sum(v[0] for v in stint_hazard.values())
tot_k = sum(v[1] for v in stint_hazard.values())
if tot_n:
    rates = [v[1] / v[0] for v in stint_hazard.values() if v[0] >= 100]
    print("  base rate %.4f; across buckets with >=100 laps the hazard runs %.4f-%.4f"
          % (tot_k / tot_n, min(rates), max(rates)))
    print("  read: a flat-ish hazard means stint age alone does NOT say which lap")
    print("  the stop lands on -- see docs/12 on why v1 projects a stop in progress.")

print("\n=== 5. undercut, normalised for the span it took to resolve ===")
if undercut_span:
    n = len(undercut_span)
    k = sum(s for _, s in undercut_span)
    spans = [sp for sp, _ in undercut_span]
    mean_span = statistics.mean(spans)
    print("  %d attempts, %d succeeded (%.0f%%), mean span %.1f laps"
          % (n, k, 100 * k / n, mean_span))
    print("\n  matched background -- adjacent pairs over the same span with NO stop")
    print("  by either car, so strategy is excluded by construction:")
    print("  %-8s %10s %10s %10s" % ("span", "pairs", "behind won", "rate"))
    for span in sorted(background_ahead):
        n2, k2 = background_ahead[span]
        if n2:
            print("  %-8d %10d %10d %10.4f" % (span, n2, k2, k2 / n2))
    near = [background_ahead[sp] for sp in (4, 5) if background_ahead[sp][0]]
    if near:
        bn = sum(x[0] for x in near); bk = sum(x[1] for x in near)
        print("  at the undercut's mean span (%.1f laps): background %.1f%% vs "
              "undercut %.1f%%" % (mean_span, 100 * bk / bn, 100 * k / n))
        print("  NOTE: compounding the per-lap swap rate over n laps is the WRONG")
        print("  background (swaps revert, so it counts 'at least one swap', not")
        print("  'ahead at the end'). This is the matched version.")
