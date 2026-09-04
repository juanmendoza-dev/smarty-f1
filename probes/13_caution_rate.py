"""Probe: safety car / VSC / red flag rate per circuit, ahead of a caution-
probability spec (docs/13).

This project does not spec a feature from mechanism alone (docs/11, 09 sec2's
opening) -- the numbers come first. A per-circuit caution *rate* needs several
editions of the same circuit, not one season: 09/12's probes reuse "the 12
archived 2026 rounds" because they need many stops/swaps *within* a season,
but every circuit runs once a season, so one season gives n=1 per circuit --
a fact, not a rate. This probe therefore pulls multiple seasons.

Reads only session_status/track_status + lap_count from the warm-or-cold
FastF1 archive cache. telemetry=False keeps this cheap; laps=True is required
even though no lap-by-lap field is read -- verified live, session.track_status
comes back empty with laps=False on this FastF1 version (v3.8.3).

FastF1 TrackStatus codes (confirmed live against 2022/2026 data, not assumed):
    1 AllClear   2 Yellow (local, not field-affecting)   4 SCDeployed
    5 Red        6 VSCDeployed   7 VSCEnding
Only 4/5/6 count as a "caution event" here -- a local double-waved yellow
(code 2) is common and does not compress the field the way an SC/VSC/red does,
which is the question this probe exists to answer.

Usage: .venv312/bin/python probes/13_caution_rate.py [--from-season 2019] [--to-season 2026]
"""
import argparse
import collections
import sys
import warnings

warnings.filterwarnings("ignore")
import fastf1
import pandas as pd

fastf1.Cache.enable_cache("data/cache/fastf1")

CAUTION_CODES = {"4": "SC", "5": "RED", "6": "VSC"}


def sec(td):
    return td.total_seconds() if pd.notna(td) else None


def race_caution_summary(ts, session_end_s):
    """One race's track_status frame -> (had_caution, n_deployments, caution_seconds).

    n_deployments counts each *start* of an SC/VSC/red segment once (VSCEnding
    is the close of a VSC segment, not a new one, and is not double-counted).
    caution_seconds sums time from each deployment to the next AllClear (1) --
    a session that ends still under caution (rare, e.g. a race stopped by red
    flag and not restarted) is closed out at session_end_s rather than dropped.
    """
    events = []
    open_start, open_kind = None, None
    for _, row in ts.iterrows():
        code = str(row["Status"])
        t = sec(row["Time"])
        if t is None:
            continue
        if code in CAUTION_CODES and open_start is None:
            open_start, open_kind = t, CAUTION_CODES[code]
        elif code == "1" and open_start is not None:
            events.append((open_kind, open_start, t))
            open_start, open_kind = None, None
    if open_start is not None:
        events.append((open_kind, open_start, session_end_s))

    n_deployments = len(events)
    caution_seconds = sum(e - s for _, s, e in events)
    kinds = sorted({k for k, _, _ in events})
    return bool(events), n_deployments, caution_seconds, kinds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-season", type=int, default=2019)
    ap.add_argument("--to-season", type=int, default=2026)
    args = ap.parse_args()

    by_circuit = collections.defaultdict(list)  # location -> [(season, had, n_dep, frac, kinds)]
    races_seen = 0

    for season in range(args.from_season, args.to_season + 1):
        try:
            sched = fastf1.get_event_schedule(season, include_testing=False)
        except Exception as e:
            sys.stderr.write("season %d: schedule skip (%s)\n" % (season, type(e).__name__))
            continue
        for _, ev in sched.iterrows():
            rnd = int(ev["RoundNumber"])
            if rnd == 0:
                continue
            location = str(ev["Location"])
            name = str(ev["EventName"])
            try:
                s = fastf1.get_session(season, rnd, "R")
                s.load(telemetry=False, laps=True, weather=False, messages=False)
                ts = s.track_status
            except Exception as e:
                sys.stderr.write("%d R%d %s: skip (%s)\n" % (season, rnd, name, type(e).__name__))
                continue
            if ts is None or ts.empty:
                sys.stderr.write("%d R%d %s: no track_status\n" % (season, rnd, name))
                continue

            session_end_s = sec(ts["Time"].iloc[-1])
            had, n_dep, caution_s, kinds = race_caution_summary(ts, session_end_s)
            frac = caution_s / session_end_s if session_end_s else None

            by_circuit[location].append((season, had, n_dep, frac, kinds))
            races_seen += 1
            print("%d R%-2d %-22s caution=%s n_dep=%d frac=%s kinds=%s"
                  % (season, rnd, location[:22], had, n_dep,
                     ("%.1f%%" % (100 * frac)) if frac is not None else "n/a", kinds))

    print("\n=== per-circuit caution rate, %d editions across %d-%d ===" %
          (races_seen, args.from_season, args.to_season))
    print("%-24s %5s %8s %10s %10s %8s" %
          ("circuit", "n", "had_%", "mean_dep", "mean_frac%", "SC%  VSC%"))
    rows = []
    for loc, entries in by_circuit.items():
        n = len(entries)
        had_pct = 100 * sum(1 for e in entries if e[1]) / n
        mean_dep = sum(e[2] for e in entries) / n
        fracs = [e[3] for e in entries if e[3] is not None]
        mean_frac = 100 * sum(fracs) / len(fracs) if fracs else None
        sc_pct = 100 * sum(1 for e in entries if "SC" in e[4]) / n
        vsc_pct = 100 * sum(1 for e in entries if "VSC" in e[4]) / n
        rows.append((loc, n, had_pct, mean_dep, mean_frac, sc_pct, vsc_pct))

    for loc, n, had_pct, mean_dep, mean_frac, sc_pct, vsc_pct in sorted(rows, key=lambda r: -r[2]):
        print("%-24s %5d %7.0f%% %8.2f %9s %6.0f%% %5.0f%%" %
              (loc[:24], n, had_pct, mean_dep,
               ("%.1f%%" % mean_frac) if mean_frac is not None else "n/a", sc_pct, vsc_pct))

    all_had = [e[1] for entries in by_circuit.values() for e in entries]
    if all_had:
        print("\npooled: %d races, %d had a caution (%.0f%%)"
              % (len(all_had), sum(all_had), 100 * sum(all_had) / len(all_had)))
    else:
        print("\nno races loaded -- nothing to pool")


if __name__ == "__main__":
    main()
