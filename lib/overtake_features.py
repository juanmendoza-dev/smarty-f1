"""Feature vectors for the overtake model. 08-overtake-model.md sec6.

Every join in here is backward-looking. That is the single most important
property of this module and it is enforced, not assumed: a feature at decision
time t may only read data timestamped <= t. `pandas.merge_asof(direction=
"backward")` is used for every join for exactly this reason -- np.interp, which
an earlier probe used, interpolates *between* surrounding samples and would
pull the future into the feature vector. assert_no_lookahead() below is the
check, and build_rows() runs it on every matrix it produces.

Deliberately absent: any DRS analogue. 03 sec7.3 measured channel 45 constant
zero across a full 2026 race (944,196 samples, 22 drivers, no other value) and
forbids treating it as one. A `drs` column appearing here is a bug.

Throttle is NOT bounded at 100 (sec10). It was measured exceeding 100 on 10.3%
of samples, max 104; the assertion that it is in [0,100] would have crashed the
live client within seconds of the first green flag, and it is not reintroduced
here.
"""

import numpy as np
import pandas as pd

from .invariants import require

# sec5.2. The Position stream updates at a median 3.3s, so a 5s horizon cannot
# be honestly validated against it. 10s is what the raw label supports.
HORIZON_S = 10.0
# sec5.3 as amended: sample decision times on a fixed cadence inside episodes,
# WITHOUT reference to outcome, then label by looking forward.
SAMPLE_HZ = 1.0
# Lookbacks for the derived closing-rate features.
CLOSING_LOOKBACK_S = 3.0
RECENT_LOOKBACK_S = 10.0

FEATURE_NAMES = [
    "interval",
    "closing_rate",
    "interval_min_recent",
    "time_in_range",
    "speed_pursuer",
    "speed_ahead",
    "speed_delta",
    "throttle_pursuer",
    "throttle_ahead",
    "brake_pursuer",
    "brake_ahead",
    "track_frac",
    "lap_number",
    "laps_remaining",
    "position",
    "under_caution",
]


def _asof(frame, times, value_cols, tol=30.0):
    """Last value at or before each t. Backward-only by construction."""
    left = pd.DataFrame({"t": np.asarray(times, dtype=float)}).sort_values("t")
    right = frame.sort_values("t")
    out = pd.merge_asof(left, right, on="t", direction="backward",
                        tolerance=tol, allow_exact_matches=True)
    return out[value_cols]


def car_frame(session, drv):
    """Per-driver car telemetry on the session clock. sec6."""
    car = session.car_data.get(drv)
    if car is None or car.empty:
        return None
    f = pd.DataFrame({
        "t": car["SessionTime"].dt.total_seconds().values,
        "speed": car["Speed"].values.astype(float),
        "throttle": car["Throttle"].values.astype(float),
        "brake": car["Brake"].values.astype(float),
    })
    return f.sort_values("t")


def caution_frame(session):
    """Track status over time -- 1 is green, anything else is a caution.

    sec6: this is not optional garnish. Overtaking is forbidden under safety
    car, so unfiltered caution laps inject guaranteed-negative rows that teach
    the model nothing except the base rate.
    """
    ts = session.track_status
    if ts is None or len(ts) == 0:
        return None
    return pd.DataFrame({
        "t": ts["Time"].dt.total_seconds().values,
        "under_caution": [0.0 if str(s) == "1" else 1.0 for s in ts["Status"].values],
    }).sort_values("t")


def lap_frame(session, drv):
    """Lap number and lap-start time, for track position and laps remaining."""
    laps = session.laps.pick_drivers(drv)
    rows = []
    for _, r in laps.iterrows():
        if pd.isna(r["LapStartTime"]) or pd.isna(r["LapNumber"]):
            continue
        rows.append((r["LapStartTime"].total_seconds(), float(r["LapNumber"]),
                     r["LapTime"].total_seconds() if pd.notna(r["LapTime"]) else np.nan))
    if not rows:
        return None
    return pd.DataFrame(rows, columns=["t", "lap_number", "lap_time"]).sort_values("t")


def sample_times(episode, hz=SAMPLE_HZ):
    """Decision times inside an episode, chosen without reference to outcome."""
    step = 1.0 / hz
    n = int((episode.end - episode.start) / step)
    return [episode.start + i * step for i in range(n + 1)]


def label_rows(times, episode, passes, horizon=HORIZON_S):
    """label = 1 iff the pursuer passes THAT SPECIFIC car within (t, t+horizon].

    sec5.3 as amended. Labelling by lookahead on an outcome-blind sample is
    what keeps positives and negatives symmetric: a positive episode sampled
    40s before the pass correctly gets label 0, and those are the hard
    negatives that carry the discriminative work.
    """
    relevant = [p.t for p in passes
                if p.overtaker == episode.pursuer and p.overtaken == episode.ahead]
    out = []
    for t in times:
        out.append(1 if any(t < pt <= t + horizon for pt in relevant) else 0)
    return out


def assert_no_lookahead(sources, times):
    """sec10: every feature's source timestamp must be <= its decision time."""
    for name, src_times in sources.items():
        bad = [(s, t) for s, t in zip(src_times, times) if not pd.isna(s) and s > t + 1e-9]
        require(not bad,
                "lookahead in feature %r: %d rows read a source timestamp after the "
                "decision time (first: source=%s t=%s)"
                % (name, len(bad), bad[0][0] if bad else None, bad[0][1] if bad else None))


def build_episode_rows(session, episode, passes, caution, total_laps, iv, ahead_idx,
                       horizon=HORIZON_S):
    """One row per sampled decision time inside one episode."""
    times = sample_times(episode)
    if not times:
        return []

    pur = car_frame(session, episode.pursuer)
    ahd = car_frame(session, episode.ahead)
    if pur is None or ahd is None:
        return []
    lapf = lap_frame(session, episode.pursuer)
    if lapf is None:
        return []

    ivp = iv[iv["Driver"] == episode.pursuer][["t", "interval"]]
    if ivp.empty:
        return []

    tarr = np.asarray(times, dtype=float)
    f_iv = _asof(ivp, tarr, ["t", "interval"], tol=30.0).rename(columns={"t": "src_iv"})
    f_pur = _asof(pur, tarr, ["t", "speed", "throttle", "brake"], tol=5.0).rename(
        columns={"t": "src_pur", "speed": "speed_pursuer",
                 "throttle": "throttle_pursuer", "brake": "brake_pursuer"})
    f_ahd = _asof(ahd, tarr, ["t", "speed", "throttle", "brake"], tol=5.0).rename(
        columns={"t": "src_ahd", "speed": "speed_ahead",
                 "throttle": "throttle_ahead", "brake": "brake_ahead"})
    f_lap = _asof(lapf, tarr, ["t", "lap_number", "lap_time"], tol=600.0).rename(
        columns={"t": "src_lap"})
    if caution is not None:
        f_cau = _asof(caution, tarr, ["t", "under_caution"], tol=1e9).rename(
            columns={"t": "src_cau"})
    else:
        f_cau = pd.DataFrame({"src_cau": [np.nan] * len(tarr),
                              "under_caution": [0.0] * len(tarr)})

    assert_no_lookahead(
        {"interval": f_iv["src_iv"].values, "car_pursuer": f_pur["src_pur"].values,
         "car_ahead": f_ahd["src_ahd"].values, "lap": f_lap["src_lap"].values,
         "caution": f_cau["src_cau"].values},
        tarr)

    # closing rate and recent history, both from backward windows only
    closing, recent_min, time_in_range = [], [], []
    ivt = ivp["t"].values
    ivv = ivp["interval"].values
    for t in tarr:
        m_now = ivt <= t
        m_back = (ivt <= t) & (ivt >= t - CLOSING_LOOKBACK_S)
        m_rec = (ivt <= t) & (ivt >= t - RECENT_LOOKBACK_S)
        if m_back.sum() >= 2:
            xs, ys = ivt[m_back], ivv[m_back]
            closing.append(float(np.polyfit(xs, ys, 1)[0]))
        else:
            closing.append(0.0)
        recent_min.append(float(ivv[m_rec].min()) if m_rec.any() else np.nan)
        time_in_range.append(float(t - episode.start))

    laps_done = f_lap["lap_number"].values
    lap_time = f_lap["lap_time"].values
    lap_start = f_lap["src_lap"].values
    track_frac = np.where(np.isnan(lap_time) | (lap_time <= 0), 0.5,
                          np.clip((tarr - lap_start) / np.where(lap_time > 0, lap_time, 1.0), 0, 1))

    # the pursuer's own track position at each decision time -- read from the
    # AheadIndex, which is backward-only by construction (sec6 no-lookahead).
    pos_at = np.array([ahead_idx.position_of(t, episode.pursuer)
                       if ahead_idx.position_of(t, episode.pursuer) is not None else np.nan
                       for t in tarr], dtype=float)

    labels = label_rows(times, episode, passes, horizon)

    rows = []
    for i, t in enumerate(tarr):
        iv_val = f_iv["interval"].values[i]
        if pd.isna(iv_val):
            continue
        rows.append({
            "t": float(t),
            "pursuer": episode.pursuer,
            "ahead": episode.ahead,
            "episode_start": episode.start,
            "interval": float(iv_val),
            "closing_rate": closing[i],
            "interval_min_recent": recent_min[i] if not pd.isna(recent_min[i]) else float(iv_val),
            "time_in_range": time_in_range[i],
            "speed_pursuer": f_pur["speed_pursuer"].values[i],
            "speed_ahead": f_ahd["speed_ahead"].values[i],
            "speed_delta": f_pur["speed_pursuer"].values[i] - f_ahd["speed_ahead"].values[i],
            "throttle_pursuer": f_pur["throttle_pursuer"].values[i],
            "throttle_ahead": f_ahd["throttle_ahead"].values[i],
            "brake_pursuer": f_pur["brake_pursuer"].values[i],
            "brake_ahead": f_ahd["brake_ahead"].values[i],
            "track_frac": float(track_frac[i]),
            "lap_number": float(laps_done[i]) if not pd.isna(laps_done[i]) else np.nan,
            "laps_remaining": (float(total_laps) - float(laps_done[i])
                               if not pd.isna(laps_done[i]) else np.nan),
            "position": float(pos_at[i]) if not pd.isna(pos_at[i]) else np.nan,
            "under_caution": float(f_cau["under_caution"].values[i])
                             if not pd.isna(f_cau["under_caution"].values[i]) else 0.0,
            "label": labels[i],
        })
    return rows
