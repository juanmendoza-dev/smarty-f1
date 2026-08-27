"""Overtake labelling and pursuit episodes. 08-overtake-model.md sec5.

Everything here reads the *historical archive* through FastF1, never a live
connection (sec4). That is the decision that decouples this model from B1's
delay measurement and from 03 sec4.4's live gate: an offline label is an
offline label no matter what the broadcast delay turns out to be.

Two things in here were measured before they were written, because the first
pass at both was wrong (sec2):

  - Ordering cars by integrated distance does NOT work. `add_distance()`
    integrates each car's own telemetry, so cumulative distance drifts between
    cars over a race; ranking by it reproduced FastF1's official per-lap
    Position only 44.7% of the time and invented 828 "overtakes" in one race.
    The feed's own Position stream is the ground truth and is what this module
    uses.
  - The debounce filter is NOT a no-op. Requiring the new order to still hold
    10s later drops 11 of 126 events across three 2026 races (8.7%), so
    sec5.1's four filters and the three-filter version are different
    procedures with different yields.

Measured yield of the four filters below (sec2.1): 43 / 38 / 34 on-track
overtakes for the 2026 Dutch, Hungarian and Belgian GPs -- about 38 a race.
"""

import bisect
from collections import namedtuple

import pandas as pd

from .invariants import require

# sec5.1. A swap that reverts inside this window is feed jitter, not a pass.
DEBOUNCE_S = 10.0
# sec5.1. Pit windows are padded on both sides -- a car is not racing for a
# few seconds either side of the pit lane either.
PIT_PAD_S = 10.0
# sec5.3. An episode is a stretch where the pursuer is within striking
# distance of the SAME car ahead.
EPISODE_INTERVAL_S = 2.0
EPISODE_MIN_S = 3.0
# A gap longer than this in the interval stream ends the episode rather than
# bridging it -- the feed is lossy and a 60s hole is not a pursuit.
EPISODE_MAX_GAP_S = 15.0

PassEvent = namedtuple("PassEvent", "t overtaker overtaken new_pos")
Episode = namedtuple("Episode", "pursuer ahead start end")


class AheadIndex:
    """Who is directly ahead of a given driver, at any time in the session.

    Built by walking the Position stream forward and snapshotting the full
    grid order at every update. Lookup is a binary search for the last
    snapshot at or before t -- never a snapshot *after* t, which would be
    lookahead (sec6, no-lookahead rule).
    """

    def __init__(self, pos_df):
        self._times = []
        self._snaps = []
        last = {}
        for row in pos_df.sort_values("t").itertuples(index=False):
            last[row.Driver] = int(row.Position)
            self._times.append(row.t)
            self._snaps.append(dict(last))

    def order_at(self, t):
        i = bisect.bisect_right(self._times, t) - 1
        return self._snaps[i] if i >= 0 else None

    def position_of(self, t, drv):
        o = self.order_at(t)
        if o is None:
            return None
        return o.get(drv)

    def ahead_of(self, t, drv):
        """The driver one position ahead of `drv` at time t, or None."""
        o = self.order_at(t)
        if o is None or drv not in o:
            return None
        p = o[drv]
        if p <= 1:
            return None
        for d, q in o.items():
            if q == p - 1:
                return d
        return None


def position_stream(stream_data):
    """The feed's own per-driver Position updates. sec2.2.

    Median update interval is 3.3s, which is why sec2.2 caps the honest
    prediction horizon at 10s rather than the 5s the owner asked for.
    """
    pos = stream_data[["t", "Driver", "Position"]].dropna()
    pos = pos[pos["Position"] > 0]
    require(not pos.empty, "position stream is empty -- archive load failed or schema drifted")
    return pos.sort_values("t")


def interval_stream(stream_data):
    """Numeric IntervalToPositionAhead only, with the non-numeric forms kept out.

    sec2.3: 111 of 31,304 rows in the Dutch GP are non-numeric, and 72% of
    those are at Position 1 -- the leader, who has no car ahead. Two string
    forms appear (`LAP n` and `1 L`) and their exact semantics are UNVERIFIED.
    They are dropped from the pursuit stream (a leader is not pursuing anyone)
    but never silently coerced to a number.
    """
    iv = stream_data[["t", "Driver", "IntervalToPositionAhead"]].dropna().copy()
    iv["interval"] = pd.to_numeric(iv["IntervalToPositionAhead"], errors="coerce")
    numeric = iv.dropna(subset=["interval"])
    require(len(numeric) > 0, "no numeric intervals -- IntervalToPositionAhead schema drifted")
    return numeric[["t", "Driver", "interval"]].sort_values("t")


def pit_windows(session):
    """Per-driver [in, out] windows from lap timing, padded by PIT_PAD_S."""
    windows = {}
    for drv in session.drivers:
        laps = session.laps.pick_drivers(drv)
        w = []
        for _, r in laps.iterrows():
            if pd.notna(r["PitInTime"]):
                start = r["PitInTime"].total_seconds()
                end = (r["PitOutTime"].total_seconds()
                       if pd.notna(r["PitOutTime"]) else start + 60.0)
                w.append((start, end))
        windows[drv] = w
    return windows


def in_pit(windows, drv, t, pad=PIT_PAD_S):
    return any(a - pad <= t <= b + pad for a, b in windows.get(drv, []))


def lap_one_end(session):
    """Timestamp after which start-lap churn is over. sec5.1."""
    t = session.laps[session.laps["LapNumber"] == 1]["Time"].dropna()
    return t.max().total_seconds() if len(t) else 0.0


def find_passes(pos, ahead_idx, windows, lap1_t):
    """On-track overtakes, sec5.1's four filters applied in order.

    A positive is an ordered pair whose relative order in the Position stream
    inverts, where the driver who held the position immediately prior is
    unique, both cars are racing, and the new order still holds DEBOUNCE_S
    later.
    """
    last = {}
    candidates = []
    for row in pos.itertuples(index=False):
        p = int(row.Position)
        prev = last.get(row.Driver)
        if prev is not None and p == prev - 1:
            # filter 1: the passed car must be uniquely identifiable
            passed = [d for d, q in last.items() if q == p and d != row.Driver]
            if len(passed) == 1:
                candidates.append(PassEvent(row.t, row.Driver, passed[0], p))
        last[row.Driver] = p

    events = []
    for ev in candidates:
        # filter 2: after lap 1
        if ev.t <= lap1_t:
            continue
        # filter 3: neither car in a pit window
        if in_pit(windows, ev.overtaker, ev.t) or in_pit(windows, ev.overtaken, ev.t):
            continue
        # filter 4: the new order persists (debounce)
        a = ahead_idx.position_of(ev.t + DEBOUNCE_S, ev.overtaker)
        b = ahead_idx.position_of(ev.t + DEBOUNCE_S, ev.overtaken)
        if a is None or b is None or not a < b:
            continue
        events.append(ev)
    return events


def find_episodes(iv, ahead_idx, windows, lap1_t):
    """Pursuit episodes: contiguous stretches within EPISODE_INTERVAL_S of the
    SAME car ahead. sec5.3.

    Breaking on a change of car-ahead identity is not cosmetic. The interval is
    always measured to whoever holds position p-1 *at that instant*, so when
    that car pits or is itself passed, the interval jumps to a different target.
    An episode that spans the change would teach the model that an identity
    swap is a closing rate.
    """
    episodes = []
    for drv, g in iv.groupby("Driver"):
        cur = None
        for row in g.sort_values("t").itertuples(index=False):
            t = row.t
            ahead = ahead_idx.ahead_of(t, drv)
            racing = (row.interval < EPISODE_INTERVAL_S and ahead is not None
                      and t > lap1_t
                      and not in_pit(windows, drv, t) and not in_pit(windows, ahead, t))
            if racing and cur is not None and cur["ahead"] == ahead \
                    and t - cur["last"] <= EPISODE_MAX_GAP_S:
                cur["last"] = t
                continue
            if cur is not None and cur["last"] - cur["start"] >= EPISODE_MIN_S:
                episodes.append(Episode(drv, cur["ahead"], cur["start"], cur["last"]))
            cur = {"ahead": ahead, "start": t, "last": t} if racing else None
        if cur is not None and cur["last"] - cur["start"] >= EPISODE_MIN_S:
            episodes.append(Episode(drv, cur["ahead"], cur["start"], cur["last"]))
    return episodes
