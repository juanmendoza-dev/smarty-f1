"""Lane A prior, DNF hazard, and the strength reconciliation. 09 sec5.5 / sec6.

The layer initialises from `02`'s pre-race winner distribution and must not
change it. What this module produces is a *reconciled* strength vector `w'` --
derived from `02`'s locked `T` and weights, never a replacement for them
(09 sec12, last bullet).

Why reconciliation exists at all, in one paragraph, because it is the subtlest
thing in the layer. `02` sec5.4 anchored `T = 0.1168` to the realized historical
rate at which pole converts to a win, so `w_d = exp(score_d / T)` already has
full-race DNF risk priced in implicitly (`04` sec6.3 rejected an earlier design
over exactly this). A *live* layer cannot leave attrition implicit: a car that
has actually retired must go to 0, and the remaining hazard must shrink as laps
run out. So the implicit hazard is REPLACED by an explicit one rather than
stacked on top of it -- iterative proportional fitting solves for `w'` such that
the simulator, run from lights-out with the explicit hazard active, reproduces
`02`'s `p_algo`. At progress 0 nothing has changed; from then on survival is
applied over the remaining fraction only. That is 09 sec11 assertion 2, and it
is the assertion that proves the double-count never forms.

The prior for a replayed archived race is *reconstructed, not looked up*
(09 sec6): `data/training/winner.csv` carries `p_a1` per driver per race, built
by `backfill.py` through `02`'s own code path under `05` sec4.4's leakage rules.
Only the 2026 Dutch GP has a live snapshot, so every other race has to come
through the backfill path.
"""

import csv
import math
import os
from collections import defaultdict

from .features import is_classified, shrink_by_n
from .invariants import require

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINNER_CSV = os.path.join(REPO_ROOT, "data", "training", "winner.csv")

# 04 sec5.1's verified 2025 full-season DNF rate, the round-1 fallback.
DEFAULT_DNF_RATE = 0.1253

# 09 sec5.5: IPF is ill-conditioned against near-zero targets, and 02 sec9's
# reference field has 15 drivers sharing ~2.1%. Reconcile the top band only.
RECONCILE_MIN_P = 0.01
IPF_ITERS = 60
IPF_DAMPING = 0.7
# 09 sec5.5 / 04 sec6.2: reconcile once per race, offline, at a budget where
# the ratio update is not chasing Monte Carlo noise.
RECONCILE_N = 200_000

# A car that appears in the archive with no prior row (backfill dropped it, or
# it was a late entry) still has to be in the permutation, or `p_win` cannot
# sum to 1 over the field the tick actually carries (09 sec11 assertion 1).
# It enters at the weakest reconciled strength in the field and is excluded
# from the reconcile band. Declared once here rather than decided per call.
UNKNOWN_DRIVER_STRENGTH_QUANTILE = "min"


def load_prior_rows(season, round_, path=WINNER_CSV):
    """The A1 feature/outcome rows for one race, from the committed matrix."""
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if int(r["season"]) == season and int(r["round"]) == round_:
                rows.append(r)
    require(rows, "no prior rows for %d R%d in %s" % (season, round_, path))
    return rows


def prior_from_rows(rows):
    """(p_algo by code, grid order by code, prior_id).

    `p_a1` is `02` sec5.4's `p_algo`, computed by backfill.py through `02`'s own
    scorer. It is read, never recomputed here -- `05` sec4.2's rule: a re-typed
    pos_score with a different K produces plausible numbers that are not the
    feature the scorer computes.
    """
    p_algo = {r["driver_code"]: float(r["p_a1"]) for r in rows}
    total = sum(p_algo.values())
    require(abs(total - 1.0) < 1e-6,
            "prior p_a1 sums to %.9f, not 1.0 -- the backfill row set is incomplete" % total)
    grid = {}
    for r in rows:
        q = r.get("quali_position")
        grid[r["driver_code"]] = int(q) if q not in (None, "", "NA") else None
    prior_id = "a1:%s:R%s" % (rows[0]["season"], rows[0]["round"])
    return p_algo, grid, prior_id


def grid_order(grid_by_code):
    """Starting order as a list of codes. Cars with no qualifying position go
    to the back in code order -- never interleaved, and never dropped."""
    known = sorted((p, c) for c, p in grid_by_code.items() if p is not None)
    unknown = sorted(c for c, p in grid_by_code.items() if p is None)
    return [c for _, c in known] + unknown


def strengths_from_prior(p_algo):
    """`04` sec6.1's quantity: w_d proportional to p_algo_d.

    Under Plackett-Luce, P(d first) = w_d / sum(w), so the strengths implied by
    `02`'s closed-form `p_algo` ARE `p_algo` up to scale -- there is no need to
    go back through `exp((score - max)/T)` and re-derive them, and doing so
    would re-import `02`'s scorer for no gain. Normalised to sum to 1 so IPF
    starts somewhere numerically sane.
    """
    return dict(p_algo)


# ---------- DNF: F_dnf_d, race-forward, and the two-segment hazard ----------

def dnf_rates_before(season, round_, path=WINNER_CSV):
    """`04` sec5.1/sec5.2's F_dnf_d, computed from rounds STRICTLY BEFORE this
    one in the same season. Race-forward, like everything else fitted here
    (`05` sec6.1) -- a reliability rate that reads the race it is scoring is a
    leak, and this one would be an especially cheap one.

    Reuses `04`'s own primitives (`is_classified`, `shrink_by_n`) rather than
    restating the blend table. Returns (F_dnf by code, field_dnf_rate).
    """
    driver_entries = defaultdict(list)
    team_entries = defaultdict(list)
    codes_this_race, team_of = set(), {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if int(r["season"]) != season:
                continue
            rnd = int(r["round"])
            if rnd == round_:
                codes_this_race.add(r["driver_code"])
                team_of[r["driver_code"]] = r["constructor_id"]
            elif rnd < round_:
                ok = is_classified(r["status"])
                driver_entries[r["driver_code"]].append(ok)
                team_entries[r["constructor_id"]].append(ok)

    total = sum(len(v) for v in driver_entries.values())
    if total == 0:
        field_rate = DEFAULT_DNF_RATE          # 04 sec5.1: round 1 of a season
    else:
        bad = sum(1 for v in driver_entries.values() for ok in v if not ok)
        field_rate = bad / total

    def rate_and_n(entries):
        n = len(entries)
        if n == 0:
            return field_rate, 0
        return sum(1 for ok in entries if not ok) / n, n

    out = {}
    for code in sorted(codes_this_race):
        dr, dn = rate_and_n(driver_entries.get(code, []))
        tr, tn = rate_and_n(team_entries.get(team_of.get(code), []))
        out[code] = (0.5 * shrink_by_n(dr, dn, prior=field_rate)
                     + 0.5 * shrink_by_n(tr, tn, prior=field_rate))
        require(0.0 <= out[code] <= 1.0, "F_dnf/%s=%r out of [0,1]" % (code, out[code]))
    return out, field_rate


class TwoSegmentHazard:
    """09 sec2.5's mildly front-loaded retirement hazard.

    Piecewise constant with a break at a quarter distance: intensity `a` on
    [0, 0.25) and `b` on [0.25, 1], normalised so 0.25a + 0.75b = 1. That
    normalisation is what makes the shape orthogonal to the level -- the level
    is `04` sec5.2's per-driver `F_dnf_d`, and this only says *when* within the
    race that risk is carried.

    `n = 50` retirements over 12 races does not settle a hazard shape (09
    sec2.5), so `flat()` exists and 09 sec5.5 requires it reported alongside.
    """

    def __init__(self, a, b, n_events=None, split=0.25):
        require(a >= 0 and b >= 0, "hazard intensities must be non-negative")
        self.a, self.b, self.split, self.n_events = a, b, split, n_events

    @classmethod
    def flat(cls):
        return cls(1.0, 1.0, n_events=None)

    @classmethod
    def fit(cls, fractions, split=0.25):
        """From retirement race-fractions. Exposure is proportional to segment
        width, so the intensity of a segment is (its share of events) / (its
        share of the race)."""
        n = len(fractions)
        if n == 0:
            return cls.flat()
        early = sum(1 for f in fractions if f < split)
        a = (early / n) / split
        b = ((n - early) / n) / (1.0 - split)
        return cls(a, b, n_events=n, split=split)

    def intensity(self, progress):
        return self.a if progress < self.split else self.b

    def as_dict(self):
        return {"a": self.a, "b": self.b, "split": self.split, "n_events": self.n_events}


def lap_hazards(f_dnf_by_code, hazard, laps_from, laps_total):
    """Per-lap retirement probability per driver for laps `laps_from+1..total`.

    The level is set so that over the FULL race distance the survival model
    reproduces `04` sec5.2's `F_dnf_d` exactly:

        H_d      = -ln(1 - F_dnf_d)                 total hazard over the race
        lambda_l = H_d * s_l / L,   s_l = hazard.intensity(progress at lap l)

    with `s` normalised to mean 1 over the race, so `sum_l lambda_l = H_d` and
    `P(survive the whole race) = exp(-H_d) = 1 - F_dnf_d`. Returns only the
    remaining laps, which is the whole point: the hazard DECAYS as the race runs
    out, which is what an implicit full-race rate baked into `T` cannot do
    (09 sec5.5), and it is why 09 sec2.2's ladder can reach 1.000 late.

    Returns {code: [p_lap, ...]} of length laps_total - laps_from.
    """
    require(laps_total > 0, "lap_hazards: laps_total must be positive")
    s = [hazard.intensity((l + 0.5) / laps_total) for l in range(laps_total)]
    mean_s = sum(s) / len(s)
    s = [x / mean_s for x in s]
    out = {}
    for code, f in f_dnf_by_code.items():
        f = min(max(f, 0.0), 0.999)
        H = -math.log(1.0 - f)
        out[code] = [1.0 - math.exp(-H * s[l] / laps_total)
                     for l in range(laps_from, laps_total)]
    return out


# ---------- IPF ----------

def reconcile(p_algo, sim_win_probs, w_start=None, iters=IPF_ITERS,
              damping=IPF_DAMPING, min_p=RECONCILE_MIN_P):
    """One IPF sweep driver. `sim_win_probs(w) -> {code: p}` is the caller's
    simulator run from lights-out at a high N (09 sec5.5: never against the
    working simulator's noisy estimate, and never at serve time).

    Only drivers with `p_algo >= min_p` are reconciled; the tail is held
    proportional to its unreconciled strengths and the whole vector is
    renormalised each sweep. 09 sec5.5's tail guard, verbatim.

    Returns (w', diagnostics).
    """
    w = dict(w_start or strengths_from_prior(p_algo))
    band = [c for c in w if p_algo.get(c, 0.0) >= min_p]
    require(band, "reconcile: no driver clears p_algo >= %.3f" % min_p)
    history = []
    p_hat = sim_win_probs(w)
    for _ in range(iters):
        worst = max(abs(p_hat.get(c, 0.0) - p_algo[c]) for c in band)
        history.append(worst)
        for c in band:
            ph = p_hat.get(c, 0.0)
            if ph <= 0.0:
                # No simulated path put this driver first at all. Push hard but
                # bounded -- an unbounded kick here is how IPF diverges.
                w[c] *= 4.0
            else:
                w[c] *= (p_algo[c] / ph) ** damping
        total = sum(w.values())
        w = {c: v / total for c, v in w.items()}
        p_hat = sim_win_probs(w)
    worst = max(abs(p_hat.get(c, 0.0) - p_algo[c]) for c in band)
    history.append(worst)
    return w, {"band": sorted(band), "worst_abs_residual": worst,
               "residual_history": history, "iters": iters, "damping": damping,
               "p_hat": p_hat}
