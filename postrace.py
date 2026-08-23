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
from score import REPO_ROOT, load_latest_snapshot, score_all, compute_comparison, compute_post_race

DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, "data", "cache")


def find_winner(season, round_, cache_dir):
    results, meta = jolpica.race_results(season, round_, cache_dir)
    if not results:
        raise SystemExit(
            f"no race result for {season}/{round_} yet -- Jolpica hasn't ingested it, nothing to score"
        )
    classified = [r for r in results if int(r["position"]) == 1]
    assert len(classified) == 1, (
        f"expected exactly one classified P1 for {season}/{round_}, got {len(classified)}"
    )
    return classified[0]["Driver"]["code"], meta


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

    winner, result_meta = find_winner(season, round_, args.cache_dir)
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
