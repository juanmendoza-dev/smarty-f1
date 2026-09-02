"""Spike: temperature, humidity, and cross-variable forecast disagreement vs outcomes.

Same discipline as wind_spike.py -- test before designing. Three hypotheses,
one script:

  1. Race-window temperature vs DNF rate / churn / the algo's own Brier score
     (p_a1 vs label, already in winner.csv -- a sharper test than a proxy,
     since it asks "is A1 actually wrong more often under these conditions").
  2. Same for humidity.
  3. Whether cross-model *disagreement* on temperature/humidity (the same
     mechanism 06 sec5.3 proved for rain -- p_spread marks unreliable
     forecasts) generalizes to these variables. Bounded to the ~2024-05+
     window the four-model historical-forecast endpoint actually covers
     (06 sec3.3), so this part runs on a much smaller n than 1/2.

Standalone, cached, touches nothing in the prediction pipeline.

Usage: python temp_humidity_spike.py [--refresh]
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
from lib.httpcache import HttpError

JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"
HISTORICAL_FORECAST_BASE = "https://historical-forecast-api.open-meteo.com/v1/forecast"
MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless"]

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cache")
WINNER_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "training", "winner.csv")


def load_races_from_corpus():
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
                abs(int(r["finish_position"]) - int(r["quali_position"])) for r in classified
            )
        brier = sum((float(r["p_a1"]) - float(r["label"])) ** 2 for r in rows)
        races.append({
            "season": season, "round": rnd,
            "circuit_id": rows[0]["circuit_id"], "race_date": rows[0]["race_date"],
            "dnf_rate": dnf_rate, "churn": churn, "brier": brier,
        })
    return races


def jolpica_race_meta(seasons):
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
                "date": r["date"], "time": r["time"],
            }
    return meta


def race_window(meta):
    start = datetime.fromisoformat(f"{meta['date']}T{meta['time'].replace('Z', '+00:00')}")
    return start - timedelta(hours=2), start + timedelta(hours=2)


def _window_idx(times, lo, hi):
    return [i for i, t in enumerate(times)
            if lo <= datetime.fromisoformat(t).replace(tzinfo=timezone.utc) <= hi]


def single_model_reading(meta, refresh):
    lo, hi = race_window(meta)
    params = {
        "latitude": meta["lat"], "longitude": meta["lon"],
        "hourly": "temperature_2m,relative_humidity_2m",
        "start_date": (lo - timedelta(days=1)).date().isoformat(),
        "end_date": (hi + timedelta(days=1)).date().isoformat(),
        "timezone": "UTC",
    }
    url = ARCHIVE_BASE + "?" + urllib.parse.urlencode(params)
    body, _ = httpcache.cached_get_json(url, CACHE_DIR, timeout=60, force_refresh=refresh)
    idx = _window_idx(body["hourly"]["time"], lo, hi)
    temps = [body["hourly"]["temperature_2m"][i] for i in idx if body["hourly"]["temperature_2m"][i] is not None]
    hums = [body["hourly"]["relative_humidity_2m"][i] for i in idx if body["hourly"]["relative_humidity_2m"][i] is not None]
    if not temps or not hums:
        return None
    return {"max_temp": max(temps), "mean_temp": statistics.mean(temps),
            "max_hum": max(hums), "mean_hum": statistics.mean(hums)}


def ensemble_spread(meta, refresh):
    """Cross-model disagreement on temp/humidity -- only where 4-model history exists."""
    lo, hi = race_window(meta)
    params = {
        "latitude": meta["lat"], "longitude": meta["lon"],
        "hourly": "temperature_2m,relative_humidity_2m", "models": ",".join(MODELS),
        "start_date": (lo - timedelta(days=1)).date().isoformat(),
        "end_date": (hi + timedelta(days=1)).date().isoformat(),
        "timezone": "UTC",
    }
    url = HISTORICAL_FORECAST_BASE + "?" + urllib.parse.urlencode(params)
    try:
        body, _ = httpcache.cached_get_json(url, CACHE_DIR, timeout=60, force_refresh=refresh)
    except HttpError:
        # Out of the endpoint's allowed date range (currently 2016-01-01 onward) --
        # a hard 400, unlike the null-but-200 gap 06 sec3.3 found for precipitation.
        return None
    idx = _window_idx(body["hourly"]["time"], lo, hi)
    if not idx:
        return None
    for var in ("temperature_2m", "relative_humidity_2m"):
        for m in MODELS:
            key = f"{var}_{m}"
            if key not in body["hourly"]:
                return None
            vals = [body["hourly"][key][i] for i in idx]
            if any(v is None for v in vals):
                return None
    def spread(var):
        per_hour = []
        for i in idx:
            vals = [body["hourly"][f"{var}_{m}"][i] for m in MODELS]
            per_hour.append(max(vals) - min(vals))
        return statistics.median(per_hour)
    return {"temp_spread": spread("temperature_2m"), "hum_spread": spread("relative_humidity_2m")}


def spearman(xs, ys):
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


def quartile_report(label, rows, key, outcome_keys):
    sorted_rows = sorted(rows, key=lambda r: r[key])
    q = len(rows) // 4
    lo, hi = sorted_rows[:q], sorted_rows[-q:]
    print(f"--- {label}: bottom/top quartile by {key} (n={q} each) ---")
    for name, bucket in (("low ", lo), ("high", hi)):
        vals = f"{bucket[0][key]:.1f}-{bucket[-1][key]:.1f}"
        stats = "  ".join(
            f"{k} {statistics.mean(r[k] for r in bucket if r[k] is not None):.3f}"
            for k in outcome_keys
        )
        print(f"  {name} ({vals}): {stats}")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    races = load_races_from_corpus()
    seasons = sorted({r["season"] for r in races})
    meta = jolpica_race_meta(seasons)

    rows = []
    ens_rows = []
    skipped = 0
    for race in races:
        m = meta.get((race["season"], race["round"]))
        if m is None:
            skipped += 1
            continue
        reading = single_model_reading(m, args.refresh)
        if reading is None:
            skipped += 1
            continue
        row = {**race, **reading}
        rows.append(row)

        spread = ensemble_spread(m, args.refresh)
        if spread is not None:
            ens_rows.append({**row, **spread})

    print(f"n = {len(rows)} races, single-model temp/humidity ({skipped} skipped)")
    print(f"n = {len(ens_rows)} races with 4-model temp/humidity coverage (bounded to ~2024-05+)\n")

    print("=== 1. Temperature vs outcome (full corpus) ===")
    temps = [r["max_temp"] for r in rows]
    print(f"  max_temp vs dnf_rate:  {spearman(temps, [r['dnf_rate'] for r in rows]):+.3f}")
    print(f"  max_temp vs brier:     {spearman(temps, [r['brier'] for r in rows]):+.3f}")
    churn_rows = [r for r in rows if r["churn"] is not None]
    print(f"  max_temp vs churn:     {spearman([r['max_temp'] for r in churn_rows], [r['churn'] for r in churn_rows]):+.3f}")
    print()
    quartile_report("temperature", rows, "max_temp", ["dnf_rate", "brier"])

    print("=== 2. Humidity vs outcome (full corpus) ===")
    hums = [r["max_hum"] for r in rows]
    print(f"  max_hum vs dnf_rate:   {spearman(hums, [r['dnf_rate'] for r in rows]):+.3f}")
    print(f"  max_hum vs brier:      {spearman(hums, [r['brier'] for r in rows]):+.3f}")
    print(f"  max_hum vs churn:      {spearman([r['max_hum'] for r in churn_rows], [r['churn'] for r in churn_rows]):+.3f}")
    print()
    quartile_report("humidity", rows, "max_hum", ["dnf_rate", "brier"])

    if ens_rows:
        print("=== 3. Cross-model disagreement vs the algo's own Brier error ===")
        print(f"  temp_spread vs brier:  {spearman([r['temp_spread'] for r in ens_rows], [r['brier'] for r in ens_rows]):+.3f}  (n={len(ens_rows)})")
        print(f"  hum_spread vs brier:   {spearman([r['hum_spread'] for r in ens_rows], [r['brier'] for r in ens_rows]):+.3f}  (n={len(ens_rows)})")
        agree = [r for r in ens_rows if r["temp_spread"] < statistics.median(x["temp_spread"] for x in ens_rows)]
        disagree = [r for r in ens_rows if r not in agree]
        print(f"  temp: agree-bucket mean brier {statistics.mean(r['brier'] for r in agree):.3f}  "
              f"vs disagree-bucket {statistics.mean(r['brier'] for r in disagree):.3f}")
    else:
        print("=== 3. skipped -- no races with 4-model temp/humidity coverage ===")


if __name__ == "__main__":
    sys.exit(main())
