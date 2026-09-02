"""Spike: does race-day wind correlate with outcome variance at all?

Gate before any corner-level wind-physics design. Cheap and reuses everything
already built: race-day wind history (archive-api.open-meteo.com, confirmed
live back to 2014, same free source weather_backtest.py already uses) crossed
against the 264-race outcome corpus already sitting in data/training/winner.csv.

No new infrastructure, touches nothing in the prediction pipeline. Standalone,
same pattern as weather_backtest.py.

Question: do windy races show more DNFs, more favorite-losses, and more
grid-to-finish churn than calm ones? If not, at race-aggregate resolution wind
doesn't move outcomes here, and corner-level modeling isn't worth building
without a different kind of evidence. If so, that's the evidence a follow-on
spec would build on.

Usage: python wind_spike.py [--refresh]
"""

import argparse
import csv
import os
import statistics
import sys
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from lib import httpcache

JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cache")
WINNER_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "training", "winner.csv")


def load_races_from_corpus():
    """One row per (season, round), aggregated from winner.csv's driver-race rows."""
    by_race = defaultdict(list)
    with open(WINNER_CSV) as f:
        for row in csv.DictReader(f):
            by_race[(row["season"], row["round"])].append(row)

    races = []
    for (season, rnd), rows in by_race.items():
        classified = [r for r in rows if r["classified"] == "1"]
        dnf_rate = 1.0 - len(classified) / len(rows)
        churn = None
        if classified:
            churn = statistics.mean(
                abs(int(r["finish_position"]) - int(r["quali_position"]))
                for r in classified
            )
        favorite = max(rows, key=lambda r: float(r["p_a1"]))
        favorite_won = favorite["label"] == "1"
        races.append({
            "season": season,
            "round": rnd,
            "circuit_id": rows[0]["circuit_id"],
            "race_date": rows[0]["race_date"],
            "n_drivers": len(rows),
            "dnf_rate": dnf_rate,
            "favorite_won": favorite_won,
            "churn": churn,
        })
    return races


def jolpica_race_meta(seasons):
    """lat/lon + start time per (season, round), same source weather_backtest.py uses."""
    meta = {}
    for season in seasons:
        url = f"{JOLPICA_BASE}/{season}/races/?format=json&limit=100"
        body, _ = httpcache.cached_get_json(url, CACHE_DIR, timeout=30)
        for r in body["MRData"]["RaceTable"]["Races"]:
            if "time" not in r:
                continue
            meta[(str(r["season"]), str(int(r["round"])))] = {
                "lat": float(r["Circuit"]["Location"]["lat"]),
                "lon": float(r["Circuit"]["Location"]["long"]),
                "date": r["date"],
                "time": r["time"],
            }
    return meta


def race_window(meta):
    start = datetime.fromisoformat(f"{meta['date']}T{meta['time'].replace('Z', '+00:00')}")
    return start - timedelta(hours=2), start + timedelta(hours=2)


def wind_for_race(meta, refresh):
    lo, hi = race_window(meta)
    params = {
        "latitude": meta["lat"],
        "longitude": meta["lon"],
        "hourly": "wind_speed_10m,wind_gusts_10m",
        "start_date": (lo - timedelta(days=1)).date().isoformat(),
        "end_date": (hi + timedelta(days=1)).date().isoformat(),
        "timezone": "UTC",
    }
    url = ARCHIVE_BASE + "?" + urllib.parse.urlencode(params)
    body, _ = httpcache.cached_get_json(url, CACHE_DIR, timeout=60, force_refresh=refresh)
    times = body["hourly"]["time"]
    idx = [i for i, t in enumerate(times)
           if lo <= datetime.fromisoformat(t).replace(tzinfo=timezone.utc) <= hi]
    speeds = [body["hourly"]["wind_speed_10m"][i] for i in idx]
    gusts = [body["hourly"]["wind_gusts_10m"][i] for i in idx]
    speeds = [v for v in speeds if v is not None]
    gusts = [v for v in gusts if v is not None]
    if not speeds or not gusts:
        return None
    return {"max_speed": max(speeds), "max_gust": max(gusts)}


def spearman(xs, ys):
    """No scipy in this repo (05 sec7's own constraint) -- hand-rolled rank correlation."""
    n = len(xs)
    if n < 3:
        return None
    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0] * n
        for rank, i in enumerate(order):
            r[i] = rank
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    sx = (sum((v - mx) ** 2 for v in rx)) ** 0.5
    sy = (sum((v - my) ** 2 for v in ry)) ** 0.5
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def build(refresh):
    races = load_races_from_corpus()
    seasons = sorted({r["season"] for r in races})
    meta = jolpica_race_meta(seasons)

    rows = []
    skipped = 0
    for race in races:
        key = (race["season"], race["round"])
        m = meta.get(key)
        if m is None:
            skipped += 1
            continue
        wind = wind_for_race(m, refresh)
        if wind is None:
            skipped += 1
            continue
        rows.append({**race, **wind})
    return rows, skipped


def report(rows, skipped):
    n = len(rows)
    print(f"n = {n} races with wind + outcome data ({skipped} skipped)\n")

    gust_speed_corr = spearman([r["max_gust"] for r in rows], [r["max_speed"] for r in rows])
    print(f"gust/speed spearman sanity check: {gust_speed_corr:.2f} (should be high -- same wind event)\n")

    # Correlation: wind severity vs each outcome, race-aggregate resolution.
    gusts = [r["max_gust"] for r in rows]
    dnf = [r["dnf_rate"] for r in rows]
    churn_rows = [r for r in rows if r["churn"] is not None]
    print("--- Spearman correlation: max gust (km/h) vs outcome ---")
    print(f"  gust vs dnf_rate:  {spearman(gusts, dnf):+.3f}  (n={n})")
    churn_gusts = [r["max_gust"] for r in churn_rows]
    churn_vals = [r["churn"] for r in churn_rows]
    print(f"  gust vs churn:     {spearman(churn_gusts, churn_vals):+.3f}  (n={len(churn_rows)})")
    print()

    # Quartile split, so a single correlation number isn't the only evidence.
    sorted_rows = sorted(rows, key=lambda r: r["max_gust"])
    q = n // 4
    calm, windy = sorted_rows[:q], sorted_rows[-q:]
    print(f"--- top/bottom quartile by max gust (n={q} each) ---")
    print(f"  calm quartile:  gust {calm[0]['max_gust']:.0f}-{calm[-1]['max_gust']:.0f} km/h  "
          f"dnf_rate {statistics.mean(r['dnf_rate'] for r in calm):.3f}  "
          f"favorite_won {statistics.mean(r['favorite_won'] for r in calm):.3f}  "
          f"churn {statistics.mean(r['churn'] for r in calm if r['churn'] is not None):.2f}")
    print(f"  windy quartile: gust {windy[0]['max_gust']:.0f}-{windy[-1]['max_gust']:.0f} km/h  "
          f"dnf_rate {statistics.mean(r['dnf_rate'] for r in windy):.3f}  "
          f"favorite_won {statistics.mean(r['favorite_won'] for r in windy):.3f}  "
          f"churn {statistics.mean(r['churn'] for r in windy if r['churn'] is not None):.2f}")
    print()

    print("--- windiest 10 races in the corpus ---")
    print(f"{'date':<12}{'circuit':<16}{'max_gust':>9}{'max_speed':>10}{'dnf_rate':>9}{'fav_won':>8}{'churn':>7}")
    for r in sorted(rows, key=lambda r: -r["max_gust"])[:10]:
        churn_s = f"{r['churn']:.2f}" if r["churn"] is not None else "n/a"
        print(f"{r['race_date']:<12}{r['circuit_id']:<16}{r['max_gust']:>9.0f}{r['max_speed']:>10.0f}"
              f"{r['dnf_rate']:>9.2f}{str(r['favorite_won']):>8}{churn_s:>7}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="bypass the disk cache")
    args = ap.parse_args()

    rows, skipped = build(args.refresh)
    if not rows:
        print("no races with coverage", file=sys.stderr)
        return 1
    report(rows, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
