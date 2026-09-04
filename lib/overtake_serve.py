"""`08` at serve time: the fitted model as an artifact, and its feature vector
built from one tick. 09 sec5.3.

`08` was built as a validation harness -- `overtake_fit.py` fits a model per
race-forward fold, scores it, and reports calibration. It never persisted a
model, because nothing consumed one: `data/live/overtakes/fit_recal.json` holds
metrics only, no weights. This layer is the consumer `08` was shaped for, so the
weights, the standardisation statistics and the two serve-time thresholds have
to become an object that outlives the fitting run. That object is produced by
`winprob_fit.py` and loaded here.

Two constants are hard-coded rather than taken per fold, exactly as 09 sec5.3
and sec11 assertion 9 require:

  - **theta = 0.0037** -- 08 sec11.1's domain gate, the 80th percentile of the
    train+calibration predictions, pooled across folds.
  - **theta_front = 0.0105** -- 09 sec2.4's second gate, the 60th percentile of
    the calibration folds' in-domain predictions with the pursuer inside the top
    six, pooled across folds.

Both are pooled across the eight folds rather than taken per fold. That is a
mild leak and it is stated rather than quietly avoided: a live consumer sees one
tick at a time and cannot take a percentile over a race in progress (08 sec11.1
ruled that out for theta and the reasoning applies identically to theta_front),
so the pooled constant is the number the layer would actually serve with. The
per-fold ranges are narrow -- 0.0023-0.0059 and 0.0095-0.0116 -- so the leak is
small, but it is a leak.

## The feature vector from a tick

`08`'s training rows come out of `lib/overtake_features.build_episode_rows`,
which reads pandas frames off a completed FastF1 session. A live consumer has a
tick and whatever it has retained. The three history-dependent features --
`closing_rate`, `interval_min_recent`, `time_in_range` -- are the reason this
module keeps a short rolling buffer per ordered pair, and `time_in_range` is
tracked with the *same* episode rule the offline labeller uses
(`ov.EPISODE_INTERVAL_S`, broken on a change of car-ahead identity), because a
feature that means "seconds spent within 2 s of this specific car" offline and
something else live is exactly the train/serve skew `05` sec4.2 exists to stop.
"""

import json
import math

from . import overtake_features as of
from . import overtakes as ov
from .invariants import require

# 08 sec11.1 / 09 sec5.3. Serve-time constants, refit whenever 08 is retrained.
THETA = 0.0037
THETA_FRONT = 0.0105
# 09 sec5.3: the extra front-of-field gate applies when the pursuer is inside
# the top six, which is the band 09 sec2.4 measured theta_front on.
FRONT_POSITION_MAX = 6

CLOSING_LOOKBACK_S = of.CLOSING_LOOKBACK_S
RECENT_LOOKBACK_S = of.RECENT_LOOKBACK_S


class OvertakeModel:
    """A fitted `08` logistic, standardisation included, ready to score one row."""

    def __init__(self, weights, bias, stats, names=None, platt=None, meta=None):
        self.names = list(names or of.FEATURE_NAMES)
        require(len(weights) == len(self.names),
                "OvertakeModel: %d weights for %d features" % (len(weights), len(self.names)))
        self.weights = [float(w) for w in weights]
        self.bias = float(bias)
        self.stats = {k: (float(v[0]), float(v[1])) for k, v in stats.items()}
        self.platt = tuple(platt) if platt else None
        self.meta = meta or {}

    def predict_row(self, row, use_platt=False):
        z = self.bias
        for name, w in zip(self.names, self.weights):
            mu, sd = self.stats[name]
            z += w * ((float(row[name]) - mu) / (sd if sd else 1.0))
        p = 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z))))
        if use_platt and self.platt:
            a, b = self.platt
            lo = math.log(max(p, 1e-12) / max(1.0 - p, 1e-12))
            p = 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, a * lo + b))))
        return p

    def as_dict(self):
        return {"weights": self.weights, "bias": self.bias,
                "stats": {k: list(v) for k, v in self.stats.items()},
                "names": self.names,
                "platt": list(self.platt) if self.platt else None,
                "meta": self.meta}

    @classmethod
    def from_dict(cls, d):
        return cls(d["weights"], d["bias"], d["stats"], d.get("names"),
                   d.get("platt"), d.get("meta"))


def load_models(path):
    """{round: OvertakeModel} -- one per race-forward fold. The model used to
    score race n was fitted on races strictly before it (`05` sec6.1)."""
    with open(path) as fh:
        blob = json.load(fh)
    return ({int(k): OvertakeModel.from_dict(v) for k, v in blob["models"].items()},
            blob.get("meta", {}))


def parse_interval(gap_ahead):
    """`03` sec7.1 keeps gap fields as the feed's verbatim strings and never as
    parsed floats. Parsing happens here, at the consumer, and the non-numeric
    forms are dropped rather than coerced: `08` sec13.6 item 3 measured that
    `LAP n` is not "a lapped car" -- 72% of those rows sit at Position 1, the
    leader, who has no car ahead -- and its semantics are still UNVERIFIED.
    """
    if gap_ahead is None:
        return None
    s = str(gap_ahead).strip()
    if not s:
        return None
    try:
        return float(s.lstrip("+"))
    except ValueError:
        return None


class PairHistory:
    """Rolling per-(pursuer, ahead) interval history, and the episode clock."""

    def __init__(self):
        self.samples = []       # (t, interval)
        self.episode_start = None

    def update(self, t, interval):
        self.samples.append((t, interval))
        cutoff = t - max(CLOSING_LOOKBACK_S, RECENT_LOOKBACK_S)
        while len(self.samples) > 1 and self.samples[0][0] < cutoff:
            self.samples.pop(0)
        if interval < ov.EPISODE_INTERVAL_S:
            if self.episode_start is None:
                self.episode_start = t
        else:
            self.episode_start = None

    def closing_rate(self, t):
        pts = [(s, v) for s, v in self.samples if s >= t - CLOSING_LOOKBACK_S]
        if len(pts) < 2:
            return 0.0
        n = len(pts)
        mx = sum(s for s, _ in pts) / n
        my = sum(v for _, v in pts) / n
        num = sum((s - mx) * (v - my) for s, v in pts)
        den = sum((s - mx) ** 2 for s, _ in pts)
        return num / den if den > 0 else 0.0

    def interval_min_recent(self, t, fallback):
        vals = [v for s, v in self.samples if s >= t - RECENT_LOOKBACK_S]
        return min(vals) if vals else fallback

    def time_in_range(self, t):
        return 0.0 if self.episode_start is None else max(0.0, t - self.episode_start)


def feature_row(tick, pursuer, ahead, history, track_frac, lap_total):
    """`08`'s feature vector for one adjacent pair, from one tick.

    Returns None if any field would be `None`. 09 sec5.3 makes that a real
    branch rather than a formality: `03` sec8's degraded modes mean an absent
    CarData window leaves speed / throttle / brake at None, and 09 sec5.3
    requires the pair to fall back to the background rate rather than be scored
    on a partially-invented vector.
    """
    cp, ca = tick.car(pursuer), tick.car(ahead)
    if cp is None or ca is None:
        return None
    interval = parse_interval(cp.gap_ahead)
    if interval is None or cp.position is None:
        return None
    for v in (cp.speed, ca.speed, cp.throttle, ca.throttle, cp.brake, ca.brake):
        if v is None:
            return None
    laps = cp.laps if cp.laps is not None else (tick.lap_current or 1) - 1
    if lap_total is None:
        return None
    t = tick.t_local
    row = {
        "interval": float(interval),
        "closing_rate": history.closing_rate(t),
        "interval_min_recent": history.interval_min_recent(t, float(interval)),
        "time_in_range": history.time_in_range(t),
        "speed_pursuer": float(cp.speed),
        "speed_ahead": float(ca.speed),
        "speed_delta": float(cp.speed) - float(ca.speed),
        "throttle_pursuer": float(cp.throttle),
        "throttle_ahead": float(ca.throttle),
        "brake_pursuer": float(cp.brake),
        "brake_ahead": float(ca.brake),
        "track_frac": float(track_frac),
        "lap_number": float(laps),
        "laps_remaining": float(lap_total) - float(laps),
        "position": float(cp.position),
        "under_caution": 0.0 if tick.track_status == 1 else 1.0,
    }
    return row


def admits(p_raw, pursuer_position):
    """09 sec5.3's two gates, and sec11 assertion 9's definition of a bug.

    theta admits ~20% of rows overall; at the front of the field theta_front
    then keeps 41% of what survives. Everything else is "no approach in
    progress" (08 sec11.1) and runs on the background rate.
    """
    if p_raw < THETA:
        return False
    if pursuer_position is not None and pursuer_position <= FRONT_POSITION_MAX:
        return p_raw >= THETA_FRONT
    return True
