"""The tick-state contract. 03 sec7 / sec8 / sec9.4.

B0's product is a *tick*: one immutable snapshot of per-car state at a point in
session time. Not a prediction, not an event, not a database (03 sec7.5). The
tick is the only interface between the live feed and any future overtake model;
the model reads ticks and nothing else.

This module owns the retained-state merge (03 sec7.4), the terminal-state latch
(sec7.4), the degraded-mode reporting (sec8), and the gear-corruption tripwire
(sec12.2). It does NOT own the socket (lib/signalr.py), the reconnect lifecycle
or the staleness clock (lib/livetiming_client.py) -- those need the wall clock
and the connection, which the assembler deliberately does not touch.

03 sec12 assertion 7 is structural, not a runtime check: there is no
interpolation / extrapolation / forward-fill code path anywhere in this file.
A gap in the sample stream leaves the field at its last real value or None;
nothing here invents a value to bridge it.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Optional

from lib.invariants import require
from lib import livetiming_parse as P

# 03 sec9.4 staleness guard: a tick whose newest input is older than this is
# marked stale and no consumer may predict from it. Lives here as the constant;
# the client applies it because it holds the monotonic clock.
STALENESS_LIMIT_S = 5.0

# 03 sec12.2: a *sustained* run of out-of-range gears is drift, not lossiness.
# The tripwire is >1% of gear samples in any 60s window -- three orders of
# magnitude above the 0.0087% measured on a real race.
GEAR_WINDOW_S = 60.0
GEAR_TRIP_RATIO = 0.01
GEAR_TRIP_MIN_SAMPLES = 200


@dataclass(frozen=True)
class CarState:
    """Per-car state at one tick. 03 sec7.1.

    Keyed on the FIA three-letter code (01 sec8.2's locked canonical key);
    racing_number is retained for provenance. Gap fields are the feed's
    verbatim strings, never parsed floats (sec7.1).
    """
    code: str
    racing_number: str
    position: Optional[int] = None
    gap_leader: Optional[str] = None
    gap_ahead: Optional[str] = None
    catching_ahead: Optional[bool] = None
    in_pit: bool = False
    pit_out: bool = False
    retired: bool = False
    stopped: bool = False
    laps: Optional[int] = None
    x: Optional[int] = None
    y: Optional[int] = None
    speed: Optional[int] = None
    throttle: Optional[int] = None
    brake: Optional[int] = None
    gear: Optional[int] = None
    rpm: Optional[int] = None
    aero_raw: Optional[int] = None  # opaque, never decoded (03 sec7.3)


@dataclass(frozen=True)
class Tick:
    """One immutable per-car snapshot. 03 sec7.1.

    `t_local` is a monotonic receipt time -- correct for ordering and the sec9.4
    staleness guard, useless for B1 because it has no epoch. `t_wall` is the
    UTC wall-clock anchor B1 needs to line the feed up against a broadcast event
    a human observed on a clock. Both are set by the client; the assembler fills
    neither. (Spec fix, 2026-08-27: 03 sec7.1 originally specced t_local alone
    and sec13 item 8 leaned on it for B1 -- a monotonic clock cannot be
    subtracted from "lights-out showed on the TV at 14:03:07Z".)
    """
    session_key: Optional[str]
    t_feed: str
    t_local: float
    t_wall: str
    track_status: int
    lap_current: Optional[int]
    lap_total: Optional[int]
    degraded: frozenset
    gap_after_reconnect: bool
    stale: bool
    cars: MappingProxyType

    def car(self, code):
        return self.cars.get(code)


class TickAssembler:
    """Folds raw channel payloads into retained state and emits ticks.

    Lifecycle per session:
        a = TickAssembler()
        a.apply_snapshot({channel: payload, ...})   # 03 sec7.4 wholesale replace
        a.ingest("CarData.z", payload, t_feed)      # sec7.2 delta merge
        ...
        tick = a.emit(t_feed, t_local, t_wall, gap_after_reconnect, stale)
    """

    # Channels this assembler folds into state. Anything else that arrives is
    # logged once and ignored by the caller (03 sec10, "F1 may add channels").
    TICK_CHANNELS = frozenset({
        "SessionInfo", "DriverList", "TimingData", "TimingDataF1",
        "CarData.z", "Position.z", "TrackStatus", "LapCount",
    })

    def __init__(self):
        self._timing = {}       # racing_number -> merged TimingData fields
        self._cardata = {}      # racing_number -> {rpm, speed, gear, throttle, brake, aero_raw}
        self._position = {}     # racing_number -> {x, y}
        self._drivers = {}      # racing_number -> FIA code
        self._track_status = 1
        self._lap_current = None
        self._lap_total = None
        self._session_key = None
        self._latched = {}      # racing_number -> {"retired": bool, "stopped": bool}
        self._seen_since_emit = set()   # subset of {"position", "cardata"}
        self._gear_window = deque()     # (monotonic_ts, dropped: bool) per gear sample

    @property
    def session_key(self):
        return self._session_key

    # -- ingest -------------------------------------------------------------

    def apply_snapshot(self, channels):
        """03 sec7.4 / sec12.11: the subscribe `type: 3` completion is the
        complete truth at that moment. REPLACE state, never merge -- merging
        lets stale cars from a previous session survive into this one.
        """
        self._timing = {}
        self._cardata = {}
        self._position = {}
        self._drivers = {}
        self._latched = {}
        for channel, payload in channels.items():
            self._route(channel, payload, snapshot=True)

    def ingest(self, channel, payload, t_feed=None):
        """Fold one live `feed` invocation into retained state (03 sec7.2)."""
        self._route(channel, payload, snapshot=False)

    def _route(self, channel, payload, snapshot):
        if channel == "SessionInfo":
            path = payload.get("Path") if isinstance(payload, dict) else None
            if path:
                self._session_key = str(path)
        elif channel == "DriverList":
            self._drivers.update(P.parse_driver_list(payload))
        elif channel in ("TimingData", "TimingDataF1"):
            if snapshot:
                self._timing = {}
            P.merge_timing_delta(self._timing, payload)
            # 03 sec7.4 / sec12.6: latch Retired / Stopped at INGEST, not at
            # emit. The feed drops these flags between messages; if the latch
            # only read state at emit time it would miss a retire that was
            # reported and then un-reported before the next tick.
            for num, car in self._timing.items():
                latch = self._latched.setdefault(num, {"retired": False, "stopped": False})
                for key in ("retired", "stopped"):
                    if car.get(key):
                        latch[key] = True
        elif channel == "CarData.z":
            decoded = P.decompress_z(payload)
            cars, gear_drops = P.parse_cardata(decoded)
            for num, rec in cars.items():
                self._cardata.setdefault(num, {v: None for v in P.CAR_CHANNELS.values()})
                self._cardata[num].update({k: v for k, v in rec.items() if v is not None})
            self._record_gear_samples(cars, gear_drops)
            self._seen_since_emit.add("cardata")
        elif channel == "Position.z":
            decoded = P.decompress_z(payload)
            for num, rec in P.parse_position(decoded).items():
                self._position.setdefault(num, {"x": None, "y": None})
                self._position[num].update({k: v for k, v in rec.items() if v is not None})
            self._seen_since_emit.add("position")
        elif channel == "TrackStatus":
            self._track_status = P.parse_track_status(payload)
        elif channel == "LapCount":
            self._lap_current, self._lap_total = P.parse_lap_count(payload)
        # SessionData / SessionStatus / Heartbeat etc. are the client's concern
        # (liveness, session lifecycle), not the tick's.

    def _record_gear_samples(self, cars, gear_drops):
        now = time.monotonic()
        dropped = set(gear_drops)
        for num in cars:
            self._gear_window.append((now, num in dropped))
        cutoff = now - GEAR_WINDOW_S
        while self._gear_window and self._gear_window[0][0] < cutoff:
            self._gear_window.popleft()
        total = len(self._gear_window)
        if total >= GEAR_TRIP_MIN_SAMPLES:
            bad = sum(1 for _, d in self._gear_window if d)
            require(bad / total <= GEAR_TRIP_RATIO,
                    "03 sec12.2: %d/%d gear samples out of range in the last %ds "
                    "(%.2f%% > %.0f%%) -- this is a moved index map, not feed lossiness"
                    % (bad, total, GEAR_WINDOW_S, 100 * bad / total, 100 * GEAR_TRIP_RATIO))

    # -- emit -------------------------------------------------------------

    def emit(self, t_feed, t_local, t_wall, gap_after_reconnect=False, stale=False):
        """Assemble one immutable Tick from current retained state. 03 sec7.1.

        Joins cardata / position / timing onto the driver list by racing number
        (03 sec12.1: a number with no code halts). Latches Retired / Stopped
        (sec7.4). Reports degraded modes from what arrived since the last emit
        (sec8)."""
        degraded = frozenset({"position", "cardata"} - self._seen_since_emit)
        self._seen_since_emit = set()

        nums = set(self._timing) | set(self._cardata) | set(self._position)
        cars = {}
        for num in sorted(nums):
            code = self._drivers.get(num)
            require(code is not None,
                    "03 sec12.1: racing number %r has telemetry but no DriverList "
                    "entry -- the canonical-key join has broken" % num)
            cars[code] = self._build_car(num, code, degraded)

        return Tick(
            session_key=self._session_key,
            t_feed=str(t_feed) if t_feed is not None else "",
            t_local=float(t_local),
            t_wall=str(t_wall),
            track_status=self._track_status,
            lap_current=self._lap_current,
            lap_total=self._lap_total,
            degraded=degraded,
            gap_after_reconnect=bool(gap_after_reconnect),
            stale=bool(stale),
            cars=MappingProxyType(dict(cars)),
        )

    def _build_car(self, num, code, degraded):
        t = self._timing.get(num, {})
        # 03 sec8 + sec12.10: in a degraded window the channel produced no
        # sample, so its fields are None -- never a carried-over previous value
        # and never 0. The staleness guard (sec9.4) is the separate mechanism
        # for data that is present but old.
        c = {} if "cardata" in degraded else self._cardata.get(num, {})
        p = {} if "position" in degraded else self._position.get(num, {})
        latch = self._latched.setdefault(num, {"retired": False, "stopped": False})

        return CarState(
            code=code,
            racing_number=str(num),
            position=t.get("position"),
            gap_leader=t.get("gap_leader"),
            gap_ahead=t.get("gap_ahead"),
            catching_ahead=t.get("catching_ahead"),
            in_pit=bool(t.get("in_pit", False)),
            pit_out=bool(t.get("pit_out", False)),
            retired=latch["retired"],
            stopped=latch["stopped"],
            laps=t.get("laps"),
            # 03 sec8: missing is never zero. An absent Position.z channel
            # leaves x / y as None, never 0 and never a carried-over value
            # (sec12.10). The .update() in _route only writes non-None, so a
            # key that never arrived is simply absent here.
            x=p.get("x"),
            y=p.get("y"),
            speed=c.get("speed"),
            throttle=c.get("throttle"),
            brake=c.get("brake"),
            gear=c.get("gear"),
            rpm=c.get("rpm"),
            aero_raw=c.get("aero_raw"),
        )
