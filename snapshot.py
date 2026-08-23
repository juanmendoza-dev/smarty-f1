#!/usr/bin/env python3
"""Build one immutable race snapshot. 01-data-pipeline.md sec8.3.

    (season, round) -> data/snapshots/{season}-{round}-race-{ISO8601 UTC}.json

Pulls grid, form, track history, weather, and both markets exactly once each,
with every response cached to disk and every source's URL/status/timestamp
recorded in the snapshot's provenance block. Writes one new file per run and
never overwrites an existing one -- snapshots are the A3 training set and the
A2 audit trail (01-data-pipeline.md sec8.3).

This script never computes a probability. That's score.py's job, reading only
this file.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import jolpica, openmeteo, polymarket, kalshi, driver_map, httpcache
from lib.circuits import multiplier_for
from lib.features import is_classified

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, "data", "cache")
DEFAULT_OUT_DIR = os.path.join(REPO_ROOT, "data", "snapshots")

# Circuit timezones, hand-maintained like the overtaking multiplier table
# (lib/circuits.py) -- defaulting to UTC here would silently reproduce exactly
# the bug 01-data-pipeline.md sec5.4 warns about, so an unknown circuit raises
# instead of guessing.
CIRCUIT_TIMEZONE = {
    "zandvoort": "Europe/Amsterdam",
}


def git_commit(cwd):
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=cwd, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return None


def cast_result_row(r):
    return {
        "code": r["Driver"]["code"],
        "driver_id": r["Driver"]["driverId"],
        "given_name": r["Driver"]["givenName"],
        "family_name": r["Driver"]["familyName"],
        "constructor_id": r["Constructor"]["constructorId"],
        "position": int(r["position"]),
        "status": r.get("status", "Finished"),
        "points": float(r["points"]),
        # 04-outcome-expansion-algo.md sec8.1: nullable -- Jolpica has an
        # ingest lag on this field right after a race finishes (verified live,
        # sec7.4), so a None here can mean "not this driver" or "not ingested
        # yet for anyone." Callers that need to tell those apart (postrace.py)
        # check across the whole field, not per-row.
        "fastest_lap_rank": (r.get("FastestLap") or {}).get("rank"),
    }


def build_grid(season, round_, cache_dir):
    quali_rows, quali_meta = jolpica.qualifying(season, round_, cache_dir)
    if not quali_rows:
        raise SystemExit(f"qualifying for {season}/{round_} hasn't happened yet -- nothing to snapshot")

    sprint_rows, sprint_meta = jolpica.sprint(season, round_, cache_dir)
    sprint_by_code = {r["Driver"]["code"]: r for r in sprint_rows}
    is_sprint_weekend = len(sprint_rows) > 0

    grid = []
    for r in quali_rows:
        code = r["Driver"]["code"]
        entry = {
            "code": code,
            "driver_id": r["Driver"]["driverId"],
            "given_name": r["Driver"]["givenName"],
            "family_name": r["Driver"]["familyName"],
            "constructor_id": r["Constructor"]["constructorId"],
            "quali_position": int(r["position"]),
            "q1": r.get("Q1"), "q2": r.get("Q2"), "q3": r.get("Q3"),
        }
        if code in sprint_by_code:
            sr = sprint_by_code[code]
            status = sr.get("status", "Finished")
            entry["sprint"] = {
                "position": int(sr["position"]),
                "status": status,
                "classified": is_classified(status),
            }
        else:
            entry["sprint"] = None
        grid.append(entry)

    provenance = [quali_meta, sprint_meta]
    return grid, is_sprint_weekend, provenance


def build_form(season, round_, grid, cache_dir):
    """Form ingredients for F2 (team), F4 (driver), and F8 (teammate H2H).

    Race-classification results only (sprint excluded, sec4 F2/F4) for every
    completed round before this one -- F8 needs the full 1..round-1 range,
    F2/F4 only need the last 5 (recent_rounds is a subset of all_rounds).
    Stored raw so score.py can apply pos_score/shrinkage/normalization itself
    without ever calling the network (01-data-pipeline.md sec1).
    """
    all_rounds = list(range(1, round_))
    recent_rounds = all_rounds[-5:]

    provenance = []
    per_round_results = {}
    for rnd in all_rounds:
        rows, meta = jolpica.race_results(season, rnd, cache_dir)
        provenance.append(meta)
        per_round_results[rnd] = [cast_result_row(r) for r in rows]

    driver_standings, ds_meta = jolpica.driver_standings(season, cache_dir, round_=None)
    provenance.append(ds_meta)
    standings = [
        {
            "code": s["Driver"]["code"],
            "position": int(s["position"]),
            "points": float(s["points"]),
            "constructor_id": s["Constructors"][0]["constructorId"] if s.get("Constructors") else None,
        }
        for s in driver_standings
    ]

    return {
        "all_rounds": all_rounds,
        "recent_rounds": recent_rounds,
        "results_by_round": per_round_results,
        "driver_standings": standings,
    }, provenance


def build_track_history(circuit_id, grid, race_date, cache_dir):
    provenance = []
    per_driver_all = {}
    for entry in grid:
        races, meta = jolpica.driver_track_history(circuit_id, entry["driver_id"], cache_dir)
        provenance.append(meta)
        rows = []
        for race in races:
            res = race["Results"][0]
            status = res.get("status", "Finished")
            rows.append({
                "season": int(race["season"]),
                "date": race["date"],
                "time": race.get("time"),
                "position": int(res["position"]),
                "status": status,
                "classified": is_classified(status),
            })
        per_driver_all[entry["code"]] = rows

    all_seasons = sorted({row["season"] for rows in per_driver_all.values() for row in rows}, reverse=True)
    target_seasons = all_seasons[:3]

    tz_name = CIRCUIT_TIMEZONE[circuit_id]
    tz = ZoneInfo(tz_name)

    edition_datetime = {}
    for rows in per_driver_all.values():
        for row in rows:
            if row["season"] in target_seasons and row["season"] not in edition_datetime:
                edition_datetime[row["season"]] = (row["date"], row["time"])

    editions_weather = {}
    for season_yr, (date_str, time_str) in edition_datetime.items():
        start_utc = datetime.fromisoformat(f"{date_str}T{time_str.replace('Z', '+00:00')}")
        start_local = start_utc.astimezone(tz)
        window_start = start_local - timedelta(hours=2)
        window_end = start_local + timedelta(hours=2)
        body, meta = openmeteo.archive(52.3888, 4.54092, date_str, date_str, tz_name, cache_dir)
        provenance.append(meta)
        hourly = body["hourly"]
        window_vals = []
        for t_str, precip in zip(hourly["time"], hourly["precipitation"]):
            t = datetime.fromisoformat(t_str).replace(tzinfo=tz)
            if window_start <= t <= window_end:
                window_vals.append(precip)
        max_precip = max(window_vals) if window_vals else 0.0
        editions_weather[str(season_yr)] = {
            "date": date_str,
            "race_window_precip_max_mm": max_precip,
            "wet": max_precip > 0.0,
        }

    recency_weights = [1.0, 0.7, 0.5]
    by_driver = {}
    for code, rows in per_driver_all.items():
        filtered = [r for r in rows if r["season"] in target_seasons]
        filtered.sort(key=lambda r: r["season"], reverse=True)
        for i, r in enumerate(filtered):
            r["wet"] = editions_weather[str(r["season"])]["wet"]
            # Baked in now, by rank among this driver's *own* up-to-3 appearances,
            # so a later filter (e.g. F7's wet-only subset) can't silently
            # re-rank an older edition into a newer one's weight slot.
            r["recency_weight"] = recency_weights[i]
        by_driver[code] = filtered

    return {
        "circuit_id": circuit_id,
        "editions_considered": target_seasons,
        "editions_weather": editions_weather,
        "by_driver": by_driver,
    }, provenance


def build_weather(lat, lon, race_date_str, race_time_str, circuit_id, cache_dir, force_refresh=False):
    tz_name = CIRCUIT_TIMEZONE[circuit_id]
    tz = ZoneInfo(tz_name)
    body, meta = openmeteo.forecast(lat, lon, race_date_str, tz_name, cache_dir, force_refresh=force_refresh)
    hourly = body["hourly"]

    start_utc = datetime.fromisoformat(f"{race_date_str}T{race_time_str.replace('Z', '+00:00')}")
    start_local = start_utc.astimezone(tz)
    window_start = start_local - timedelta(hours=2)
    window_end = start_local + timedelta(hours=2)

    window_rows = []
    for i, t_str in enumerate(hourly["time"]):
        t = datetime.fromisoformat(t_str).replace(tzinfo=tz)
        if window_start <= t <= window_end:
            window_rows.append({
                "local_time": t_str,
                "temperature_2m": hourly["temperature_2m"][i],
                "precipitation_probability": hourly["precipitation_probability"][i],
                "precipitation": hourly["precipitation"][i],
                "wind_speed_10m": hourly["wind_speed_10m"][i],
                "relative_humidity_2m": hourly["relative_humidity_2m"][i],
            })

    p_max = max(r["precipitation_probability"] for r in window_rows) if window_rows else None

    return {
        "race_window_local": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "hourly_window": window_rows,
        "p_max": p_max,
        "hourly_units": body.get("hourly_units"),
    }, [meta]


def _pull_polymarket_market(slug, race_date_str, cache_dir, force_refresh, k=1,
                             fallback_title_contains=None):
    """One Polymarket market -> (block, meta). Raises polymarket.StaleMarketError
    on anything unresolvable; caller decides whether that's fatal (winner,
    build_markets) or soft (podium/points/fastest-lap, build_extended_markets --
    04-outcome-expansion-algo.md sec8.2).
    """
    event, meta, markets = polymarket.resolve_event(
        slug, race_date_str, cache_dir, fallback_title_contains=fallback_title_contains,
        force_refresh=force_refresh, k=k,
    )
    overround, normalized = polymarket.normalize(markets, k=k)
    by_code = {}
    other = []
    for m in markets:
        if m["code"] is None:
            other.append({"name": m["name"], "active": m["active"], "mid": m["mid"]})
            continue
        by_code[m["code"]] = {
            "raw": {
                "best_bid": m["best_bid"], "best_ask": m["best_ask"],
                "outcome_prices": m["outcome_prices_raw"], "last_trade_price": m["last_trade_price"],
                "mid": m["mid"], "active": m["active"], "volume": m["volume"],
            },
            "normalized": normalized.get(m["code"]),
        }
    block = {
        "event_id": event.get("id"), "slug": event.get("slug"),
        "overround": overround, "by_code": by_code, "excluded_outcomes": other,
    }
    return block, meta


def _pull_kalshi_market(series_ticker, event_ticker, race_date_str, cache_dir, force_refresh, k=1):
    """One Kalshi market -> (block, [meta, meta]). Raises kalshi.StaleMarketError
    on anything unresolvable; same soft/hard-fail split as the Polymarket helper.
    """
    markets, events_meta, markets_meta = kalshi.resolve_markets(
        series_ticker, event_ticker, race_date_str, cache_dir, force_refresh=force_refresh, k=k,
    )
    overround, normalized = kalshi.normalize(markets, k=k)
    by_code = {
        m["code"]: {
            "raw": {
                "yes_bid": m["yes_bid"], "yes_ask": m["yes_ask"], "last_price": m["last_price"],
                "mid": m["mid"], "volume": m["volume"], "open_interest": m["open_interest"],
                "no_sub_title": m["no_sub_title"],
            },
            "normalized": normalized.get(m["code"]),
        }
        for m in markets
    }
    block = {"event_ticker": event_ticker, "overround": overround, "by_code": by_code}
    return block, [events_meta, markets_meta]


def _market_mean(by_code_blocks):
    """by_code_blocks: list of {code: {"normalized": float|None, ...}} dicts,
    one per venue that has a market for this outcome. Skips venues that don't
    (e.g. points has no Polymarket entry -- 04 sec2/sec6.4)."""
    all_codes = set()
    for bc in by_code_blocks:
        all_codes |= set(bc)
    market_mean = {}
    for code in all_codes:
        vals = [bc[code]["normalized"] for bc in by_code_blocks
                if code in bc and bc[code]["normalized"] is not None]
        if vals:
            market_mean[code] = sum(vals) / len(vals)
    return market_mean


def build_markets(polymarket_slug, race_date_str, kalshi_series, kalshi_event_ticker, cache_dir,
                   force_refresh=False):
    """Winner market only. Hard-fails on anything unresolvable -- unchanged
    01-data-pipeline.md/02-winner-prediction-algo.md behavior."""
    provenance = []

    pm_block, pm_meta = _pull_polymarket_market(
        polymarket_slug, race_date_str, cache_dir, force_refresh,
        fallback_title_contains="Dutch Grand Prix Winner",
    )
    provenance.append(pm_meta)

    kx_block, kx_metas = _pull_kalshi_market(
        kalshi_series, kalshi_event_ticker, race_date_str, cache_dir, force_refresh,
    )
    provenance.extend(kx_metas)

    market_mean = _market_mean([pm_block["by_code"], kx_block["by_code"]])

    return {
        "polymarket": pm_block,
        "kalshi": kx_block,
        "market_mean": market_mean,
    }, provenance


def build_extended_markets(race_date_str, cache_dir, force_refresh,
                            polymarket_podium_slug, polymarket_fastestlap_slug,
                            kalshi_podium_ticker, kalshi_top10_ticker, kalshi_fastlap_ticker):
    """Podium/points/fastest-lap markets. 04-outcome-expansion-algo.md sec8.2:
    additive to build_markets(), never touches the winner block, and every
    pull here is soft-fail per venue -- a market not open yet (verified live,
    04 sec2: Kalshi opens race markets later than Polymarket does) or
    otherwise unresolvable must not block a snapshot that would otherwise
    succeed. Points has no Polymarket entry (04 sec2 -- no such market exists).
    """
    provenance = []

    def soft_pull(fn, *args, **kwargs):
        try:
            block, meta = fn(*args, **kwargs)
            if isinstance(meta, list):
                provenance.extend(meta)
            else:
                provenance.append(meta)
            return block
        except (polymarket.StaleMarketError, kalshi.StaleMarketError, httpcache.HttpError) as e:
            return {"status": "unavailable", "reason": str(e)}

    podium_pm = soft_pull(_pull_polymarket_market, polymarket_podium_slug, race_date_str,
                          cache_dir, force_refresh, 3)
    podium_kx = soft_pull(_pull_kalshi_market, "KXF1RACEPODIUM", kalshi_podium_ticker,
                          race_date_str, cache_dir, force_refresh, 3)
    podium = {
        "polymarket": podium_pm, "kalshi": podium_kx,
        "market_mean": _market_mean([
            podium_pm.get("by_code", {}), podium_kx.get("by_code", {}),
        ]),
    }

    points_kx = soft_pull(_pull_kalshi_market, "KXF1TOP10", kalshi_top10_ticker,
                          race_date_str, cache_dir, force_refresh, 10)
    points = {
        "kalshi": points_kx,
        "market_mean": _market_mean([points_kx.get("by_code", {})]),
    }

    fastlap_pm = soft_pull(_pull_polymarket_market, polymarket_fastestlap_slug, race_date_str,
                           cache_dir, force_refresh, 1)
    fastlap_kx = soft_pull(_pull_kalshi_market, "KXF1FASTLAP", kalshi_fastlap_ticker,
                           race_date_str, cache_dir, force_refresh, 1)
    fastest_lap = {
        "polymarket": fastlap_pm, "kalshi": fastlap_kx,
        "market_mean": _market_mean([
            fastlap_pm.get("by_code", {}), fastlap_kx.get("by_code", {}),
        ]),
    }

    return {"podium": podium, "points": points, "fastest_lap": fastest_lap}, provenance


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--round", type=int, default=12)
    ap.add_argument("--polymarket-slug", default="f1-dutch-grand-prix-winner-2026-08-23")
    ap.add_argument("--kalshi-series", default="KXF1RACE")
    ap.add_argument("--kalshi-event-ticker", default="KXF1RACE-DUTGP26")
    # 04-outcome-expansion-algo.md sec8.3: per-outcome overrides, race-specific
    # like the winner-market args above, not derived. Defaults point at the
    # Dutch GP -- override all five for a different race.
    ap.add_argument("--polymarket-podium-slug", default="f1-dutch-grand-prix-driver-podium-2026-08-23")
    ap.add_argument("--polymarket-fastestlap-slug",
                     default="f1-dutch-grand-prix-driver-fastest-lap-2026-08-23")
    ap.add_argument("--kalshi-podium-ticker", default="KXF1RACEPODIUM-DUTGP26")
    ap.add_argument("--kalshi-top10-ticker", default="KXF1TOP10-DUTGP26")
    ap.add_argument("--kalshi-fastlap-ticker", default="KXF1FASTLAP-DUTGP26")
    ap.add_argument("--skip-extended-markets", action="store_true",
                     help="skip the podium/points/fastest-lap market pulls entirely (grid/form/"
                          "track-history/weather/winner-market snapshot is unaffected either way).")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--force-refresh", action="store_true",
                     help="bypass the disk cache for markets + weather forecast (grid/form/track "
                          "history are untouched -- they can't change once qualifying's happened). "
                          "Use this for a re-snapshot close to lights-out; without it, a second run "
                          "silently replays whatever odds were cached on the first run.")
    args = ap.parse_args()

    cache_dir = args.cache_dir
    provenance = {}

    race_info, race_meta = jolpica.race_info(args.season, args.round, cache_dir)
    provenance["schedule"] = race_meta
    circuit_id = race_info["Circuit"]["circuitId"]
    circuit_row, circuit_meta = jolpica.circuit(circuit_id, cache_dir)
    provenance["circuit"] = circuit_meta
    lat = float(circuit_row["Location"]["lat"])
    lon = float(circuit_row["Location"]["long"])
    race_date = race_info["date"]
    race_time = race_info["time"]

    grid, is_sprint_weekend, grid_prov = build_grid(args.season, args.round, cache_dir)
    provenance["grid"] = grid_prov

    form, form_prov = build_form(args.season, args.round, grid, cache_dir)
    provenance["form"] = form_prov

    track_history, th_prov = build_track_history(circuit_id, grid, race_date, cache_dir)
    provenance["track_history"] = th_prov

    weather, weather_prov = build_weather(
        lat, lon, race_date, race_time, circuit_id, cache_dir, force_refresh=args.force_refresh
    )
    provenance["weather"] = weather_prov

    markets, markets_prov = build_markets(
        args.polymarket_slug, race_date, args.kalshi_series, args.kalshi_event_ticker, cache_dir,
        force_refresh=args.force_refresh,
    )
    provenance["markets"] = markets_prov

    if args.skip_extended_markets:
        markets["podium"] = {"status": "skipped"}
        markets["points"] = {"status": "skipped"}
        markets["fastest_lap"] = {"status": "skipped"}
    else:
        extended_markets, extended_prov = build_extended_markets(
            race_date, cache_dir, args.force_refresh,
            args.polymarket_podium_slug, args.polymarket_fastestlap_slug,
            args.kalshi_podium_ticker, args.kalshi_top10_ticker, args.kalshi_fastlap_ticker,
        )
        markets.update(extended_markets)
        provenance["extended_markets"] = extended_prov

    provenance["fastf1"] = {"status": "unavailable", "reason": "python<3.10"}

    snapshot_ts = datetime.now(timezone.utc)
    snapshot = {
        "meta": {
            "season": args.season,
            "round": args.round,
            "race_name": race_info["raceName"],
            "circuit_id": circuit_id,
            "circuit_lat": lat,
            "circuit_lon": lon,
            "race_start_utc": f"{race_date}T{race_time}",
            "race_start_local": datetime.fromisoformat(f"{race_date}T{race_time.replace('Z','+00:00')}")
                .astimezone(ZoneInfo(CIRCUIT_TIMEZONE[circuit_id])).isoformat(),
            "is_sprint_weekend": is_sprint_weekend,
            "track_overtaking_multiplier": multiplier_for(circuit_id),
            "snapshot_timestamp_utc": snapshot_ts.isoformat(),
            "git_commit": git_commit(REPO_ROOT),
        },
        "provenance": provenance,
        "grid": grid,
        "form": form,
        "track_history": track_history,
        "weather": weather,
        "markets": markets,
    }

    os.makedirs(args.out_dir, exist_ok=True)
    fname = f"{args.season}-{args.round}-race-{snapshot_ts.strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path = os.path.join(args.out_dir, fname)
    if os.path.exists(out_path):
        raise SystemExit(f"refusing to overwrite existing snapshot {out_path}")
    with open(out_path, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)

    print(f"wrote {out_path}")
    print(f"grid: {len(grid)} drivers, sprint_weekend={is_sprint_weekend}")
    print(f"weather P_max={weather['p_max']}% (dormant if <40)")
    print(f"polymarket overround={markets['polymarket']['overround']:.4f}, "
          f"kalshi overround={markets['kalshi']['overround']:.4f}")

    def venue_status(block, venue):
        v = block.get(venue)
        if v is None:
            return "n/a"
        return "ok" if "by_code" in v else v.get("status", "unknown")

    for outcome in ("podium", "points", "fastest_lap"):
        block = markets.get(outcome, {})
        if block.get("status") == "skipped":
            print(f"{outcome}: skipped (--skip-extended-markets)")
        else:
            print(f"{outcome}: polymarket={venue_status(block, 'polymarket')} "
                  f"kalshi={venue_status(block, 'kalshi')}")

    return out_path


if __name__ == "__main__":
    main()
