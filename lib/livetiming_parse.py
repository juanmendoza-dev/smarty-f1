"""Payload decoders for the F1 live-timing feed. 03 sec7.2 / sec7.3 / sec10.

Every function here turns one raw channel payload into plain Python. Nothing
here connects to anything: the transport is lib/signalr.py, the tick assembly
is lib/livetiming_tick.py. The split exists so the decode path -- the one thing
03 sec6.1 flags as carried over from the legacy protocol on expectation rather
than evidence -- can be exercised against hand-built synthetic frames with no
network, per 03 sec11.2.

03 sec10's rule, inherited unchanged: a required field missing, or a value
outside its declared domain, raises through lib.invariants.require and halts.
It does not get a default and does not produce a tick with a hole that looks
real. sec10 draws the line between drift (halts) and degradation (an entire
channel absent -- handled, sec8); this module only ever sees a channel that
did arrive, so everything wrong here is drift.
"""

import base64
import json
import zlib

from lib.invariants import require

# 03 sec7.3 -- the CarData channel index map. Keys are positions in each car's
# "Channels" object. 45 was DRS through 2025 and is measured constant-zero in
# 2026 (sec7.3); it is carried opaque as a drift tripwire, never decoded.
CAR_CHANNELS = {0: "rpm", 2: "speed", 3: "gear", 4: "throttle", 5: "brake", 45: "aero_raw"}

# 03 sec12.2 -- REVISED against the measured 2026 Dutch GP (944,196 samples).
# Do not tighten these without re-measuring against a real session; sec12.2's
# whole point is that the first draft's [0,100] throttle bound would have
# halted the client within seconds of the first green flag.
#
#   throttle really does exceed 100     -- 10.3% of samples, observed max 104
#   -> domain is [0, 110], violation still halts (a moved index map overshoots
#      much further than 104)
#
#   gear above 8 is sparse feed corruption, NOT drift -- 82 samples in a race,
#   ~1 in 11,500, scattered one-offs with no pattern
#   -> drop the sample (field = None), count it, never halt on a single one.
#      A *sustained* run is different: >1% of samples in a 60s window halts,
#      and that lives in the tick assembler which sees the time axis.
CAR_DOMAINS_HALT = {"speed": (0, 400), "rpm": (0, 20000), "throttle": (0, 110)}
GEAR_RANGE = (0, 8)

# 03 sec7.1 / sec12.3.
TRACK_STATUS_CODES = {1, 2, 4, 5, 6, 7}


class SchemaDrift(Exception):
    """A present channel arrived in a shape 03 sec7.2 does not describe.

    Distinct from lib.invariants.InvariantError so the client's disconnect
    classifier (03 sec9.3) can tell a schema halt apart from a domain halt --
    both stop the client, but only one means "re-check the spec against the
    feed before writing more code" (sec13's closing line).
    """


def _drift(msg):
    raise SchemaDrift("03 sec10: " + msg)


def decompress_z(payload):
    """base64 -> raw DEFLATE (no zlib header) -> parsed JSON. 03 sec7.2.

    The decompress call is `zlib.decompress(data, -zlib.MAX_WBITS)`, not plain
    `zlib.decompress` -- the payload has no zlib header. A failure at any step
    is a framing change (sec10's "This is a framing change, not noise") and
    halts.
    """
    require(isinstance(payload, str),
            "sec7.2: .z payload should be a base64 string, got %s" % type(payload).__name__)
    try:
        compressed = base64.b64decode(payload, validate=True)
    except (ValueError, base64.binascii.Error) as e:
        _drift("a .z payload failed base64 decode: %s" % e)
    try:
        blob = zlib.decompress(compressed, -zlib.MAX_WBITS)
    except zlib.error as e:
        _drift("a .z payload failed raw-DEFLATE decode: %s" % e)
    try:
        return json.loads(blob)
    except json.JSONDecodeError as e:
        _drift("a decompressed .z payload was not JSON: %s" % e)


def parse_cardata(payload):
    """CarData.z -> {racing_number: {field: value_or_None}}. 03 sec7.2 / sec7.3.

    Input is the decompressed dict
        {"Entries": [{"Utc": ..., "Cars": {"<num>": {"Channels": {"0": ...}}}}]}
    Multiple entries can arrive in one payload; the last value per car wins,
    which is what a sample stream folded newest-last means.

    Returns per-car dicts with keys from CAR_CHANNELS. A channel index that is
    absent yields None for that field (sec10: "Field is `None` for that
    sample"). A halt-domain value out of range raises (a moved index map). A
    gear out of range yields None and a ("gear_dropped", racing_number) marker
    in the second return value, for the assembler to count (sec12.2).
    """
    if not isinstance(payload, dict) or "Entries" not in payload:
        _drift("CarData.z decompressed to something without an 'Entries' key")
    entries = payload["Entries"]
    if not isinstance(entries, list):
        _drift("CarData.z 'Entries' is not a list")

    out = {}
    gear_drops = []
    for entry in entries:
        cars = entry.get("Cars") if isinstance(entry, dict) else None
        if not isinstance(cars, dict):
            _drift("a CarData.z entry has no 'Cars' mapping")
        for num, car in cars.items():
            channels = car.get("Channels") if isinstance(car, dict) else None
            if not isinstance(channels, dict):
                _drift("CarData.z car %r has no 'Channels' mapping" % num)
            rec = out.setdefault(str(num), {v: None for v in CAR_CHANNELS.values()})
            for idx, name in CAR_CHANNELS.items():
                # The feed keys channels as strings; tolerate ints too.
                raw = channels.get(str(idx), channels.get(idx))
                if raw is None:
                    continue
                try:
                    val = int(raw)
                except (TypeError, ValueError):
                    _drift("CarData.z channel %d for car %r is not an int: %r" % (idx, num, raw))
                if name == "gear":
                    if not (GEAR_RANGE[0] <= val <= GEAR_RANGE[1]):
                        rec["gear"] = None
                        gear_drops.append(str(num))
                        continue
                elif name in CAR_DOMAINS_HALT:
                    lo, hi = CAR_DOMAINS_HALT[name]
                    require(lo <= val <= hi,
                            "03 sec12.2: CarData %s=%d outside [%d,%d] for car %r -- "
                            "the channel index map has moved" % (name, val, lo, hi, num))
                rec[name] = val
    return out, gear_drops


def parse_position(payload):
    """Position.z -> {racing_number: {"x": int|None, "y": int|None}}. 03 sec7.2.

    Input is the decompressed dict
        {"Position": [{"Timestamp": ..., "Entries": {"<num>": {"X":..,"Y":..,"Z":..}}}]}
    Z is altitude, broadcast as 0, ignored (sec7.2). Last sample per car wins.
    """
    if not isinstance(payload, dict) or "Position" not in payload:
        _drift("Position.z decompressed to something without a 'Position' key")
    frames = payload["Position"]
    if not isinstance(frames, list):
        _drift("Position.z 'Position' is not a list")

    out = {}
    for frame in frames:
        entries = frame.get("Entries") if isinstance(frame, dict) else None
        if not isinstance(entries, dict):
            _drift("a Position.z frame has no 'Entries' mapping")
        for num, e in entries.items():
            if not isinstance(e, dict):
                _drift("Position.z entry for car %r is not an object" % num)
            rec = out.setdefault(str(num), {"x": None, "y": None})
            for src, dst in (("X", "x"), ("Y", "y")):
                if src in e and e[src] is not None:
                    try:
                        rec[dst] = int(e[src])
                    except (TypeError, ValueError):
                        _drift("Position.z %s for car %r is not an int: %r" % (src, num, e[src]))
    return out


def merge_timing_delta(state, payload):
    """Fold a TimingData / TimingDataF1 delta into retained per-car state.

    03 sec7.2: TimingData is a delta protocol -- only changed fields are sent --
    so this MERGES into `state` (mutated in place and returned), never treats an
    update as a complete record. sec7.4: on a subscribe snapshot the caller
    replaces `state` wholesale instead of calling this.

    `state` is {racing_number: {field: value}}. The fields pulled through are
    the ones 03 sec7.1's CarState needs:
        Position, GapToLeader, IntervalToPositionAhead (+ .Catching),
        InPit, PitOut, NumberOfLaps, Retired, Stopped
    Retired / Stopped are latched by the assembler (sec7.4), not here.
    """
    if not isinstance(payload, dict) or "Lines" not in payload:
        _drift("TimingData delta has no 'Lines' key")
    lines = payload["Lines"]
    if not isinstance(lines, dict):
        _drift("TimingData 'Lines' is not a mapping")

    for num, line in lines.items():
        if not isinstance(line, dict):
            # A line can arrive as a bare value only when the feed is deleting
            # an entry; nothing in sec7 consumes that, so skip rather than halt.
            continue
        car = state.setdefault(str(num), {})
        if "Position" in line and line["Position"] not in (None, ""):
            try:
                car["position"] = int(line["Position"])
            except (TypeError, ValueError):
                _drift("TimingData Position for car %r is not an int: %r" % (num, line["Position"]))
        if "GapToLeader" in line:
            car["gap_leader"] = _verbatim(line["GapToLeader"])
        itv = line.get("IntervalToPositionAhead")
        if isinstance(itv, dict):
            if "Value" in itv:
                car["gap_ahead"] = _verbatim(itv["Value"])
            if "Catching" in itv:
                car["catching_ahead"] = bool(itv["Catching"])
        elif itv is not None:
            car["gap_ahead"] = _verbatim(itv)
        if "InPit" in line:
            car["in_pit"] = bool(line["InPit"])
        if "PitOut" in line:
            car["pit_out"] = bool(line["PitOut"])
        if "NumberOfLaps" in line and line["NumberOfLaps"] not in (None, ""):
            try:
                car["laps"] = int(line["NumberOfLaps"])
            except (TypeError, ValueError):
                _drift("TimingData NumberOfLaps for car %r is not an int: %r"
                       % (num, line["NumberOfLaps"]))
        # TimingDataF1 extras.
        for src, dst in (("Retired", "retired"), ("Stopped", "stopped")):
            if src in line:
                car[dst] = bool(line[src])
    return state


def _verbatim(v):
    """03 sec7.1: gap fields are carried as the feed's verbatim string, never
    parsed to a float. The format is context-dependent ("+1.234", "1L", "",
    lap-down markers) and interpreting it is the model's decision, made later,
    with the raw string still available to check.
    """
    return v if isinstance(v, str) else ("" if v is None else str(v))


def parse_track_status(payload):
    """TrackStatus -> int code. 03 sec12.3: code must be in {1,2,4,5,6,7}."""
    if not isinstance(payload, dict) or "Status" not in payload:
        _drift("TrackStatus has no 'Status' key")
    try:
        code = int(payload["Status"])
    except (TypeError, ValueError):
        _drift("TrackStatus 'Status' is not an int: %r" % payload["Status"])
    require(code in TRACK_STATUS_CODES,
            "03 sec12.3: TrackStatus code %d not in {1,2,4,5,6,7}" % code)
    return code


def parse_lap_count(payload):
    """LapCount -> (current, total), either may be None."""
    if not isinstance(payload, dict):
        _drift("LapCount payload is not an object")
    cur = payload.get("CurrentLap")
    tot = payload.get("TotalLaps")
    return (_opt_int(cur, "CurrentLap"), _opt_int(tot, "TotalLaps"))


def parse_driver_list(payload):
    """DriverList -> {racing_number: FIA three-letter code}. 03 sec6.3 / sec12.1.

    This is the join to 01 sec8.2's canonical key. A CarData/Position racing
    number with no entry here halts in the assembler (sec12.1), not here.
    """
    if not isinstance(payload, dict):
        _drift("DriverList payload is not an object")
    out = {}
    for num, rec in payload.items():
        if num in ("_kf",) or not isinstance(rec, dict):
            continue
        code = rec.get("Tla") or rec.get("tla")
        if code:
            out[str(num)] = str(code)
    return out


def _opt_int(v, field):
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        _drift("%s is not an int: %r" % (field, v))
