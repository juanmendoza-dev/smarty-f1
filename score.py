#!/usr/bin/env python3
"""Score a snapshot. 02-winner-prediction-algo.md.

Reads one snapshot JSON and nothing else -- no network calls. Computes the
eight feature sub-scores, applies the locked weights and track flex, runs
softmax(T), and only *after* that emits the algo-vs-market comparison.

Market-blindness (sec1, sec8 assertion 6) is enforced structurally: score_all()
is handed a copy of the snapshot with the "markets" key removed, so there is no
way for a market field to leak into the scoring path even by accident.
"""

import argparse
import glob
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.features import NEUTRAL, K_GRID, K_SPRINT, K_FIN, pos_score, shrink_by_n, field_normalize

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
T = 0.1168

BASE_WEIGHTS = {
    "grid": 0.35, "team": 0.15, "sprint": 0.13, "driver_form": 0.11,
    "track": 0.08, "champ": 0.08, "weather": 0.05, "teammate": 0.05,
}
assert abs(sum(BASE_WEIGHTS.values()) - 1.0) < 1e-9, "base weights must sum to 1.0"


# ---------- F1 grid ----------

def compute_grid(algo_snapshot):
    grid = algo_snapshot["grid"]
    out = {}
    for d in grid:
        p = d["quali_position"]
        if p is None:
            out[d["code"]] = 0.0
        else:
            out[d["code"]] = pos_score(p, K_GRID)
    return out


# ---------- F2 team / car form (race points only, last 5 completed rounds) ----------

def compute_team_form(algo_snapshot):
    recent_rounds = algo_snapshot["form"]["recent_rounds"]
    per_round = algo_snapshot["form"]["results_by_round"]
    constructor_points = defaultdict(float)
    for rnd in recent_rounds:
        rows = per_round[str(rnd)]
        for row in rows:
            constructor_points[row["constructor_id"]] += row["points"]
    grid = algo_snapshot["grid"]
    raw_by_code = {d["code"]: constructor_points.get(d["constructor_id"], 0.0) for d in grid}
    return field_normalize(raw_by_code)


# ---------- F3 sprint ----------

def compute_sprint(algo_snapshot):
    grid = algo_snapshot["grid"]
    out = {}
    for d in grid:
        sprint = d.get("sprint")
        if sprint is None:
            out[d["code"]] = NEUTRAL
        elif sprint["classified"]:
            out[d["code"]] = pos_score(sprint["position"], K_SPRINT)
        else:
            out[d["code"]] = 0.0
    return out


# ---------- F4 driver recent form (race-only, last 5 completed rounds) ----------

def compute_driver_form(algo_snapshot):
    recent_rounds = algo_snapshot["form"]["recent_rounds"]
    per_round = algo_snapshot["form"]["results_by_round"]
    by_driver_rounds = defaultdict(list)
    for rnd in recent_rounds:
        for row in per_round[str(rnd)]:
            by_driver_rounds[row["code"]].append(row)

    grid = algo_snapshot["grid"]
    raw_by_code = {}
    for d in grid:
        rows = by_driver_rounds.get(d["code"], [])
        if not rows:
            raw_by_code[d["code"]] = NEUTRAL
            continue
        scores = []
        for row in rows:
            classified = row["status"] == "Finished" or row["status"].startswith("+")
            scores.append(pos_score(row["position"], K_FIN) if classified else 0.0)
        raw_by_code[d["code"]] = sum(scores) / len(scores)
    return field_normalize(raw_by_code)


# ---------- F5 track history ----------

def _weighted_mean(rows):
    """rows: a subset of this driver's track-history rows, each carrying its own
    'recency_weight' baked in at snapshot time (rank among *all* of that
    driver's up-to-3 appearances, not among whatever subset is passed in here).
    Using the stored weight rather than list position matters for F7: filtering
    to wet-only editions can drop the middle entry, and a positional 1.0/0.7/0.5
    zip would then silently promote an older edition into a newer one's slot.
    """
    num, den = 0.0, 0.0
    for row in rows:
        w = row["recency_weight"]
        s = pos_score(row["position"], K_FIN) if row["classified"] else 0.0
        num += w * s
        den += w
    return num / den if den > 0 else None


def compute_track_history(algo_snapshot):
    by_driver = algo_snapshot["track_history"]["by_driver"]
    grid = algo_snapshot["grid"]

    raw_by_code = {}
    n_by_code = {}
    for d in grid:
        rows = by_driver.get(d["code"], [])
        n = len(rows)
        n_by_code[d["code"]] = n
        if n == 0:
            continue
        raw_by_code[d["code"]] = _weighted_mean(rows)

    normalized = field_normalize(raw_by_code)
    out = {}
    for d in grid:
        code = d["code"]
        n = n_by_code[code]
        if n == 0:
            out[code] = NEUTRAL
        else:
            out[code] = shrink_by_n(normalized[code], n)
    return out, n_by_code


# ---------- F6 championship standing ----------

def compute_champ(algo_snapshot):
    standings = algo_snapshot["form"]["driver_standings"]
    by_code = {s["code"]: s["points"] for s in standings}
    leader_points = max(by_code.values()) if by_code else 0.0
    grid = algo_snapshot["grid"]
    out = {}
    for d in grid:
        pts = by_code.get(d["code"])
        out[d["code"]] = (pts / leader_points) if (pts is not None and leader_points > 0) else 0.0
    return out


# ---------- F7 weather ----------

def compute_weather(algo_snapshot):
    p_max = algo_snapshot["weather"]["p_max"]
    grid = algo_snapshot["grid"]
    dormant = p_max is None or p_max < 40
    if dormant:
        return {d["code"]: NEUTRAL for d in grid}, dormant, p_max

    by_driver = algo_snapshot["track_history"]["by_driver"]
    raw_by_code = {}
    n_wet_by_code = {}
    for d in grid:
        rows = [r for r in by_driver.get(d["code"], []) if r.get("wet")]
        n_wet_by_code[d["code"]] = len(rows)
        if rows:
            raw_by_code[d["code"]] = _weighted_mean(rows)

    normalized = field_normalize(raw_by_code)
    out = {}
    for d in grid:
        code = d["code"]
        n = n_wet_by_code[code]
        out[code] = NEUTRAL if n == 0 else shrink_by_n(normalized[code], n)
    return out, dormant, p_max


# ---------- F8 teammate H2H ----------

def compute_teammate_h2h(algo_snapshot):
    """Over every completed round this season (form.all_rounds), count races
    where both teammates were classified and who finished ahead. Reads the
    per-round classification results already captured in the snapshot by
    build_form() -- no network call (sec1: score.py never calls an API).
    """
    grid = algo_snapshot["grid"]
    all_rounds = algo_snapshot["form"]["all_rounds"]
    per_round = algo_snapshot["form"]["results_by_round"]

    ahead_count = defaultdict(int)
    total_count = defaultdict(int)

    for rnd in all_rounds:
        rows = per_round[str(rnd)]
        seen_constructors = defaultdict(list)
        for row in rows:
            classified = row["status"] == "Finished" or row["status"].startswith("+")
            seen_constructors[row["constructor_id"]].append((row["code"], row["position"], classified))
        for cid, entries in seen_constructors.items():
            if len(entries) != 2:
                continue
            (c1, p1, cl1), (c2, p2, cl2) = entries
            if not (cl1 and cl2):
                continue
            total_count[c1] += 1
            total_count[c2] += 1
            if p1 < p2:
                ahead_count[c1] += 1
            else:
                ahead_count[c2] += 1

    out = {}
    for d in grid:
        code = d["code"]
        n = total_count.get(code, 0)
        out[code] = (ahead_count.get(code, 0) / n) if n >= 3 else NEUTRAL
    return out


# ---------- combine ----------

def effective_weights(base_weights, m, is_sprint_weekend):
    w = dict(base_weights)
    w_grid_eff = w["grid"] * m
    scale = (1 - w_grid_eff) / (1 - w["grid"])
    eff = {"grid": w_grid_eff}
    for k, v in w.items():
        if k == "grid":
            continue
        eff[k] = v * scale
    assert abs(sum(eff.values()) - 1.0) < 1e-9, "effective weights must sum to 1.0 after flex"

    if not is_sprint_weekend:
        eff.pop("sprint")
        total = sum(eff.values())
        eff = {k: v / total for k, v in eff.items()}
        assert abs(sum(eff.values()) - 1.0) < 1e-9, "effective weights must sum to 1.0 after drop"

    return eff


def score_all(algo_snapshot):
    assert "markets" not in algo_snapshot, "market-blindness violated: scorer received market data"

    grid = algo_snapshot["grid"]
    codes = [d["code"] for d in grid]

    s_grid = compute_grid(algo_snapshot)
    s_team = compute_team_form(algo_snapshot)
    s_sprint = compute_sprint(algo_snapshot)
    s_driver_form = compute_driver_form(algo_snapshot)
    s_track, track_n = compute_track_history(algo_snapshot)
    s_champ = compute_champ(algo_snapshot)
    s_weather, weather_dormant, p_max = compute_weather(algo_snapshot)
    s_teammate = compute_teammate_h2h(algo_snapshot)

    sub_scores = {
        "grid": s_grid, "team": s_team, "sprint": s_sprint, "driver_form": s_driver_form,
        "track": s_track, "champ": s_champ, "weather": s_weather, "teammate": s_teammate,
    }
    for fname, by_code in sub_scores.items():
        for code, v in by_code.items():
            assert -1e-9 <= v <= 1 + 1e-9, f"sub-score {fname}/{code}={v} out of [0,1]"

    m = algo_snapshot["meta"]["track_overtaking_multiplier"]
    is_sprint_weekend = algo_snapshot["meta"]["is_sprint_weekend"]
    eff = effective_weights(BASE_WEIGHTS, m, is_sprint_weekend)

    raw_scores = {}
    for code in codes:
        total = 0.0
        for fkey, w in eff.items():
            total += w * sub_scores[fkey][code]
        raw_scores[code] = total

    assert T > 0, "T must be > 0"
    max_score = max(raw_scores.values())
    exps = {code: math.exp((s - max_score) / T) for code, s in raw_scores.items()}
    denom = sum(exps.values())
    p_algo = {code: v / denom for code, v in exps.items()}

    total_p = sum(p_algo.values())
    assert abs(total_p - 1.0) < 1e-6, f"probabilities sum to {total_p}, expected 1.0"

    qualifying_codes = set(codes)
    assert qualifying_codes == set(p_algo.keys()), "driver set mismatch vs qualifying classification"

    return {
        "sub_scores": sub_scores,
        "track_n": track_n,
        "weather_dormant": weather_dormant,
        "p_max": p_max,
        "effective_weights": eff,
        "raw_scores": raw_scores,
        "p_algo": p_algo,
    }


def load_latest_snapshot(snapshot_dir):
    paths = sorted(glob.glob(os.path.join(snapshot_dir, "*.json")))
    if not paths:
        raise SystemExit(f"no snapshots found in {snapshot_dir}")
    return paths[-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshot", nargs="?", default=None, help="path to a snapshot JSON; defaults to the latest in data/snapshots")
    ap.add_argument("--winner", default=None, help="FIA code of the actual race winner, if known (post-race scoring)")
    ap.add_argument("--out", default=None, help="path to write the score result JSON; defaults alongside the snapshot")
    args = ap.parse_args()

    snapshot_path = args.snapshot or load_latest_snapshot(os.path.join(REPO_ROOT, "data", "snapshots"))
    with open(snapshot_path) as f:
        snapshot = json.load(f)

    algo_snapshot = {k: v for k, v in snapshot.items() if k != "markets"}

    result = score_all(algo_snapshot)
    p_algo = result["p_algo"]

    print(f"{snapshot['meta']['race_name']} {snapshot['meta']['season']} — m={snapshot['meta']['track_overtaking_multiplier']}, "
          f"sprint_weekend={snapshot['meta']['is_sprint_weekend']}, weather p_max={result['p_max']}% "
          f"({'dormant' if result['weather_dormant'] else 'ACTIVE'})")
    print(f"effective weights: " + ", ".join(f"{k} {v:.4f}" for k, v in result["effective_weights"].items()))
    print()

    ranked = sorted(p_algo.items(), key=lambda kv: -kv[1])
    print(f"{'code':5} {'score':>8} {'p_algo':>8}")
    for code, p in ranked[:10]:
        print(f"{code:5} {result['raw_scores'][code]:>8.4f} {p*100:>7.1f}%")

    # market comparison -- only now, after scoring is fully complete
    markets = snapshot["markets"]
    market_mean = markets["market_mean"]
    comparison = {}
    for code, p in p_algo.items():
        pm = markets["polymarket"]["by_code"].get(code, {}).get("normalized")
        kx = markets["kalshi"]["by_code"].get(code, {}).get("normalized")
        mm = market_mean.get(code)
        edge = (p - mm) if mm is not None else None
        spread = (abs(pm - kx) if (pm is not None and kx is not None) else None)
        comparison[code] = {
            "p_algo": p, "p_polymarket": pm, "p_kalshi": kx, "p_market_mean": mm,
            "edge": edge, "venue_spread": spread,
        }

    print()
    print("algo vs market:")
    print(f"{'code':5} {'algo':>7} {'poly':>7} {'kalshi':>7} {'mean':>7} {'edge':>7}")
    for code, p in ranked[:10]:
        c = comparison[code]
        fmt = lambda v: f"{v*100:6.1f}%" if v is not None else "   n/a "
        edge_fmt = f"{c['edge']*100:+6.1f}" if c["edge"] is not None else "   n/a"
        print(f"{code:5} {fmt(c['p_algo'])} {fmt(c['p_polymarket'])} {fmt(c['p_kalshi'])} {fmt(c['p_market_mean'])} {edge_fmt}")

    edges = [(code, c["edge"]) for code, c in comparison.items() if c["edge"] is not None]
    if edges:
        top_pos = max(edges, key=lambda kv: kv[1])
        top_neg = min(edges, key=lambda kv: kv[1])
        print(f"\nlargest positive edge: {top_pos[0]} {top_pos[1]*100:+.1f}")
        print(f"largest negative edge: {top_neg[0]} {top_neg[1]*100:+.1f}")

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
    }

    if args.winner:
        winner = args.winner
        assert winner in p_algo, f"winner code {winner!r} not in this snapshot's driver set"
        outcome = {code: (1.0 if code == winner else 0.0) for code in p_algo}

        def brier(probs):
            return sum((probs.get(code, 0.0) - outcome[code]) ** 2 for code in outcome)

        pm_probs = {code: markets["polymarket"]["by_code"].get(code, {}).get("normalized") or 0.0 for code in p_algo}
        kx_probs = {code: markets["kalshi"]["by_code"].get(code, {}).get("normalized") or 0.0 for code in p_algo}
        mm_probs = {code: market_mean.get(code, 0.0) for code in p_algo}

        brier_algo = brier(p_algo)
        brier_pm = brier(pm_probs)
        brier_kx = brier(kx_probs)
        brier_mm = brier(mm_probs)
        top_pick = max(p_algo.items(), key=lambda kv: kv[1])[0]

        print(f"\n--- post-race scoring vs winner={winner} ---")
        print(f"brier: algo={brier_algo:.4f} polymarket={brier_pm:.4f} kalshi={brier_kx:.4f} market_mean={brier_mm:.4f}")
        print(f"algo top pick: {top_pick} ({'correct' if top_pick == winner else 'incorrect'})")
        print(f"algo beat market_mean on brier: {brier_algo < brier_mm}")

        output["post_race"] = {
            "winner": winner, "brier_algo": brier_algo, "brier_polymarket": brier_pm,
            "brier_kalshi": brier_kx, "brier_market_mean": brier_mm,
            "top_pick": top_pick, "top_pick_correct": top_pick == winner,
            "beat_market_mean_on_brier": brier_algo < brier_mm,
        }

    out_path = args.out or (os.path.splitext(snapshot_path)[0] + "-score.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
