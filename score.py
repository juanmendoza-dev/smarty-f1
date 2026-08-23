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

from lib.features import NEUTRAL, K_GRID, K_SPRINT, K_FIN, pos_score, shrink_by_n, field_normalize, is_classified
from lib.simulate import simulate_topk_probabilities

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
T = 0.1168

BASE_WEIGHTS = {
    "grid": 0.35, "team": 0.15, "sprint": 0.13, "driver_form": 0.11,
    "track": 0.08, "champ": 0.08, "weather": 0.05, "teammate": 0.05,
}
assert abs(sum(BASE_WEIGHTS.values()) - 1.0) < 1e-9, "base weights must sum to 1.0"

# ---------- Phase A4 (04-outcome-expansion-algo.md) constants ----------

# sec5.1: real 2025 full-season DNF rate, verified live 2026-08-23 against
# Jolpica (60 non-classified results out of 479 entries across all of 2025).
# Only used as the round-1-of-a-season fallback, when there's no prior-round
# data this season to compute a field average DNF rate from.
DEFAULT_DNF_RATE = 0.1253

# sec7.1: fastest lap reuses 3 of 02's 8 features -- team/car form and driver
# recent form matter for a single-lap effort, sprint pace is a same-week
# same-track signal on sprint weekends; grid/track-history/champ/weather/H2H
# have no clear causal link to who sets one fast lap and are dropped rather
# than force-included.
FASTLAP_BASE_WEIGHTS = {"team": 0.55, "driver_form": 0.30, "sprint": 0.15}
assert abs(sum(FASTLAP_BASE_WEIGHTS.values()) - 1.0) < 1e-9, "fastlap base weights must sum to 1.0"

# sec6.2: Monte Carlo self-consistency tolerance between the simulation's own
# top-1 marginal and 02's closed-form p_algo (which has a known-exact answer
# for top-1) -- generous enough not to flake on legitimate sampling noise
# (~9x the ~0.11pp standard error at SIM_N=200,000), tight enough to catch a
# real implementation bug.
SELF_CONSISTENCY_TOLERANCE = 0.01


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
        elif is_classified(sprint["status"]):
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
            classified = is_classified(row["status"])
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
        s = pos_score(row["position"], K_FIN) if is_classified(row["status"]) else 0.0
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
            classified = is_classified(row["status"])
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


def compute_post_race(p_algo, markets, winner):
    """sec7: Brier score for algo/Polymarket/Kalshi/market-mean vs. the actual
    winner, plus whether the algo's top pick was right and whether it beat the
    market mean. Pulled out of main() so postrace.py can call it with a winner
    code sourced from Jolpica instead of a CLI flag.
    """
    assert winner in p_algo, f"winner code {winner!r} not in this snapshot's driver set"
    outcome = {code: (1.0 if code == winner else 0.0) for code in p_algo}

    def brier(probs):
        return sum((probs.get(code, 0.0) - outcome[code]) ** 2 for code in outcome)

    market_mean = markets["market_mean"]
    pm_probs = {code: markets["polymarket"]["by_code"].get(code, {}).get("normalized") or 0.0 for code in p_algo}
    kx_probs = {code: markets["kalshi"]["by_code"].get(code, {}).get("normalized") or 0.0 for code in p_algo}
    mm_probs = {code: market_mean.get(code, 0.0) for code in p_algo}

    brier_algo = brier(p_algo)
    brier_pm = brier(pm_probs)
    brier_kx = brier(kx_probs)
    brier_mm = brier(mm_probs)
    top_pick = max(p_algo.items(), key=lambda kv: kv[1])[0]

    return {
        "winner": winner, "brier_algo": brier_algo, "brier_polymarket": brier_pm,
        "brier_kalshi": brier_kx, "brier_market_mean": brier_mm,
        "top_pick": top_pick, "top_pick_correct": top_pick == winner,
        "beat_market_mean_on_brier": brier_algo < brier_mm,
    }


def compute_comparison(p_algo, markets):
    """sec6: per-driver algo-vs-market table (edge, venue spread). Pulled out
    of main() so postrace.py can write the same comparison block instead of
    silently dropping it -- the divergences are the product (sec6), not
    something a post-race run should lose.
    """
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
    return comparison


# ---------- Phase A4: DNF probability (04-outcome-expansion-algo.md sec5) ----------

def compute_dnf(algo_snapshot):
    """F_dnf: driver + team reliability rate, season-to-date (all_rounds, not
    the 5-race recent_rounds window -- a reliability estimate needs more races
    than a form window gives), shrunk toward the field's own average DNF rate
    this season rather than NEUTRAL (sec4/sec5.1 -- 0.5 is a nonsense prior for
    a DNF rate). No softmax: each driver's non-finish is roughly independent,
    not a single-winner competition, and each rate is already a genuine
    probability in [0,1].

    Returns (p_dnf: {code: float}, n_by_code: {code: {"driver_n", "team_n"}},
    field_dnf_rate: float).
    """
    grid = algo_snapshot["grid"]
    all_rounds = algo_snapshot["form"]["all_rounds"]
    per_round = algo_snapshot["form"]["results_by_round"]

    driver_entries = defaultdict(list)
    team_entries = defaultdict(list)
    for rnd in all_rounds:
        for row in per_round[str(rnd)]:
            classified = is_classified(row["status"])
            driver_entries[row["code"]].append(classified)
            team_entries[row["constructor_id"]].append(classified)

    total_entries = sum(len(v) for v in driver_entries.values())
    if total_entries == 0:
        # sec5.1: round 1 of a season -- no prior data to average. Fall back
        # to the real, verified 2025 full-season rate rather than NEUTRAL.
        field_dnf_rate = DEFAULT_DNF_RATE
    else:
        total_dnf = sum(1 for classifications in driver_entries.values()
                         for classified in classifications if not classified)
        field_dnf_rate = total_dnf / total_entries

    def rate_and_n(classifications):
        n = len(classifications)
        if n == 0:
            return field_dnf_rate, 0
        return sum(1 for c in classifications if not c) / n, n

    p_dnf = {}
    n_by_code = {}
    for d in grid:
        code = d["code"]
        driver_rate, n_driver = rate_and_n(driver_entries.get(code, []))
        team_rate, n_team = rate_and_n(team_entries.get(d["constructor_id"], []))
        driver_shrunk = shrink_by_n(driver_rate, n_driver, prior=field_dnf_rate)
        team_shrunk = shrink_by_n(team_rate, n_team, prior=field_dnf_rate)
        p_dnf[code] = 0.5 * driver_shrunk + 0.5 * team_shrunk
        n_by_code[code] = {"driver_n": n_driver, "team_n": n_team}

    for code, v in p_dnf.items():
        assert -1e-9 <= v <= 1 + 1e-9, f"p_dnf/{code}={v} out of [0,1]"

    return p_dnf, n_by_code, field_dnf_rate


# ---------- Phase A4: fastest lap (04-outcome-expansion-algo.md sec7) ----------

def fastlap_effective_weights(is_sprint_weekend):
    """sec7.1: no track-flex (fastest lap isn't grid-position-driven), just the
    sprint-drop-and-renormalize rule reused from 02 sec5.2."""
    if is_sprint_weekend:
        return dict(FASTLAP_BASE_WEIGHTS)
    w = {k: v for k, v in FASTLAP_BASE_WEIGHTS.items() if k != "sprint"}
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def compute_fastest_lap(algo_snapshot, sub_scores):
    """sec7.2. Reuses sub_scores['team']/['driver_form']/['sprint'] already
    computed by score_all() -- no recomputation, no network call. T_FL == T,
    borrowed from the win-market calibration (sec7.2 -- not independently
    anchored, flagged in sec11).
    """
    grid = algo_snapshot["grid"]
    codes = [d["code"] for d in grid]
    is_sprint_weekend = algo_snapshot["meta"]["is_sprint_weekend"]
    eff = fastlap_effective_weights(is_sprint_weekend)

    raw_scores = {code: sum(w * sub_scores[fkey][code] for fkey, w in eff.items()) for code in codes}

    assert T > 0, "T must be > 0"
    max_score = max(raw_scores.values())
    exps = {code: math.exp((s - max_score) / T) for code, s in raw_scores.items()}
    denom = sum(exps.values())
    p_fastlap = {code: v / denom for code, v in exps.items()}

    total_p = sum(p_fastlap.values())
    assert abs(total_p - 1.0) < 1e-6, f"fastlap probabilities sum to {total_p}, expected 1.0"

    return {"effective_weights": eff, "raw_scores": raw_scores, "p_fastlap": p_fastlap}


# ---------- Phase A4: podium + points (04-outcome-expansion-algo.md sec6) ----------

def compute_podium_points(raw_scores, p_algo):
    """sec6. Reuses raw_scores/T from score_all()'s win computation -- same
    w_d = exp((score_d - max) / T) strengths, Plackett-Luce simulation over
    the full field, no DNF removal (sec6.3: DNF risk is already implicitly
    priced into these strengths via T's historical calibration in 02 sec5.4;
    an explicit DNF draw here would double-count it and break the
    self-consistency check below).

    Returns (p_podium: {code}, p_points: {code}, sim_meta: {"n", "seed"}).
    """
    max_score = max(raw_scores.values())
    weights = {code: math.exp((s - max_score) / T) for code, s in raw_scores.items()}

    sim_probs, sim_meta = simulate_topk_probabilities(weights, ks=[1, 3, 10])

    for code in weights:
        sim_top1 = sim_probs[code][1]
        assert abs(sim_top1 - p_algo[code]) < SELF_CONSISTENCY_TOLERANCE, (
            f"simulated top-1 for {code}={sim_top1:.4f} vs closed-form p_algo={p_algo[code]:.4f} "
            f"-- exceeds self-consistency tolerance (sec6.2); likely an implementation bug"
        )

    p_podium = {code: sim_probs[code][3] for code in weights}
    p_points = {code: sim_probs[code][10] for code in weights}

    for code in weights:
        # p_win <= p_podium <= p_points (sec10 assertion 1). podium<=points is
        # exact by construction (same simulated draws, sec6.2); win<=podium
        # is exact for the *simulated* top-1 and holds for the closed-form
        # p_algo only up to the self-consistency tolerance already checked
        # above, so the same tolerance applies here.
        assert p_algo[code] <= p_podium[code] + SELF_CONSISTENCY_TOLERANCE, (
            f"p_win > p_podium for {code}"
        )
        assert p_podium[code] <= p_points[code] + 1e-9, f"p_podium > p_points for {code}"

    return p_podium, p_points, sim_meta


# ---------- Phase A4: K-of-N market comparison + Brier (sec6.4) ----------

def _normalized_by_code(market_block, venue):
    block = (market_block or {}).get(venue)
    if not block or "by_code" not in block:
        return {}
    return {code: v["normalized"] for code, v in block["by_code"].items() if v["normalized"] is not None}


def compute_comparison_kofn(p_by_code, market_block):
    """sec6.4: algo-vs-market comparison for a K-of-N or single-winner-shaped
    outcome (podium/points/fastest-lap all reuse this -- fastest-lap's
    market_block is single-winner-shaped like 02's winner market, same
    function works for both). market_block may be missing a venue's by_code
    entirely (points has no polymarket key, sec2) or carry a
    {"status": "unavailable"} placeholder instead of real data (sec8.2) --
    both are handled as "no data for this venue," not an error.
    """
    pm = _normalized_by_code(market_block, "polymarket")
    kx = _normalized_by_code(market_block, "kalshi")
    market_mean = (market_block or {}).get("market_mean", {})

    comparison = {}
    for code, p in p_by_code.items():
        pm_p = pm.get(code)
        kx_p = kx.get(code)
        mm = market_mean.get(code)
        edge = (p - mm) if mm is not None else None
        spread = (abs(pm_p - kx_p) if (pm_p is not None and kx_p is not None) else None)
        comparison[code] = {
            "p_algo": p, "p_polymarket": pm_p, "p_kalshi": kx_p, "p_market_mean": mm,
            "edge": edge, "venue_spread": spread,
        }
    return comparison


def compute_post_race_kofn(p_by_code, outcome_by_code, market_block=None):
    """sec6.4: per-driver binary Brier, MEAN across the field -- a different
    shape from 02 sec7's sum-based winner Brier (there, Brier sums over the
    whole field as a proper multi-class score in [0,2]; here every driver has
    their own independent binary outcome, so the natural score is the mean of
    each driver's own (p-outcome)^2, in [0,1]). Do not compare these numbers
    to a winner-market Brier score -- same formula shape, different metric.

    market_block=None for DNF (sec2: no market exists on either venue) --
    only the outcome-vs-algo Brier is computed then, no market columns.
    """
    def brier_mean(probs):
        return sum((probs.get(code, 0.0) - outcome_by_code[code]) ** 2
                    for code in outcome_by_code) / len(outcome_by_code)

    result = {"brier_algo": brier_mean(p_by_code)}

    if market_block is not None:
        pm = _normalized_by_code(market_block, "polymarket")
        kx = _normalized_by_code(market_block, "kalshi")
        mm = market_block.get("market_mean", {})

        if pm:
            result["brier_polymarket"] = brier_mean(pm)
        if kx:
            result["brier_kalshi"] = brier_mean(kx)
        if mm:
            result["brier_market_mean"] = brier_mean(mm)

    return result


DERIVED_SUFFIXES = ("-score.json", "-postrace.json")


def load_latest_snapshot(snapshot_dir):
    paths = sorted(glob.glob(os.path.join(snapshot_dir, "*.json")))
    paths = [p for p in paths if not p.endswith(DERIVED_SUFFIXES)]
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
    comparison = compute_comparison(p_algo, markets)

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

    # ---------- Phase A4: podium, points, DNF, fastest lap ----------
    p_dnf, dnf_n, field_dnf_rate = compute_dnf(algo_snapshot)
    fastlap = compute_fastest_lap(algo_snapshot, result["sub_scores"])
    p_fastlap = fastlap["p_fastlap"]
    p_podium, p_points, sim_meta = compute_podium_points(result["raw_scores"], p_algo)

    podium_comparison = compute_comparison_kofn(p_podium, markets.get("podium"))
    points_comparison = compute_comparison_kofn(p_points, markets.get("points"))
    fastlap_comparison = compute_comparison_kofn(p_fastlap, markets.get("fastest_lap"))

    print()
    print(f"phase A4 (field DNF rate this season: {field_dnf_rate*100:.1f}%, "
          f"simulation n={sim_meta['n']} seed={sim_meta['seed']}):")
    print(f"{'code':5} {'podium':>8} {'points':>8} {'dnf':>7} {'fastlap':>8}")
    for code, p in ranked[:10]:
        print(f"{code:5} {p_podium[code]*100:7.1f}% {p_points[code]*100:7.1f}% "
              f"{p_dnf[code]*100:6.1f}% {p_fastlap[code]*100:7.1f}%")

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
        "phase_a4": {
            "p_dnf": p_dnf, "dnf_n": dnf_n, "field_dnf_rate": field_dnf_rate,
            "fastlap_effective_weights": fastlap["effective_weights"],
            "fastlap_raw_scores": fastlap["raw_scores"], "p_fastlap": p_fastlap,
            "p_podium": p_podium, "p_points": p_points, "sim_meta": sim_meta,
            "podium_comparison": podium_comparison,
            "points_comparison": points_comparison,
            "fastlap_comparison": fastlap_comparison,
        },
    }

    if args.winner:
        post_race = compute_post_race(p_algo, markets, args.winner)

        print(f"\n--- post-race scoring vs winner={post_race['winner']} ---")
        print(f"brier: algo={post_race['brier_algo']:.4f} polymarket={post_race['brier_polymarket']:.4f} "
              f"kalshi={post_race['brier_kalshi']:.4f} market_mean={post_race['brier_market_mean']:.4f}")
        print(f"algo top pick: {post_race['top_pick']} ({'correct' if post_race['top_pick_correct'] else 'incorrect'})")
        print(f"algo beat market_mean on brier: {post_race['beat_market_mean_on_brier']}")

        output["post_race"] = post_race

    out_path = args.out or (os.path.splitext(snapshot_path)[0] + "-score.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
