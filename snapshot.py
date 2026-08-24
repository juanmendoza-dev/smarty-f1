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
from lib.invariants import require

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, "data", "cache")
DEFAULT_OUT_DIR = os.path.join(REPO_ROOT, "data", "snapshots")

# Circuit timezones, hand-maintained like the overtaking multiplier table
# (lib/circuits.py) -- defaulting to UTC here would silently reproduce exactly
# the bug 01-data-pipeline.md sec5.4 warns about, so an unknown circuit raises
# instead of guessing.
#
# Kept in sync with lib/circuits.OVERTAKING_MULTIPLIER: a circuit you can score
# but can't resolve a timezone for is a snapshot that dies partway through, so
# every circuit listed there is listed here (test_config.py asserts it).
# Indexed as a bare dict lookup in build_track_history and build_weather, so a
# missing circuit is a KeyError, not a degraded feature. That is deliberate --
# guessing a timezone silently shifts the race window and corrupts F5's wet
# lookup and F7's forecast alike -- but it means this table has to cover every
# circuit a run will touch. The 2014-2026 A3 backfill corpus is 33 circuits
# (05-trained-model.md sec5.2); all 33 are below, verified to resolve under
# ZoneInfo on 2026-08-24.
CIRCUIT_TIMEZONE = {
    "zandvoort": "Europe/Amsterdam",
    "monza": "Europe/Rome",
    "monaco": "Europe/Monaco",
    "hungaroring": "Europe/Budapest",
    "marina_bay": "Asia/Singapore",
    "imola": "Europe/Rome",
    "silverstone": "Europe/London",
    "suzuka": "Asia/Tokyo",
    "catalunya": "Europe/Madrid",
    "americas": "America/Chicago",
    "albert_park": "Australia/Melbourne",
    "baku": "Asia/Baku",
    "jeddah": "Asia/Riyadh",
    "spa": "Europe/Brussels",
    "interlagos": "America/Sao_Paulo",

    # Added 2026-08-24 for the A3 backfill window. Everything below appears in
    # 2014-2026 but not on the 2026 calendar, except miami/vegas/shanghai/
    # bahrain/yas_marina/red_bull_ring, which are current races that simply had
    # never been snapshotted before.
    "bahrain": "Asia/Bahrain",
    "hockenheimring": "Europe/Berlin",
    "istanbul": "Europe/Istanbul",
    "losail": "Asia/Qatar",
    "madring": "Europe/Madrid",        # Madrid, new for 2026
    "miami": "America/New_York",
    "mugello": "Europe/Rome",
    "nurburgring": "Europe/Berlin",
    "portimao": "Europe/Lisbon",
    "red_bull_ring": "Europe/Vienna",
    "ricard": "Europe/Paris",          # Paul Ricard
    "rodriguez": "America/Mexico_City",
    "sepang": "Asia/Kuala_Lumpur",
    "shanghai": "Asia/Shanghai",
    "sochi": "Europe/Moscow",          # no separate IANA zone for Sochi
    "vegas": "America/Los_Angeles",
    "villeneuve": "America/Toronto",   # Montreal; America/Montreal is a link
    "yas_marina": "Asia/Dubai",
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


def build_form(season, round_, grid, cache_dir, race_has_run=False):
    """Form ingredients for F2 (team), F4 (driver), F6 (championship), F8 (H2H).

    Race-classification results only (sprint excluded, sec4 F2/F4) for every
    completed round before this one -- F8 needs the full 1..round-1 range,
    F2/F4 only need the last 5 (recent_rounds is a subset of all_rounds).
    Stored raw so score.py can apply pos_score/shrinkage/normalization itself
    without ever calling the network (01-data-pipeline.md sec1).

    race_has_run selects how F6's standings are sourced, and the two modes are
    not interchangeable -- see the comment on the standings pull below.
    """
    all_rounds = list(range(1, round_))
    recent_rounds = all_rounds[-5:]

    # One bulk season-level pull instead of a loop over jolpica.race_results()
    # per prior round -- 01-data-pipeline.md sec4.3's "one filtered query over
    # N per-entity queries" advice, previously not followed here. Round 1 has
    # no prior rounds and no results to fetch.
    if all_rounds:
        results_by_round, provenance = jolpica.season_results(season, cache_dir)
    else:
        results_by_round, provenance = {}, []
    per_round_results = {
        rnd: [cast_result_row(r) for r in results_by_round.get(rnd, [])]
        for rnd in all_rounds
    }
    # The old per-round race_results loop made a missing round visible as an
    # explicit empty list from that call. results_by_round.get(rnd, []) above
    # would silently do the same for a round season_results() didn't return at
    # all -- F2/F4/F8/F_dnf would just average over fewer races with no error.
    # Every round strictly before round_ has necessarily already happened, so
    # a gap here is a real problem (a cancelled round with no classification,
    # or a season_results bug), not an expected state.
    missing = [rnd for rnd in all_rounds if not per_round_results[rnd]]
    require(
        not missing,
        f"build_form: round(s) {missing} of {season} came back with no results from "
        f"season_results -- either a real gap or a season_results bug, not silently averaged over",
    )

    # F6's standings must contain everything that happened before this race and
    # nothing from the race itself. Which endpoint gets you that depends on
    # whether the race has run, and the two are NOT interchangeable:
    #
    # Live (race_has_run=False): round_=None, the "latest" standings. This is
    #   the only way to capture a sprint that already ran this weekend. Jolpica
    #   stamps that list with round_ itself even when it holds sprint points
    #   only -- verified against the 2026 Dutch GP, where "latest" was stamped
    #   round 12 and sat exactly 8/7/6/5/4/3/2/1 points above the round-11
    #   table for the sprint's top 8. So a stamped round of round_ is expected
    #   here and is not evidence of leakage; anything beyond round_ is.
    #
    # Backfill (race_has_run=True): "latest" is the FINAL table of a finished
    #   season -- F6 (w=0.08) handed the answer. Ask for the prior round
    #   explicitly instead. Known limitation: on a sprint weekend this drops
    #   that weekend's sprint points from F6, because there is no round-indexed
    #   way to ask for "after round N-1's race plus round N's sprint." Bounded
    #   at 8 points against leader totals in the hundreds, on a feature
    #   normalized by leader points -- documented in 01-data-pipeline.md sec4.6
    #   rather than papered over with a per-season sprint-scoring table.
    #
    # Round 1 has no prior round and no standings; F6 scores everyone 0.0 off
    # an empty list.
    prior_round = round_ - 1
    if prior_round < 1:
        driver_standings, standings_after_round = [], None
    elif race_has_run:
        driver_standings, ds_meta = jolpica.driver_standings(
            season, cache_dir, round_=prior_round, verify_round=prior_round
        )
        provenance.append(ds_meta)
        standings_after_round = prior_round
    else:
        driver_standings, ds_meta = jolpica.driver_standings(
            season, cache_dir, round_=None, max_round=round_
        )
        provenance.append(ds_meta)
        standings_after_round = ds_meta.get("standings_round")
    # "position" is genuinely absent -- not empty, absent -- for every driver
    # tied on zero points, who get positionText "-" instead. Jolpica declines
    # to rank a block of drivers it can't separate, which is most of the field
    # after round 1 of any season: verified live on 2015 R1, where 7 of 18
    # drivers have no position key. Nothing scores off this field (F6 divides
    # points by leader points, score.compute_champ:179), so it stays as
    # diagnostic metadata and goes None rather than inventing a rank.
    #
    # This is not backfill-specific and predates A3 -- a live snapshot taken at
    # round 2 of a season would have hit it just the same. It had never fired
    # because every prior run was mid-season 2026 or the 2023 Dutch GP (R13).
    standings = [
        {
            "code": s["Driver"]["code"],
            "position": int(s["position"]) if "position" in s else None,
            "position_text": s.get("positionText"),
            "points": float(s["points"]),
            "constructor_id": s["Constructors"][0]["constructorId"] if s.get("Constructors") else None,
        }
        for s in driver_standings
    ]

    require(
        all(r < round_ for r in all_rounds),
        f"form leakage: all_rounds={all_rounds} includes the round being predicted ({round_})",
    )

    return {
        "all_rounds": all_rounds,
        "recent_rounds": recent_rounds,
        "results_by_round": per_round_results,
        "driver_standings": standings,
        "standings_after_round": standings_after_round,
    }, provenance


def build_track_history(circuit_id, lat, lon, grid, race_date, cache_dir):
    provenance = []
    per_driver_all = {}
    for entry in grid:
        races, meta = jolpica.driver_track_history(circuit_id, entry["driver_id"], cache_dir)
        provenance.append(meta)
        rows = []
        for race in races:
            # Jolpica returns a driver's ENTIRE history at this circuit, past and
            # future relative to race_date. Filtering here (not on season) is the
            # only leakage guard in this function: a same-season backfill has to
            # drop the target race itself, whose date is exactly race_date, and a
            # season-level filter would keep it. ISO-8601 dates compare correctly
            # as strings.
            if race["date"] >= race_date:
                continue
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
        body, meta = openmeteo.archive(lat, lon, date_str, date_str, tz_name, cache_dir)
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

    for code, rows in by_driver.items():
        for r in rows:
            require(
                r["date"] < race_date,
                f"track-history leakage: {code} has a {circuit_id} row dated {r['date']}, "
                f"which is not strictly before the race being predicted ({race_date})",
            )

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

    A null event_ticker means the race config hasn't been given one yet, which
    for the extended markets is the ordinary "Kalshi hasn't opened this event"
    case -- raise the same error type so soft_pull records it as unavailable
    instead of building a nonsense URL out of the string "None".
    """
    if not event_ticker:
        raise kalshi.StaleMarketError(
            f"no Kalshi event ticker configured for series {series_ticker!r} "
            f"-- not open yet, or missing from the race config"
        )
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
                   force_refresh=False, fallback_title_contains=None):
    """Winner market only. Hard-fails on anything unresolvable -- unchanged
    01-data-pipeline.md/02-winner-prediction-algo.md behavior.

    fallback_title_contains comes from the race config; it used to be the
    hardcoded string "Dutch Grand Prix Winner", which silently made the
    slug-lookup fallback useless for every other race.
    """
    provenance = []

    pm_block, pm_meta = _pull_polymarket_market(
        polymarket_slug, race_date_str, cache_dir, force_refresh,
        fallback_title_contains=fallback_title_contains,
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


# Per-race identifiers (market slugs/tickers) live in races/*.json, not in
# argparse defaults. They are pure per-race data with no sane default -- the old
# defaults pointed at the Dutch GP, so every other race meant remembering to
# override seven flags and silently snapshotting Dutch odds if you forgot.
RACE_CONFIG_DIR = os.path.join(REPO_ROOT, "races")

RACE_CONFIG_FIELDS = (
    "season", "round",
    "polymarket_slug", "polymarket_fallback_title",
    "polymarket_podium_slug", "polymarket_fastestlap_slug",
    "kalshi_series", "kalshi_event_ticker",
    "kalshi_podium_ticker", "kalshi_top10_ticker", "kalshi_fastlap_ticker",
)

# Without these a snapshot cannot be built at all. The extended-market fields
# are allowed to be null: those pulls soft-fail per venue by design (04 sec8.2).
RACE_CONFIG_REQUIRED = ("season", "round", "polymarket_slug", "kalshi_series",
                         "kalshi_event_ticker")


def load_race_config(path):
    """Read one races/*.json and return it as a plain dict of known fields."""
    if not os.path.exists(path):
        available = sorted(glob.glob(os.path.join(RACE_CONFIG_DIR, "*.json")))
        raise SystemExit(
            f"no race config at {path}\navailable: "
            + (", ".join(os.path.basename(a) for a in available) or "(none)")
        )
    with open(path) as f:
        raw = json.load(f)
    unknown = set(raw) - set(RACE_CONFIG_FIELDS) - {"_comment"}
    if unknown:
        raise SystemExit(f"{path}: unknown field(s) {sorted(unknown)}")
    return {k: raw.get(k) for k in RACE_CONFIG_FIELDS}


def resolve_race_config(args):
    """Merge --race config with explicit CLI flags. Flags win, so a config can
    be used as a base and one value overridden for a single run."""
    cfg = load_race_config(args.race) if args.race else {k: None for k in RACE_CONFIG_FIELDS}
    for field in RACE_CONFIG_FIELDS:
        override = getattr(args, field, None)
        if override is not None:
            cfg[field] = override

    missing = [f for f in RACE_CONFIG_REQUIRED if cfg.get(f) is None]
    if missing:
        hint = ""
        if "kalshi_event_ticker" in missing:
            hint = (
                "\n\nKalshi opens its race events later than Polymarket does "
                "(04-outcome-expansion-algo.md sec2), so this is often just 'not yet'."
                "\nList what's currently open with:"
                "\n  python3 -c \"import sys;sys.path.insert(0,'.');"
                "from lib import kalshi;from snapshot import DEFAULT_CACHE_DIR as C;"
                "print([e['event_ticker'] for e in kalshi.discover_event_ticker("
                "'KXF1RACE',C,force_refresh=True)[0]])\""
            )
        raise SystemExit(
            f"race config is missing required field(s): {missing}"
            f"\nset them in {args.race or 'a races/*.json passed via --race'} "
            f"or pass the matching --flag{hint}"
        )
    return cfg


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--race", help="path to a races/*.json holding this race's season/round and "
                                    "market identifiers, e.g. races/2026-monza.json")
    # Every flag below overrides the corresponding --race field for one run and
    # defaults to None so 'unset' is distinguishable from 'set to the Dutch GP'.
    ap.add_argument("--season", type=int)
    ap.add_argument("--round", type=int)
    ap.add_argument("--polymarket-slug")
    ap.add_argument("--polymarket-fallback-title")
    ap.add_argument("--kalshi-series")
    ap.add_argument("--kalshi-event-ticker")
    ap.add_argument("--polymarket-podium-slug")
    ap.add_argument("--polymarket-fastestlap-slug")
    ap.add_argument("--kalshi-podium-ticker")
    ap.add_argument("--kalshi-top10-ticker")
    ap.add_argument("--kalshi-fastlap-ticker")
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
    cfg = resolve_race_config(args)
    season, round_ = cfg["season"], cfg["round"]

    cache_dir = args.cache_dir
    provenance = {}

    race_info, race_meta = jolpica.race_info(season, round_, cache_dir)
    provenance["schedule"] = race_meta
    circuit_id = race_info["Circuit"]["circuitId"]
    circuit_row, circuit_meta = jolpica.circuit(circuit_id, cache_dir)
    provenance["circuit"] = circuit_meta
    lat = float(circuit_row["Location"]["lat"])
    lon = float(circuit_row["Location"]["long"])
    race_date = race_info["date"]
    race_time = race_info["time"]

    grid, is_sprint_weekend, grid_prov = build_grid(season, round_, cache_dir)
    provenance["grid"] = grid_prov

    # A past race is a backfill, which changes how F6's standings are sourced
    # (see build_form). Race day itself counts as live: a pre-race snapshot on
    # race morning must still see the sprint, and you do not snapshot after
    # lights-out.
    race_has_run = race_date < datetime.now(timezone.utc).date().isoformat()
    form, form_prov = build_form(season, round_, grid, cache_dir,
                                  race_has_run=race_has_run)
    provenance["form"] = form_prov

    track_history, th_prov = build_track_history(circuit_id, lat, lon, grid, race_date, cache_dir)
    provenance["track_history"] = th_prov

    weather, weather_prov = build_weather(
        lat, lon, race_date, race_time, circuit_id, cache_dir, force_refresh=args.force_refresh
    )
    provenance["weather"] = weather_prov

    markets, markets_prov = build_markets(
        cfg["polymarket_slug"], race_date, cfg["kalshi_series"], cfg["kalshi_event_ticker"],
        cache_dir, force_refresh=args.force_refresh,
        fallback_title_contains=cfg["polymarket_fallback_title"],
    )
    provenance["markets"] = markets_prov

    if args.skip_extended_markets:
        markets["podium"] = {"status": "skipped"}
        markets["points"] = {"status": "skipped"}
        markets["fastest_lap"] = {"status": "skipped"}
    else:
        extended_markets, extended_prov = build_extended_markets(
            race_date, cache_dir, args.force_refresh,
            cfg["polymarket_podium_slug"], cfg["polymarket_fastestlap_slug"],
            cfg["kalshi_podium_ticker"], cfg["kalshi_top10_ticker"], cfg["kalshi_fastlap_ticker"],
        )
        markets.update(extended_markets)
        provenance["extended_markets"] = extended_prov

    provenance["fastf1"] = {"status": "unavailable", "reason": "python<3.10"}

    snapshot_ts = datetime.now(timezone.utc)
    snapshot = {
        "meta": {
            "season": season,
            "round": round_,
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
    fname = f"{season}-{round_}-race-{snapshot_ts.strftime('%Y%m%dT%H%M%SZ')}.json"
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
