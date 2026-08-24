"""Backtest the multi-model weather ensemble against observed rainfall.

Produces every number quoted in 06-weather-ensemble-signal.md sec5. Standalone
verification tooling -- it imports lib.httpcache for the disk cache and touches
nothing in the prediction pipeline, so running it cannot affect a snapshot.

For each past race it pulls three things over the same race window snapshot.py
uses (lights-out +/- 2h, see snapshot.py:320):

  observed   archive API, precipitation in mm -> wet, on snapshot.py:288's
             `max mm > 0.0` rule and on stricter thresholds for comparison
  blended    historical-forecast API with no `models=` -- what the pipeline
             asks for today, replayed as it stood on race day
  ensemble   historical-forecast API with `models=` -- the four named models

Window arithmetic runs in UTC. Those are the same instants as snapshot.py's
local +/-2h, and it avoids CIRCUIT_TIMEZONE, which covers 15 of 33 circuits
(00-roadmap.md, A3 prerequisite). Contrast sec5.4's warning: that is about
*reading* local hours off a UTC-defaulted response, which is a different bug.

Usage:  python3 weather_backtest.py [--from-season 2024] [--refresh]
"""

import argparse
import json
import os
import statistics
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

from lib import httpcache

JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"
HISTORICAL_FORECAST_BASE = "https://historical-forecast-api.open-meteo.com/v1/forecast"

MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless"]

# F7's dormancy gate (02-winner-prediction-algo.md sec4, F7).
P_GATE = 40

# snapshot.py:288's wet rule, plus stricter ones. > 0.0mm counts a 0.1mm trace
# as a wet race; see sec5.4 of the spec for why that matters here.
WET_THRESHOLDS = [0.0, 0.5, 1.0]

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cache")


def races_with_start_times(first_season, last_season):
    """Every race in the range that publishes a start time (needed for the window)."""
    out = []
    for season in range(first_season, last_season + 1):
        url = f"{JOLPICA_BASE}/{season}/races/?format=json&limit=100"
        body, _ = httpcache.cached_get_json(url, CACHE_DIR, timeout=30)
        for r in body["MRData"]["RaceTable"]["Races"]:
            if "time" not in r:
                continue
            out.append({
                "season": int(r["season"]),
                "round": int(r["round"]),
                "name": r["raceName"],
                "circuit": r["Circuit"]["circuitId"],
                "lat": float(r["Circuit"]["Location"]["lat"]),
                "lon": float(r["Circuit"]["Location"]["long"]),
                "date": r["date"],
                "time": r["time"],
            })
    return out


def race_window(race):
    start = datetime.fromisoformat(f"{race['date']}T{race['time'].replace('Z', '+00:00')}")
    return start - timedelta(hours=2), start + timedelta(hours=2)


def _window_indices(times, lo, hi):
    idx = []
    for i, t in enumerate(times):
        stamp = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        if lo <= stamp <= hi:
            idx.append(i)
    return idx


def _pull(base, race, extra, refresh):
    """One hourly pull spanning the race window, padded a day either side."""
    lo, hi = race_window(race)
    params = {
        "latitude": race["lat"],
        "longitude": race["lon"],
        "start_date": (lo - timedelta(days=1)).date().isoformat(),
        "end_date": (hi + timedelta(days=1)).date().isoformat(),
        "timezone": "UTC",
        **extra,
    }
    url = base + "?" + urllib.parse.urlencode(params)
    body, _ = httpcache.cached_get_json(url, CACHE_DIR, timeout=60, force_refresh=refresh)
    return body, _window_indices(body["hourly"]["time"], lo, hi)


def observed(race, refresh):
    body, idx = _pull(ARCHIVE_BASE, race, {"hourly": "precipitation"}, refresh)
    vals = [body["hourly"]["precipitation"][i] for i in idx]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def blended(race, refresh):
    """No `models=` -- the provider's own blend, which is what the pipeline gets today."""
    body, idx = _pull(HISTORICAL_FORECAST_BASE, race,
                      {"hourly": "precipitation_probability"}, refresh)
    vals = [body["hourly"]["precipitation_probability"][i] for i in idx]
    return None if any(v is None for v in vals) or not vals else max(vals)


def ensemble(race, refresh):
    """Per-model probability series over the window, or None if any model is absent.

    Before roughly 2024-05 this endpoint returns the per-model keys with every
    value null rather than an error -- a silent gap, not a failure (spec sec3.3).
    """
    body, idx = _pull(HISTORICAL_FORECAST_BASE, race,
                      {"hourly": "precipitation_probability", "models": ",".join(MODELS)},
                      refresh)
    hourly = body["hourly"]
    series = {}
    for m in MODELS:
        key = f"precipitation_probability_{m}"
        if key not in hourly:
            return None
        vals = [hourly[key][i] for i in idx]
        if not vals or any(v is None for v in vals):
            return None
        series[m] = vals
    return series


def derive(series):
    """The three aggregates, in the order sec4 fixes them.

    p_mean and p_max collapse models first, then hours. p_spread is the *median*
    hourly spread, not the max -- the max-over-window distribution is badly skewed
    (sec5.3), so a single volatile hour would otherwise set the whole race's flag.
    """
    hours = list(zip(*(series[m] for m in MODELS)))
    return {
        "p_mean": max(sum(h) / len(MODELS) for h in hours),
        "p_max": max(max(h) for h in hours),
        "p_spread": statistics.median([max(h) - min(h) for h in hours]),
    }


def confusion(rows, gate_key, wet_key):
    tp = sum(1 for r in rows if r[gate_key] >= P_GATE and r[wet_key])
    fp = sum(1 for r in rows if r[gate_key] >= P_GATE and not r[wet_key])
    fn = sum(1 for r in rows if r[gate_key] < P_GATE and r[wet_key])
    tn = sum(1 for r in rows if r[gate_key] < P_GATE and not r[wet_key])
    recall = tp / (tp + fn) if tp + fn else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    return tp, fp, fn, tn, recall, precision


def build(first_season, last_season, today, refresh):
    rows = []
    skipped = 0
    for race in races_with_start_times(first_season, last_season):
        if race["date"] > today:
            continue
        obs = observed(race, refresh)
        series = ensemble(race, refresh)
        if obs is None or series is None:
            skipped += 1
            continue
        row = {**race, "obs_mm": obs, "blend": blended(race, refresh), "per_model": series}
        row.update(derive(series))
        for t in WET_THRESHOLDS:
            row[f"wet_{t}"] = obs > 0.0 if t == 0.0 else obs >= t
        rows.append(row)
    return rows, skipped


def report(rows, skipped):
    n = len(rows)
    print(f"n = {n} races with full four-model coverage "
          f"({rows[0]['date']} .. {rows[-1]['date']}); {skipped} skipped for missing data\n")

    gates = [("blend", "blended default (today)"), ("p_mean", "p_mean (4-model)"),
             ("p_max", "p_max (4-model)")]

    for t in WET_THRESHOLDS:
        wet_key = f"wet_{t}"
        rule = "> 0.0mm (snapshot.py:288)" if t == 0.0 else f">= {t}mm"
        print(f"--- gate at P>={P_GATE}, wet = observed {rule}  "
              f"({sum(1 for r in rows if r[wet_key])}/{n} wet) ---")
        print(f"{'gate input':<26}{'TP':>4}{'FP':>4}{'FN':>4}{'TN':>4}{'recall':>9}{'precision':>11}")
        for key, label in gates:
            if any(r[key] is None for r in rows):
                continue
            tp, fp, fn, tn, rec, pre = confusion(rows, key, wet_key)
            print(f"{label:<26}{tp:>4}{fp:>4}{fn:>4}{tn:>4}{rec:>8.0%}{pre:>11.0%}")
        print()

    print("--- p_spread: does model disagreement flag the gate's own errors? ---")
    print("(error = today's blended gate disagreeing with observed >= 0.5mm)")
    print(f"{'agree threshold':<18}{'n agree':>9}{'errors':>9}{'n disagree':>13}{'errors':>9}")
    for thr in (10, 15, 20, 25, 30):
        agree = [r for r in rows if r["p_spread"] < thr]
        disagree = [r for r in rows if r["p_spread"] >= thr]
        ea = sum(1 for r in agree if (r["blend"] >= P_GATE) != r["wet_0.5"])
        ed = sum(1 for r in disagree if (r["blend"] >= P_GATE) != r["wet_0.5"])
        print(f"{'< ' + str(thr) + 'pp':<18}{len(agree):>9}{ea:>4} ({ea/len(agree) if agree else 0:>3.0%})"
              f"{len(disagree):>13}{ed:>4} ({ed/len(disagree) if disagree else 0:>3.0%})")

    print("\n--- every race with observed >= 0.5mm ---")
    print(f"{'date':<12}{'race':<30}{'obs mm':>7}{'blend':>7}{'p_mean':>8}{'p_max':>7}{'p_spread':>10}")
    for r in sorted([x for x in rows if x["wet_0.5"]], key=lambda r: -r["obs_mm"]):
        print(f"{r['date']:<12}{r['name'][:29]:<30}{r['obs_mm']:>7.1f}{r['blend']:>7}"
              f"{r['p_mean']:>8.1f}{r['p_max']:>7}{r['p_spread']:>10.1f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-season", type=int, default=2024,
                    help="first season to consider (per-model history starts ~2024-05)")
    ap.add_argument("--to-season", type=int, default=datetime.now(timezone.utc).year)
    ap.add_argument("--today", default=datetime.now(timezone.utc).date().isoformat(),
                    help="ignore races after this date")
    ap.add_argument("--refresh", action="store_true", help="bypass the disk cache")
    ap.add_argument("--json", metavar="PATH", help="also write the per-race rows here")
    args = ap.parse_args()

    rows, skipped = build(args.from_season, args.to_season, args.today, args.refresh)
    if not rows:
        print("no races with full coverage in that range", file=sys.stderr)
        return 1
    report(rows, skipped)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(rows, f, indent=1)
        print(f"\nwrote {len(rows)} rows to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
