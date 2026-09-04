"""Replay an archived race as `03` sec7.1 ticks. 09 sec9.1.

**This is the train/serve skew guard, not a convenience.** The layer must
consume replayed ticks through exactly the same entry point a live client would
feed -- `05` sec4.2's rule and `08` sec10's "feature vectors computed offline
match what a live tick would produce, field for field" are the same requirement,
and this is where it gets tested for this layer.

**What replay validates, and what it does not.** The archive is post-processed
and complete. The live feed is delta-encoded, lossy, has degraded modes (`03`
sec8), reconnect gaps (`03` sec9.4) and schema drift (`03` sec10). Replay
validates the *estimator*. It does not validate the live plumbing, and every
claim about live behaviour from a replay run is UNVERIFIED until `03` sec13's
acceptance run. `inject` below exists so the `reliable` logic is exercised
offline rather than first meeting a degraded tick during a race.

Two modes, and the difference matters for one reported number:

  - `lap` -- one tick per lap boundary, loaded with `telemetry=False`. Cheap,
    and enough for the prior/hazard/simulator half of the layer. Every tick is
    legitimately `degraded = {position, cardata}` under `03` sec8, so under 09
    sec8.1 every estimate is `reliable = False`.
  - `full` -- 1 Hz ticks with car telemetry, positions and intervals. This is
    the only mode in which 09 sec5.7's realised suppression fraction means
    anything; measured in `lap` mode it would read 100% and say nothing.

Nothing here opens a socket. `03` sec4.4's live gate is untouched.
"""

import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from types import MappingProxyType

import fastf1
from fastf1 import api

from .invariants import require
from .livetiming_tick import CarState, Tick

CACHE_DIR = "data/cache/fastf1"
SEASON = 2026
# 08 sec5.3's SAMPLE_HZ. Keeping the replay cadence identical to the cadence 08
# was trained at is the point of 09 sec7.2's "1 Hz matches 08's own training
# cadence" -- a live layer sampling faster than the model was fitted on is
# train/serve skew wearing a different hat.
REPLAY_HZ = 1.0

# `04` sec5.1 measured the season's four status values from Jolpica and 09
# sec2.5 re-measured them in the archive: Finished, Lapped, Retired, Did not
# start. `Lapped` IS a finish -- treating it as a retirement inflates the count
# from 4.2/race to 6.6/race and flips the hazard from front- to back-loaded
# (09 sec15's recorded correction).
RETIRED_STATUS = "Retired"


def enable_cache(path=CACHE_DIR):
    fastf1.Cache.enable_cache(path)


class RaceArchive:
    """One archived race, loaded once and queried many times."""

    def __init__(self, season, rnd, name=None, telemetry=False):
        enable_cache()
        self.season, self.round, self.name = season, rnd, name
        self.session = fastf1.get_session(season, rnd, "R")
        self.session.load(telemetry=telemetry, laps=True, weather=False, messages=False)
        self.telemetry = telemetry
        laps = self.session.laps
        require(laps is not None and not laps.empty,
                "R%d has no laps -- archive load failed" % rnd)
        self.laps = laps
        self.total_laps = int(laps["LapNumber"].max())
        self.results = self.session.results
        self.codes = sorted(set(str(d) for d in laps["Driver"].dropna().unique()))
        # Two key spaces meet here and they are NOT interchangeable. `session.laps`
        # is keyed on the FIA three-letter code (`01` sec8.2's locked canonical
        # key), while the timing stream, `car_data` and `pos_data` are all keyed
        # on the racing number -- which is also what `03` sec7.1's DriverList
        # join uses live. Getting this wrong is silent: every asof join simply
        # matches nothing and every telemetry field comes back None, which looks
        # exactly like `03` sec8's degraded mode rather than like a bug.
        self.number_of = {}
        for _, r in laps[["Driver", "DriverNumber"]].dropna().drop_duplicates().iterrows():
            self.number_of[str(r["Driver"])] = str(r["DriverNumber"])
        require(len(self.number_of) == len(self.codes),
                "driver code <-> number map is incomplete: %d codes, %d numbers"
                % (len(self.codes), len(self.number_of)))
        self.circuit_id = _circuit_id(self.session)

        self._stream = None
        if telemetry:
            _, stream = api.timing_data(self.session.api_path)
            stream = stream.copy()
            stream["t"] = stream["Time"].dt.total_seconds()
            self._stream = stream

    # -- derived race facts ------------------------------------------------

    def winner(self):
        res = self.results
        if res is None or res.empty:
            return None
        return str(res.sort_values("Position").iloc[0]["Abbreviation"])

    def classified_order(self):
        res = self.results
        if res is None or res.empty:
            return []
        return [str(r["Abbreviation"]) for _, r in res.sort_values("Position").iterrows()]

    def status_by_code(self):
        res = self.results
        if res is None or res.empty:
            return {}
        return {str(r["Abbreviation"]): str(r.get("Status", "")) for _, r in res.iterrows()}

    def retired_lap_by_code(self):
        """Last completed lap for cars whose status is exactly `Retired`."""
        out = {}
        for code, status in self.status_by_code().items():
            if status != RETIRED_STATUS:
                continue
            dl = self.laps[self.laps["Driver"] == code]["LapNumber"].dropna()
            if len(dl):
                out[code] = int(dl.max())
        return out

    def order_by_lap(self):
        """{lap -> {position -> code}} from the archive's own per-lap Position."""
        out = {}
        for lap in range(1, self.total_laps + 1):
            sub = self.laps[(self.laps["LapNumber"] == lap) & self.laps["Position"].notna()]
            out[lap] = {int(r["Position"]): str(r["Driver"]) for _, r in sub.iterrows()}
        return out

    def lap_end_times(self):
        """{lap -> session-time seconds at which that lap was completed}."""
        out = {}
        for lap in range(1, self.total_laps + 1):
            t = self.laps[self.laps["LapNumber"] == lap]["Time"].dropna()
            if len(t):
                out[lap] = float(t.min().total_seconds())
        return out

    def track_status_frame(self):
        ts = self.session.track_status
        if ts is None or len(ts) == 0:
            return None
        return pd.DataFrame({
            "t": ts["Time"].dt.total_seconds().values,
            "status": [int(str(s)[0]) if str(s) else 1 for s in ts["Status"].values],
        }).sort_values("t")

    def track_status_at(self, t, frame=None):
        f = self.track_status_frame() if frame is None else frame
        if f is None or f.empty:
            return 1
        prior = f[f["t"] <= t]
        return int(prior.iloc[-1]["status"]) if len(prior) else 1


def _circuit_id(session):
    """Jolpica-style circuit id, so `lib/circuits.multiplier_for` can be reused
    rather than a second name map being invented here."""
    name = str(getattr(session.event, "Location", "") or "").lower()
    ev = str(getattr(session.event, "EventName", "") or "").lower()
    table = [
        ("melbourne", "albert_park"), ("shanghai", "shanghai"), ("suzuka", "suzuka"),
        ("miami", "miami"), ("montr", "villeneuve"), ("monaco", "monaco"),
        ("monte", "monaco"), ("barcelona", "catalunya"), ("catalunya", "catalunya"),
        ("spielberg", "red_bull_ring"), ("silverstone", "silverstone"),
        ("spa", "spa"), ("budapest", "hungaroring"), ("hungarian", "hungaroring"),
        ("zandvoort", "zandvoort"), ("monza", "monza"), ("baku", "baku"),
        ("singapore", "marina_bay"), ("austin", "americas"), ("jeddah", "jeddah"),
        ("interlagos", "interlagos"), ("sao paulo", "interlagos"),
    ]
    for needle, cid in table:
        if needle in name or needle in ev:
            return cid
    return name.replace(" ", "_") or "unknown"


def _asof(frame, grid, cols, tol):
    left = pd.DataFrame({"t": grid})
    right = frame.sort_values("t")
    return pd.merge_asof(left, right, on="t", direction="backward",
                         tolerance=tol, allow_exact_matches=True)[cols]


def _pit_windows(archive):
    out = {}
    for code in archive.codes:
        sub = archive.laps[archive.laps["Driver"] == code]
        w = []
        for _, r in sub.iterrows():
            if pd.notna(r["PitInTime"]):
                a = r["PitInTime"].total_seconds()
                b = (r["PitOutTime"].total_seconds()
                     if pd.notna(r["PitOutTime"]) else a + 60.0)
                w.append((a, b))
        out[code] = w
    return out


def lap_ticks(archive, inject=None):
    """One tick per lap boundary, from lap-level data alone.

    Every tick reports `degraded = {position, cardata}`, which is the honest
    `03` sec8 description of what a lap-level source carries: the channels
    produced no sample, so their fields are None -- never a carried-over value
    and never 0 (`03` sec12.10).
    """
    order_by_lap = archive.order_by_lap()
    lap_end = archive.lap_end_times()
    retired_lap = archive.retired_lap_by_code()
    ts_frame = archive.track_status_frame()
    pit_laps = {}
    for _, r in archive.laps.iterrows():
        if pd.notna(r["PitInTime"]) and pd.notna(r["LapNumber"]):
            pit_laps.setdefault(str(r["Driver"]), set()).add(int(r["LapNumber"]))

    latched = set()
    for lap in range(1, archive.total_laps + 1):
        t = lap_end.get(lap)
        if t is None:
            continue
        order = order_by_lap.get(lap, {})
        cars = {}
        for code in archive.codes:
            rl = retired_lap.get(code)
            if rl is not None and lap > rl:
                latched.add(code)
            pos = next((p for p, d in order.items() if d == code), None)
            cars[code] = CarState(
                code=code, racing_number=archive.number_of.get(code, ""), position=pos,
                gap_leader=None, gap_ahead=None, catching_ahead=None,
                in_pit=lap in pit_laps.get(code, ()),
                pit_out=False, retired=code in latched, stopped=False,
                # A retired car's lap count freezes at the lap it stopped on;
                # a running car has completed the current one.
                laps=min(lap, rl) if rl is not None else lap,
            )
        yield _make_tick(archive, t, lap, cars,
                         archive.track_status_at(t, ts_frame),
                         frozenset({"position", "cardata"}), inject)


def full_ticks(archive, hz=REPLAY_HZ, inject=None):
    """1 Hz ticks carrying telemetry, positions and intervals.

    Every channel is joined onto the sample grid with `merge_asof(direction=
    "backward")`, never interpolated. That is the same rule
    `lib/overtake_features` is built on and for the same reason: an
    interpolation reads the sample *after* the decision time, which is lookahead
    wearing the costume of a smoothing step (09 sec11 assertion 4).
    """
    require(archive.telemetry,
            "full_ticks needs an archive loaded with telemetry=True")
    stream = archive._stream
    lap_end = archive.lap_end_times()
    retired_lap = archive.retired_lap_by_code()
    ts_frame = archive.track_status_frame()
    windows = _pit_windows(archive)

    t0 = min(lap_end.values()) - 120.0
    t1 = max(lap_end.values()) + 1.0
    grid = np.arange(t0, t1, 1.0 / hz)

    lap_of_t = np.zeros(len(grid), dtype=int)
    ends = sorted(lap_end.items())
    for lap, t in ends:
        lap_of_t[grid >= t] = lap

    per_driver = {}
    for code in archive.codes:
        num = archive.number_of[code]
        sub = stream[stream["Driver"] == num]
        chan = {}
        pos_f = sub[["t", "Position"]].dropna()
        chan["position"] = (_asof(pos_f, grid, ["Position"], 3600.0)["Position"].values
                            if len(pos_f) else np.full(len(grid), np.nan))
        for src, key in (("IntervalToPositionAhead", "gap_ahead"), ("GapToLeader", "gap_leader")):
            f = sub[["t", src]].dropna()
            chan[key] = (_asof(f, grid, [src], 3600.0)[src].values
                         if len(f) else np.array([None] * len(grid), dtype=object))
        car = archive.session.car_data.get(num)
        if car is not None and not car.empty:
            cf = pd.DataFrame({
                "t": car["SessionTime"].dt.total_seconds().values,
                "speed": car["Speed"].values.astype(float),
                "throttle": car["Throttle"].values.astype(float),
                "brake": car["Brake"].values.astype(float),
            })
            got = _asof(cf, grid, ["speed", "throttle", "brake"], 5.0)
            chan["speed"], chan["throttle"], chan["brake"] = (
                got["speed"].values, got["throttle"].values, got["brake"].values)
        else:
            nan = np.full(len(grid), np.nan)
            chan["speed"] = chan["throttle"] = chan["brake"] = nan
        pdta = archive.session.pos_data.get(num)
        if pdta is not None and not pdta.empty:
            pf = pd.DataFrame({
                "t": pdta["SessionTime"].dt.total_seconds().values,
                "x": pdta["X"].values.astype(float), "y": pdta["Y"].values.astype(float),
            })
            got = _asof(pf, grid, ["x", "y"], 5.0)
            chan["x"], chan["y"] = got["x"].values, got["y"].values
        else:
            chan["x"] = chan["y"] = np.full(len(grid), np.nan)
        # laps COMPLETED at t, from the lap's own end time -- never the lap the
        # car is currently on, which would count a lap before it was finished.
        sub_laps = archive.laps[archive.laps["Driver"] == code]
        done = np.zeros(len(grid), dtype=float)
        for _, r in sub_laps.iterrows():
            if pd.notna(r["Time"]) and pd.notna(r["LapNumber"]):
                done[grid >= r["Time"].total_seconds()] = float(r["LapNumber"])
        chan["laps"] = done
        per_driver[code] = chan

    latched = set()
    ts_codes = np.array([archive.track_status_at(float(t), ts_frame) for t in grid])
    for i, t in enumerate(grid):
        t = float(t)
        lap = int(lap_of_t[i]) + 1
        cars = {}
        for code in archive.codes:
            ch = per_driver[code]
            rl = retired_lap.get(code)
            if rl is not None and lap > rl + 1:
                latched.add(code)
            pos = ch["position"][i]
            cars[code] = CarState(
                code=code, racing_number=archive.number_of[code],
                position=int(pos) if pos == pos else None,
                gap_leader=_str_or_none(ch["gap_leader"][i]),
                gap_ahead=_str_or_none(ch["gap_ahead"][i]),
                catching_ahead=None,
                in_pit=any(a <= t <= b for a, b in windows.get(code, ())),
                pit_out=False, retired=code in latched, stopped=False,
                laps=int(ch["laps"][i]),
                x=_int_or_none(ch["x"][i]), y=_int_or_none(ch["y"][i]),
                speed=_int_or_none(ch["speed"][i]),
                throttle=_int_or_none(ch["throttle"][i]),
                brake=_int_or_none(ch["brake"][i]),
            )
        yield _make_tick(archive, t, min(lap, archive.total_laps), cars,
                         int(ts_codes[i]), frozenset(), inject)


def _str_or_none(v):
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    s = str(v).strip()
    return s or None


def _int_or_none(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    return int(v)


def _make_tick(archive, t, lap, cars, track_status, degraded, inject):
    stale = False
    gap = False
    if inject:
        degraded, stale, gap = inject(t, lap, degraded)
        if degraded:
            cars = {c: _blank(s, degraded) for c, s in cars.items()}
    return Tick(
        session_key="%d/R%d" % (archive.season, archive.round),
        t_feed="%.3f" % t, t_local=float(t),
        # A replay has no wall clock of its own. Session-time seconds are
        # emitted in the field's shape so the record is complete, and any
        # feed-versus-market comparison off a replay is meaningless anyway --
        # 09 sec9.1's "replay does not validate the live plumbing".
        t_wall="replay+%.3f" % t,
        track_status=int(track_status), lap_current=int(lap),
        lap_total=int(archive.total_laps), degraded=frozenset(degraded),
        gap_after_reconnect=bool(gap), stale=bool(stale),
        cars=MappingProxyType(cars))


def _blank(car, degraded):
    """`03` sec8 / sec12.10: in a degraded window the channel produced no
    sample, so its fields are None -- never a carried-over previous value and
    never 0."""
    kw = dict(car.__dict__)
    if "cardata" in degraded:
        for k in ("speed", "throttle", "brake", "gear", "rpm", "aero_raw"):
            kw[k] = None
    if "position" in degraded:
        kw["x"] = kw["y"] = None
    return CarState(**kw)


def degrade_every(n_ticks, modes=("cardata",), stale_every=None, gap_every=None):
    """A deterministic `inject` for exercising the 09 sec8.1 reliability paths
    offline, rather than meeting a degraded tick for the first time in a race."""
    def inject(t, lap, degraded):
        i = int(t)
        d = set(degraded)
        if n_ticks and i % n_ticks == 0:
            d |= set(modes)
        stale = bool(stale_every and i % stale_every == 0)
        gap = bool(gap_every and i % gap_every == 0)
        return frozenset(d), stale, gap
    return inject
