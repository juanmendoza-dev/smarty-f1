#!/usr/bin/env python3
"""Tests for the B0 live-timing client. 03 sec6-13.

Every fixture here is a hand-written synthetic payload built to match 03 sec7.2,
NEVER a captured frame -- 03 sec11.2 forbids any F1 live-timing data in this
repo, sample or fixture included, and the repo is public.

Run under the 3.12 venv (requests + websockets):
    .venv312/bin/python test_livetiming.py
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import zlib

from lib import signalr
from lib import livetiming_parse as P
from lib.livetiming_tick import TickAssembler
from lib.livetiming_client import LiveTimingClient
from lib.invariants import InvariantError

FAILURES = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + detail) if not cond else ""))
    if not cond:
        FAILURES.append(name)


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def make_z(obj):
    """base64 + raw DEFLATE (no zlib header) -- the exact shape 03 sec7.2
    says CarData.z / Position.z arrive in."""
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = co.compress(json.dumps(obj).encode()) + co.flush()
    return base64.b64encode(raw).decode()


def car_payload(cars):
    return {"Entries": [{"Utc": "2026-09-04T11:31:00Z",
                         "Cars": {n: {"Channels": ch} for n, ch in cars.items()}}]}


# --------------------------------------------------------------- framing
def test_split_frames():
    print("split_frames (03 sec6.1 -- one WS frame can carry many messages)")
    msgs, rem = signalr.split_frames('{"a":1}\x1e{"b":2}\x1e{"c":3')
    check("splits complete messages", msgs == ['{"a":1}', '{"b":2}'])
    check("holds the trailing partial", rem == '{"c":3')
    msgs2, rem2 = signalr.split_frames(rem + '}\x1e')
    check("partial completes on next frame", msgs2 == ['{"c":3}'] and rem2 == "")
    check("ping is discarded by parse path", signalr.parse_message('{"type":6}')["type"] == 6
          and signalr.parse_message("  ") is None)


# --------------------------------------------------------------- decompress
def test_decompress_z():
    print("decompress_z (03 sec7.2 -- raw DEFLATE, not zlib)")
    obj = {"Entries": [{"Cars": {"44": {"Channels": {"2": 305}}}}]}
    check("round-trips a synthetic .z payload", P.decompress_z(make_z(obj)) == obj)
    check("bad base64 -> drift halt", raises(lambda: P.decompress_z("not base64!!"), P.SchemaDrift))
    check("valid b64 but not deflate -> drift halt",
          raises(lambda: P.decompress_z(base64.b64encode(b"xxxx").decode()), P.SchemaDrift))


# --------------------------------------------------------------- cardata
def test_parse_cardata():
    print("parse_cardata (03 sec7.3 channel map, sec12.2 domains)")
    cars, drops = P.parse_cardata(car_payload({
        "44": {"0": 11000, "2": 305, "3": 7, "4": 88, "5": 0, "45": 0},
    }))
    check("channel map applied", cars["44"] == {
        "rpm": 11000, "speed": 305, "gear": 7, "throttle": 88, "brake": 0, "aero_raw": 0})
    check("no gear drops on clean data", drops == [])

    absent, _ = P.parse_cardata(car_payload({"1": {"2": 200}}))
    check("absent channel -> None, never 0 (sec10)", absent["1"]["throttle"] is None)

    hot, _ = P.parse_cardata(car_payload({"1": {"4": 104}}))
    check("throttle 104 is legal (sec12.2: [0,110], measured max 104)", hot["1"]["throttle"] == 104)
    check("throttle 200 -> halt (moved index map)",
          raises(lambda: P.parse_cardata(car_payload({"1": {"4": 200}})), InvariantError))

    g, gd = P.parse_cardata(car_payload({"1": {"3": 19}}))
    check("gear 19 -> None + drop marker, never halt (sec12.2)",
          g["1"]["gear"] is None and gd == ["1"])
    check("speed 9000 -> halt",
          raises(lambda: P.parse_cardata(car_payload({"1": {"2": 9000}})), InvariantError))


# --------------------------------------------------------------- position
def test_parse_position():
    print("parse_position (03 sec7.2)")
    payload = {"Position": [{"Timestamp": "t", "Entries": {
        "44": {"X": 100, "Y": -200, "Z": 0}, "1": {"X": 5, "Y": 6, "Z": 731}}}]}
    out = P.parse_position(payload)
    check("X/Y parsed, Z ignored", out["44"] == {"x": 100, "y": -200} and "z" not in out["1"])
    check("missing Position key -> drift", raises(lambda: P.parse_position({}), P.SchemaDrift))


# --------------------------------------------------------------- timing delta
def test_merge_timing_delta():
    print("merge_timing_delta (03 sec7.2 -- delta protocol, verbatim gaps)")
    state = {}
    P.merge_timing_delta(state, {"Lines": {"44": {
        "Position": 3, "GapToLeader": "+12.345",
        "IntervalToPositionAhead": {"Value": "+1.201", "Catching": True},
        "InPit": False, "NumberOfLaps": 10}}})
    check("first delta populates", state["44"]["position"] == 3
          and state["44"]["gap_ahead"] == "+1.201" and state["44"]["catching_ahead"] is True)
    P.merge_timing_delta(state, {"Lines": {"44": {"Position": 2}}})
    check("second delta merges, doesn't replace (sec7.2)",
          state["44"]["position"] == 2 and state["44"]["gap_ahead"] == "+1.201")
    check("gap kept verbatim as string, not parsed", isinstance(state["44"]["gap_leader"], str))
    P.merge_timing_delta(state, {"Lines": {"44": {"Retired": True}}})
    check("TimingDataF1 Retired folds in", state["44"]["retired"] is True)


def test_track_status():
    print("parse_track_status (03 sec12.3)")
    check("code 4 (SC) ok", P.parse_track_status({"Status": "4"}) == 4)
    check("code 3 -> halt", raises(lambda: P.parse_track_status({"Status": "3"}), InvariantError))


# --------------------------------------------------------------- assembler
def snapshot():
    return {
        "SessionInfo": {"Path": "2026/2026-09-04_Italian_GP/2026-09-04_Practice_1"},
        "DriverList": {"44": {"Tla": "HAM"}, "1": {"Tla": "VER"}, "_kf": True},
        "TimingData": {"Lines": {
            "44": {"Position": 1, "IntervalToPositionAhead": {"Value": ""}},
            "1": {"Position": 2, "IntervalToPositionAhead": {"Value": "+0.802"}}}},
        "TrackStatus": {"Status": "1"},
        "LapCount": {"CurrentLap": 5, "TotalLaps": 53},
        "CarData.z": make_z(car_payload({"44": {"2": 300}, "1": {"2": 301}})),
        "Position.z": make_z({"Position": [{"Entries": {
            "44": {"X": 10, "Y": 20, "Z": 0}, "1": {"X": 11, "Y": 21, "Z": 0}}}]}),
    }


def test_assembler_tick():
    print("TickAssembler.emit (03 sec7.1)")
    a = TickAssembler()
    a.apply_snapshot(snapshot())
    t = a.emit(t_feed="2026-09-04T11:31:00Z", t_local=1000.0, t_wall="2026-09-04T11:31:02Z")
    check("keyed on FIA code (01 sec8.2)", set(t.cars) == {"HAM", "VER"})
    check("session key from SessionInfo.Path", "Italian_GP" in t.session_key)
    check("timing + cardata + position joined", t.car("VER").position == 2
          and t.car("VER").speed == 301 and t.car("VER").x == 11)
    check("gap carried verbatim", t.car("VER").gap_ahead == "+0.802")
    check("nothing degraded when all channels arrived", t.degraded == frozenset())
    check("t_wall anchor present (B1 needs an epoch, sec7.1 fix)", t.t_wall.endswith("Z"))


def test_assembler_degraded():
    print("degraded modes (03 sec8 -- missing is never zero)")
    a = TickAssembler()
    a.apply_snapshot(snapshot())
    a.emit(t_feed="", t_local=1.0, t_wall="")            # first window: everything seen
    a.ingest("TimingData", {"Lines": {"44": {"Position": 1}}})  # only timing this window
    t = a.emit(t_feed="", t_local=2.0, t_wall="")
    check("position + cardata both degraded when neither arrived", t.degraded == frozenset({"position", "cardata"}))
    check("x/y are None in position-degraded, not 0 or stale (sec12.10)",
          t.car("HAM").x is None and t.car("VER").x is None)


def test_assembler_latch_and_join():
    print("latch + canonical-key join (03 sec7.4 / sec12.1 / sec12.6)")
    a = TickAssembler()
    a.apply_snapshot(snapshot())
    a.ingest("TimingDataF1", {"Lines": {"44": {"Retired": True}}})
    a.ingest("TimingDataF1", {"Lines": {"44": {"Retired": False}}})  # feed drops the flag
    t = a.emit(t_feed="", t_local=1.0, t_wall="")
    check("Retired latched true->true even after the feed says false", t.car("HAM").retired is True)

    a.ingest("CarData.z", make_z(car_payload({"99": {"2": 100}})))  # no DriverList entry
    check("racing number with no code -> halt (sec12.1)",
          raises(lambda: a.emit(t_feed="", t_local=2.0, t_wall=""), InvariantError))


def test_gear_tripwire():
    print("gear corruption tripwire (03 sec12.2 -- sustained run halts, one-off doesn't)")
    a = TickAssembler()
    a.apply_snapshot(snapshot())
    for _ in range(300):
        a.ingest("CarData.z", make_z(car_payload({"44": {"3": 5}})))  # clean
    a.ingest("CarData.z", make_z(car_payload({"44": {"3": 19}})))     # 1 bad in ~301
    check("one bad gear in a clean window does not halt", True)
    a2 = TickAssembler()
    a2.apply_snapshot(snapshot())
    check("a sustained run of bad gears halts",
          raises(lambda: [a2.ingest("CarData.z", make_z(car_payload({"44": {"3": 19}})))
                          for _ in range(300)], InvariantError))


# --------------------------------------------------------------- replay
def test_replay_roundtrip():
    print("run_replay (03 sec6.5.3 -- offline, no network)")
    lines = []
    for i in range(12):
        for ch, data in [
            ("SessionInfo", {"Path": "2026/x/2026_Practice_1"}) if i == 0 else ("Heartbeat", {}),
            ("TimingData", {"Lines": {"44": {"Position": 1}, "1": {"Position": 2}}}),
            ("DriverList", {"44": {"Tla": "HAM"}, "1": {"Tla": "VER"}}),
            ("CarData.z", make_z(car_payload({"44": {"2": 300 + i}, "1": {"2": 280}}))),
        ]:
            lines.append(json.dumps({"recv_wall": "2026-09-04T11:%02d:00Z" % i,
                                     "recv_mono": float(i), "msg": {
                                         "type": 1, "target": "feed",
                                         "arguments": [ch, data, ""]}}))
    with tempfile.TemporaryDirectory() as d:
        raw = os.path.join(d, "in.jsonl")
        open(raw, "w").write("\n".join(lines))
        c = LiveTimingClient(capture_root=d, tick_interval=2.0)
        c.run_replay(raw)
        ticks = [json.loads(l) for l in open(os.path.join(d, "ticks", "2026_x_2026_Practice_1.jsonl"))]
    check("replay produced ticks", len(ticks) >= 3)
    check("ticks carry joined per-car state", ticks[-1]["cars"]["HAM"]["speed"] is not None)
    check("t_wall marked (replay)", ticks[-1]["t_wall"] == "(replay)")


# --------------------------------------------------------------- repo guards
def test_verify_script():
    print("livetiming_verify -- 03 sec13 acceptance check against a synthetic capture")
    import livetiming_verify as V
    with tempfile.TemporaryDirectory() as d:
        rawdir = os.path.join(d, "raw"); os.makedirs(rawdir)
        lines = [json.dumps({"recv_wall": "2026-09-04T11:30:00Z", "recv_mono": 0.0,
                             "msg": {"type": 3,
                                     "subscribe_snapshot_channels": list(
                                         __import__("lib.livetiming_client", fromlist=["CHANNELS"]).CHANNELS),
                                     "missing": []}})]
        t = 1.0
        for i in range(60):
            for ch, data, dt in [
                ("DriverList", {"44": {"Tla": "HAM"}, "1": {"Tla": "VER"}}, 0.0),
                ("Heartbeat", {}, 0.0),
                ("CarData.z", make_z(car_payload({"44": {"0": 11000, "2": 300, "3": 5, "4": 80, "5": 0, "45": 0},
                                                  "1": {"0": 10500, "2": 280, "3": 4, "4": 60, "5": 0, "45": 0}})), 0.24),
                ("Position.z", make_z({"Position": [{"Entries": {
                    "44": {"X": i, "Y": 2 * i, "Z": 0}, "1": {"X": i + 1, "Y": 2 * i, "Z": 0}}}]}), 0.24),
            ]:
                lines.append(json.dumps({"recv_wall": "x", "recv_mono": t, "msg": {
                    "type": 1, "target": "feed", "arguments": [ch, data, ""]}}))
                t += dt
            t += 0.5
        open(os.path.join(rawdir, "seg00.jsonl"), "w").write("\n".join(lines))
        ticks = os.path.join(d, "t.jsonl")
        open(ticks, "w").write("\n".join(json.dumps({"t_local": float(i), "t_wall": "2026-09-04T11:%02d:00Z" % i})
                                         for i in range(30)))
        log = os.path.join(d, "l.log")
        open(log, "w").write("AWSALBCORS cookie present\n"
                             "segment closed: 400 WS frames, 3 carried >1 message (sec13 item 1b)\n")
        rc = V.verify(rawdir, ticks, log)
    check("verify passes on a clean synthetic capture", rc == 0, "rc=%s" % rc)


def test_no_live_data_in_git():
    print("03 sec12.13 -- nothing under data/live/ is tracked by git")
    out = subprocess.run(["git", "ls-files", "data/live"], capture_output=True, text=True)
    check("git ls-files data/live is empty", out.stdout.strip() == "", out.stdout.strip())


def test_no_interpolation_code_path():
    print("03 sec12.7 -- structural: no interpolation/forward-fill anywhere in the parse path")
    src = ""
    for fn in ("lib/signalr.py", "lib/livetiming_parse.py", "lib/livetiming_tick.py",
               "lib/livetiming_client.py"):
        src += open(fn).read().lower()
    # tolerate the word inside a comment that explains the ban
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    bad = [w for w in ("ffill", "forward_fill", "forwardfill", ".interpolate(", "np.interp")
           if w in code]
    check("no forbidden interpolation calls", not bad, str(bad))


def test_headers_are_constant():
    print("03 sec12.14 -- request headers are constants, not attempt-dependent")
    check("HEADERS has exactly the sec6.2 keys",
          set(signalr.HEADERS) == {"User-Agent", "Accept-Encoding"})
    check("no header-mutation code in signalr.py",
          "HEADERS[" not in open("lib/signalr.py").read())


def main():
    for fn in (test_split_frames, test_decompress_z, test_parse_cardata, test_parse_position,
               test_merge_timing_delta, test_track_status, test_assembler_tick,
               test_assembler_degraded, test_assembler_latch_and_join, test_gear_tripwire,
               test_replay_roundtrip, test_verify_script, test_no_live_data_in_git,
               test_no_interpolation_code_path, test_headers_are_constant):
        fn()
    print()
    if FAILURES:
        print("FAILED: %d" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all tests passed")


if __name__ == "__main__":
    main()
