"""Probe: does the SIMULATOR over-disperse, or only the rate it was fitted on?
09 sec10.2, docs/12 sec2.3.

`probes/12b_pit_projection.py` measured that net displacement at five laps is
0.61x what the archive's own per-lap swap rate compounded predicts. That is a
fact about the archive, and it says the *rate* implies more net movement than
actually happens. It is NOT by itself a measurement of `lib/winprob_sim.py`,
which is what 09 sec10.2 claims -- the simulator does not consume the raw rate.
It consumes a cell rate that has been shrunk toward its band, had retirement-
driven changes removed, and been scaled by `exp(c*(m-1))`, and it then multiplies
that by the strength tilt `2*w_b/(w_a+w_b)`, which is asymmetric: a strong car
ahead of a weak one swaps well below `q`.

So the tilt may already absorb some of the gap, and attributing the whole 1.6x
to the simulator would be exactly the confident-unverified claim this project
keeps being bitten by (`08` sec2, `03`'s correction banner).

This probe measures it directly and matched: from the observed order at lap L,
run the real `forward_simulate` for exactly five lap-steps at the true race
progress, and count how often the car that was behind is ahead -- against the
same pairs' archive outcome at L+5, over the same races, with retirement
switched off on both sides so the comparison isolates the swap process.

Needs `data/live/winprob/fit.json` (run `winprob_fit.py` first).

Usage: .venv312/bin/python probes/09b_dispersion.py
"""
import collections
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

# Unlike the other probes this one imports the layer itself, because measuring
# the simulator is the whole point -- reimplementing five lap-steps here would
# measure a copy of it instead.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import winprob_background as bgmod
from lib import winprob_replay as wpr
from lib import winprob_sim as wsim

FIT = "data/live/winprob/fit.json"
SPAN = 5
N_PATHS = 4000
SEASON = 2026

try:
    fit = json.load(open(FIT))
except FileNotFoundError:
    sys.exit("%s not found -- run winprob_fit.py first" % FIT)

sim_stat = collections.defaultdict(lambda: [0, 0])      # band -> [pairs, behind ahead]
arc_stat = collections.defaultdict(lambda: [0, 0])
q_used = collections.defaultdict(list)
# The lead pair on its own, and split by race quarter. 09 sec5.4 conditions on
# 09 sec2.3's bands, so the P1/P2 pair is represented by the P1-P3 band rate --
# and 12b measured the lead pair swapping at 0.0055/lap in the final quarter
# against the band's 0.0351. If the simulator tracks the band but not the lead
# pair, that is where the late-race leader error lives.
lead_sim = collections.defaultdict(lambda: [0, 0])      # quarter -> [paths, behind ahead]
lead_arc = collections.defaultdict(lambda: [0, 0])

for rnd_s in sorted(fit["races"], key=int):
    rnd = int(rnd_s)
    blob = fit["races"][rnd_s]
    background = bgmod.BackgroundRate.from_dict(blob["background"])
    rec = blob["reconciled"]
    strengths, m = rec["strengths"], rec["m"]
    try:
        archive = wpr.RaceArchive(SEASON, rnd, telemetry=False)
    except Exception as e:                              # noqa: BLE001
        sys.stderr.write("R%d: skip (%s)\n" % (rnd, type(e).__name__))
        continue
    total = archive.total_laps
    order_by_lap = archive.order_by_lap()

    for L in range(1, total - SPAN):
        a_ord = order_by_lap.get(L)
        far = order_by_lap.get(L + SPAN)
        if not a_ord or not far:
            continue
        far_pos = {c: p for p, c in far.items()}
        codes = [a_ord[p] for p in sorted(a_ord)]
        if len(codes) < 4:
            continue
        # Retirement off on both sides: 09 sec5.4 already removes
        # retirement-driven changes from the rate, so including attrition here
        # would compare the swap process against something else.
        hz = {c: [0.0] * (total - L + 1) for c in codes}
        _, _, info = wsim.forward_simulate(
            "disp:%d:%d" % (rnd, L), codes, strengths, hz, background, L, total,
            track_frac=0.0, m=m, pursuits=(), n_paths=N_PATHS,
            use_overtake_model=False, collect_orders=True, horizon_laps=SPAN)
        orders = info["orders"]
        rank = orders.argsort(axis=1)                   # car index -> slot
        for k in range(len(codes) - 1):
            ahead_c, behind_c = codes[k], codes[k + 1]
            band = bgmod.band_of(k + 1)
            n_behind_ahead = int((rank[:, k + 1] < rank[:, k]).sum())
            sim_stat[band][0] += N_PATHS
            sim_stat[band][1] += n_behind_ahead
            q_used[band].append(background.rate(k + 1, (L + 0.5) / total, m))
            matched = ahead_c in far_pos and behind_c in far_pos
            if matched:
                arc_stat[band][0] += 1
                if far_pos[behind_c] < far_pos[ahead_c]:
                    arc_stat[band][1] += 1
            if k == 0 and matched:
                qq = min(int((L / float(total)) * 4), 3)
                lead_sim[qq][0] += N_PATHS
                lead_sim[qq][1] += n_behind_ahead
                lead_arc[qq][0] += 1
                if far_pos[behind_c] < far_pos[ahead_c]:
                    lead_arc[qq][1] += 1
    print("R%-2d %-28s laps=%d" % (rnd, str(archive.session.event["EventName"])[:28], total),
          flush=True)

print("\n=== simulator vs archive: is the car that was behind ahead %d laps later? ===" % SPAN)
print("%-10s %10s %10s %10s %10s %9s" %
      ("band", "mean q", "sim net", "archive net", "sim/archive", "arc pairs"))
tot_s = [0, 0]
tot_a = [0, 0]
for band in bgmod.BAND_NAMES:
    sn, sk = sim_stat[band]
    an, ak = arc_stat[band]
    if not sn or not an:
        continue
    tot_s[0] += sn; tot_s[1] += sk
    tot_a[0] += an; tot_a[1] += ak
    qm = sum(q_used[band]) / len(q_used[band])
    print("%-10s %10.4f %10.4f %10.4f %10.2f %9d"
          % (band, qm, sk / sn, ak / an, (sk / sn) / (ak / an) if ak else float("nan"), an))
if tot_s[0] and tot_a[0] and tot_a[1]:
    s_net, a_net = tot_s[1] / tot_s[0], tot_a[1] / tot_a[0]
    print("%-10s %10s %10.4f %10.4f %10.2f %9d"
          % ("POOLED", "", s_net, a_net, s_net / a_net, tot_a[0]))
    print("\n  read: the ratio above is the SIMULATOR's over-dispersion. 12b's 0.61 is the")
    print("  archive's own rate against its own net displacement, which is a different")
    print("  quantity -- the simulator's shrinkage, retirement exclusion, circuit term and")
    print("  strength tilt all sit between the two.")

print("\n=== the LEAD PAIR alone (slot P1/P2), by race quarter ===")
print("09 sec5.4 hands the P1/P2 pair the pooled P1-P3 band rate.")
print("%-12s %10s %12s %12s %9s" % ("quarter", "sim net", "archive net", "sim/archive", "pairs"))
ts = [0, 0]; ta = [0, 0]
for qq in range(4):
    sn, sk = lead_sim[qq]
    an, ak = lead_arc[qq]
    if not sn or not an:
        continue
    ts[0] += sn; ts[1] += sk; ta[0] += an; ta[1] += ak
    print("%-12s %10.4f %12.4f %12s %9d"
          % ("%.2f-%.2f" % (qq / 4, (qq + 1) / 4), sk / sn, ak / an,
             ("%.2f" % ((sk / sn) / (ak / an))) if ak else "inf", an))
if ts[0] and ta[0]:
    print("%-12s %10.4f %12.4f %12s %9d"
          % ("POOLED", ts[1] / ts[0], ta[1] / ta[0],
             ("%.2f" % ((ts[1] / ts[0]) / (ta[1] / ta[0]))) if ta[1] else "inf", ta[0]))
