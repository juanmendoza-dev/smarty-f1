#!/usr/bin/env python3
"""Auto-score a snapshot against the actual race result. 02-winner-prediction-algo.md sec7.

Everything score.py needs for post-race scoring already exists -- it's the
same score_all()/compute_comparison()/compute_post_race() pipeline, just
wired to --winner CODE typed by hand. This script is that same pipeline with
the winner pulled from Jolpica's classification instead: it reads the
snapshot's (season, round), fetches the race result, and takes the classified
P1.

Writes to <snapshot>-postrace.json, not score.py's <snapshot>-score.json --
the owner re-snapshots the markets close to lights-out (sec8.3), and
score.py's file is the committed pre-race audit trail for that snapshot.
Overwriting it post-race would silently drop the "comparison" block, which
sec6 is explicit is the product, not a defect to tune away.

Refuses to run if Jolpica has no result for this round yet (lib/jolpica.py:
race_results() returning [] means the race hasn't happened, or hasn't been
ingested yet) -- fail loudly rather than silently score against nothing
(02-winner-prediction-algo.md sec8).
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import jolpica
from lib.features import is_classified
from lib.invariants import require
from score import (
    REPO_ROOT, load_latest_snapshot, score_all, compute_comparison, compute_post_race,
    compute_dnf, compute_fastest_lap, compute_podium_points,
    compute_comparison_kofn, compute_post_race_kofn,
)

DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, "data", "cache")


class FastestLapNotIngestedError(Exception):
    """04-outcome-expansion-algo.md sec7.4/sec8.1: Jolpica's FastestLap field
    lags a just-finished race. Distinct from SystemExit (no race result at
    all) -- callers can catch this specifically and still score podium/points/
    DNF, which don't depend on this field."""


def find_full_result(season, round_, cache_dir):
    """04-outcome-expansion-algo.md sec8.1: every driver's {code, position,
    status, classified, fastest_lap_rank}, replacing find_winner()'s
    winner-only extraction. Same "no result yet" failure mode as before;
    additionally asserts exactly one classified P1 using the real
    is_classified() rule (04 sec6.4's finding that Jolpica assigns a position
    even to retirees, so position alone was never actually sufficient).
    """
    results, meta = jolpica.race_results(season, round_, cache_dir)
    if not results and meta.get("cached"):
        # The cache can hold an empty result fetched BEFORE the race ran, and
        # that entry never expires -- "no result" is exactly the answer that
        # goes stale. Seen live: 2026/12/results.json was cached at 04:17Z on
        # race day, nine hours before lights out, so every local run since has
        # concluded the Dutch GP was never run. 05-trained-model.md sec5.4's
        # staleness rule doesn't catch it, because it compares at day
        # granularity and the fetch and the race share a date.
        #
        # Re-ask once before believing it. This costs a request only on the
        # path that was about to fail anyway, so it can't be the thing that
        # burns the rate-limit budget -- which rules out the tempting general
        # version of this rule: an empty sprint response for 2014-2018 is
        # correct and permanent (sprints start in 2021), and refetching every
        # empty response would re-fetch ~93 of them on every backfill.
        results, meta = jolpica.race_results(season, round_, cache_dir, force_refresh=True)
    if not results:
        raise SystemExit(
            f"no race result for {season}/{round_} yet -- Jolpica hasn't ingested it, nothing to score"
        )
    rows = []
    for r in results:
        status = r.get("status", "Finished")
        rows.append({
            "code": r["Driver"]["code"],
            "position": int(r["position"]),
            "status": status,
            "classified": is_classified(status),
            "fastest_lap_rank": (r.get("FastestLap") or {}).get("rank"),
        })

    classified_p1 = [row for row in rows if row["classified"] and row["position"] == 1]
    require(len(classified_p1) == 1, (
        f"expected exactly one classified P1 for {season}/{round_}, got {len(classified_p1)}"
    ))

    return rows, meta


def find_winner(season, round_, cache_dir):
    """Winner-only wrapper around find_full_result(), kept so existing callers
    (test_postrace.py) work unchanged and this doesn't need a second network
    call for the same data."""
    rows, meta = find_full_result(season, round_, cache_dir)
    winner = next(row["code"] for row in rows if row["classified"] and row["position"] == 1)
    return winner, meta


def find_fastest_lap(rows):
    """sec7.4/sec8.1: raises FastestLapNotIngestedError if fastest_lap_rank is
    None for every row (the real, verified ingest-lag case -- distinct from
    the normal case where exactly one row has rank == "1" and the rest are
    None because someone else set it)."""
    ranked = [row for row in rows if row["fastest_lap_rank"] is not None]
    if not ranked:
        raise FastestLapNotIngestedError(
            "fastest-lap data not yet ingested by Jolpica for this round (04 sec7.4) -- "
            "every row's FastestLap is null"
        )
    winners = [row["code"] for row in ranked if row["fastest_lap_rank"] == "1"]
    require(len(winners) == 1, f"expected exactly one fastest-lap holder, got {winners}")
    return winners[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshot", nargs="?", default=None,
                     help="path to a snapshot JSON; defaults to the latest in data/snapshots")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    ap.add_argument("--out", default=None,
                     help="path to write the score result JSON; defaults to <snapshot>-postrace.json, "
                          "kept separate from score.py's <snapshot>-score.json so this never "
                          "overwrites the committed pre-race audit trail")
    args = ap.parse_args()

    snapshot_path = args.snapshot or load_latest_snapshot(os.path.join(REPO_ROOT, "data", "snapshots"))
    with open(snapshot_path) as f:
        snapshot = json.load(f)

    season = snapshot["meta"]["season"]
    round_ = snapshot["meta"]["round"]

    rows, result_meta = find_full_result(season, round_, args.cache_dir)
    winner = next(row["code"] for row in rows if row["classified"] and row["position"] == 1)
    print(f"{snapshot['meta']['race_name']} {season} -- actual winner: {winner}")

    algo_snapshot = {k: v for k, v in snapshot.items() if k != "markets"}
    result = score_all(algo_snapshot)
    p_algo = result["p_algo"]

    markets = snapshot["markets"]
    comparison = compute_comparison(p_algo, markets)
    post_race = compute_post_race(p_algo, markets, winner)

    print(f"brier: algo={post_race['brier_algo']:.4f} polymarket={post_race['brier_polymarket']:.4f} "
          f"kalshi={post_race['brier_kalshi']:.4f} market_mean={post_race['brier_market_mean']:.4f}")
    print(f"algo top pick: {post_race['top_pick']} ({'correct' if post_race['top_pick_correct'] else 'incorrect'})")
    print(f"algo beat market_mean on brier: {post_race['beat_market_mean_on_brier']}")

    # ---------- Phase A4: podium, points, DNF, fastest lap ----------
    # 04-outcome-expansion-algo.md sec3: this snapshot's markets.podium/points/
    # fastest_lap may not exist at all (any snapshot taken before this phase,
    # including the committed Dutch GP one) or may be {"status": "unavailable"}
    # (04 sec8.2's soft-fail). compute_post_race_kofn()/compute_comparison_kofn()
    # already treat a missing/unavailable market block as "no data," not an
    # error, so this degrades to outcome-only scoring automatically -- exactly
    # sec3's rule, enforced by the data rather than a special case here.
    podium_outcome = {row["code"]: (1.0 if row["classified"] and row["position"] <= 3 else 0.0) for row in rows}
    points_outcome = {row["code"]: (1.0 if row["classified"] and row["position"] <= 10 else 0.0) for row in rows}
    dnf_outcome = {row["code"]: (0.0 if row["classified"] else 1.0) for row in rows}

    p_dnf, dnf_n, field_dnf_rate = compute_dnf(algo_snapshot)
    fastlap = compute_fastest_lap(algo_snapshot, result["sub_scores"])
    p_fastlap = fastlap["p_fastlap"]
    p_podium, p_points, sim_meta = compute_podium_points(result["raw_scores"], p_algo)

    podium_market = markets.get("podium")
    points_market = markets.get("points")
    fastlap_market = markets.get("fastest_lap")

    podium_post = compute_post_race_kofn(p_podium, podium_outcome, podium_market)
    points_post = compute_post_race_kofn(p_points, points_outcome, points_market)
    dnf_post = compute_post_race_kofn(p_dnf, dnf_outcome, market_block=None)  # 04 sec2: no DNF market

    print(f"\n--- Phase A4 post-race (mean per-driver binary Brier -- not comparable to the "
          f"winner Brier above, 04 sec6.4) ---")
    print(f"podium: algo={podium_post['brier_algo']:.4f}"
          + (f" market_mean={podium_post['brier_market_mean']:.4f}" if "brier_market_mean" in podium_post else " (no market data)"))
    print(f"points: algo={points_post['brier_algo']:.4f}"
          + (f" market_mean={points_post['brier_market_mean']:.4f}" if "brier_market_mean" in points_post else " (no market data)"))
    print(f"dnf:    algo={dnf_post['brier_algo']:.4f} (no market exists, 04 sec2)")

    try:
        fastest_lap_winner = find_fastest_lap(rows)
        print(f"fastest lap: {fastest_lap_winner}")
        fastlap_outcome = {row["code"]: (1.0 if row["code"] == fastest_lap_winner else 0.0) for row in rows}
        fastlap_post = compute_post_race_kofn(p_fastlap, fastlap_outcome, fastlap_market)
        print(f"fastlap: algo={fastlap_post['brier_algo']:.4f}"
              + (f" market_mean={fastlap_post['brier_market_mean']:.4f}" if "brier_market_mean" in fastlap_post else " (no market data)"))
    except FastestLapNotIngestedError as e:
        print(f"fastest lap: {e}")
        fastest_lap_winner = None
        fastlap_post = None

    phase_a4 = {
        "p_dnf": p_dnf, "dnf_n": dnf_n, "field_dnf_rate": field_dnf_rate,
        "fastlap_effective_weights": fastlap["effective_weights"],
        "fastlap_raw_scores": fastlap["raw_scores"], "p_fastlap": p_fastlap,
        "p_podium": p_podium, "p_points": p_points, "sim_meta": sim_meta,
        "podium_outcome": podium_outcome, "points_outcome": points_outcome, "dnf_outcome": dnf_outcome,
        "podium_comparison": compute_comparison_kofn(p_podium, podium_market),
        "points_comparison": compute_comparison_kofn(p_points, points_market),
        "fastlap_comparison": compute_comparison_kofn(p_fastlap, fastlap_market),
        "podium_post_race": podium_post, "points_post_race": points_post, "dnf_post_race": dnf_post,
        "fastest_lap_winner": fastest_lap_winner, "fastlap_post_race": fastlap_post,
    }

    output = {
        "snapshot_path": snapshot_path,
        "meta": snapshot["meta"],
        "sub_scores": result["sub_scores"],
        "track_n": result["track_n"],
        "effective_weights": result["effective_weights"],
        "raw_scores": result["raw_scores"],
        "p_algo": p_algo,
        "weather_dormant": result["weather_dormant"],
        "p_max": result["p_max"],
        "phase_a4": phase_a4,
        "comparison": comparison,
        "post_race": post_race,
        "result_provenance": result_meta,
    }

    out_path = args.out or (os.path.splitext(snapshot_path)[0] + "-postrace.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
